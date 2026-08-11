"""Delivery read-model aggregation (contract v0.1 §2-§3).

One delivery = one ExecutionPlan lifecycle (``delivery_id = execution_plan_id``).
A project's latest un-materialized plan snapshot forms a virtual draft delivery
with ``delivery_id: null``. The service only reads through module contracts and
composition-root adapters; unimplemented contract fields return ``null``.
"""

import json
from dataclasses import asdict
from uuid import UUID

from repomesh.modules.delivery.contracts import (
    ChangeSetView,
    RecoveryActionKind,
    RecoveryActionStatus,
    RepositoryDeliveryStatus,
)
from repomesh.modules.task_orchestration.contracts import TaskStatus, TaskView

from .mappings import (
    DeliveryPhase,
    GateDisplay,
    derive_phase,
    gate_display,
    has_active_recovery,
    task_display_status,
)
from .sources import (
    AgentNameSource,
    ArchiveSource,
    ChangeSetSource,
    ExecutionPlanSource,
    PlanSnapshotData,
    PlanSnapshotSource,
    RepositorySource,
    SpecificationSource,
    TaskSource,
    TopologySource,
    ValidationSource,
)

REWORK_TASK_TITLE = "Repair failed delivery candidate"
"""The canonical title CIReworkTaskCreator assigns; identifies rework chains."""

MERGE_GATE_MOOT_STATUSES = frozenset(
    {
        RepositoryDeliveryStatus.MERGE_REQUESTED,
        RepositoryDeliveryStatus.MERGED,
        RepositoryDeliveryStatus.COMPENSATION_PENDING,
        RepositoryDeliveryStatus.COMPENSATED,
    }
)
"""Contract 889464e: the pre-merge gate question is moot here, so merge_gate is
null instead of a factually wrong 'blocked' answer."""

_TERMINAL_ACTION_STATUSES = frozenset(
    {RecoveryActionStatus.SUCCEEDED, RecoveryActionStatus.SKIPPED}
)


class DeliveryReadModelService:
    def __init__(
        self,
        *,
        plans: ExecutionPlanSource,
        snapshots: PlanSnapshotSource,
        tasks: TaskSource,
        change_sets: ChangeSetSource,
        archives: ArchiveSource,
        validations: ValidationSource,
        specifications: SpecificationSource,
        repositories: RepositorySource,
        agents: AgentNameSource,
        topology: TopologySource,
    ) -> None:
        self._plans = plans
        self._snapshots = snapshots
        self._tasks = tasks
        self._change_sets = change_sets
        self._archives = archives
        self._validations = validations
        self._specifications = specifications
        self._repositories = repositories
        self._agents = agents
        self._topology = topology

    # ------------------------------------------------------------------ list

    async def list_deliveries(self, *, include_archived: bool = False) -> dict:
        plans = await self._plans.list_all()
        plans_by_project: dict[UUID, list] = {}
        for plan in plans:
            plans_by_project.setdefault(plan.project_id, []).append(plan)
        project_ids = set(plans_by_project) | set(await self._snapshots.project_ids())

        projects = []
        for project_id in sorted(project_ids, key=str):
            snapshots = await self._snapshots.for_project(project_id)
            snapshot_by_plan = {
                snapshot.execution_plan_id: snapshot
                for snapshot in snapshots
                if snapshot.execution_plan_id is not None
            }
            deliveries = []
            for plan in plans_by_project.get(project_id, ()):
                summary = await self._delivery_summary(
                    plan, snapshot_by_plan.get(plan.id)
                )
                if summary["phase"] == DeliveryPhase.ARCHIVED.value and not include_archived:
                    continue
                deliveries.append(summary)
            draft = self._draft_summary(snapshots)
            if draft is not None:
                deliveries.insert(0, draft)
            if not deliveries:
                continue
            title_source = snapshots[0].requirement_text if snapshots else None
            projects.append(
                {
                    "project_id": project_id,
                    "project_key": None,  # no project registry yet; frontend degrades
                    "title": _title(title_source, project_id),
                    "deliveries": deliveries,
                }
            )
        return {"projects": projects, "next_cursor": None}

    async def _delivery_summary(self, plan, snapshot: PlanSnapshotData | None) -> dict:
        archived = await self._archives.get(plan.id) is not None
        change_set = await self._change_sets.for_delivery(plan.id)
        validation = await self._find_validation(plan.project_id, plan.id, change_set)
        phase = derive_phase(
            archived=archived,
            plan_status=plan.status,
            change_set=change_set,
            has_validation_snapshot=validation is not None,
            has_plan_snapshot=snapshot is not None,
            materialized=True,
        )
        pending = 0
        if change_set is not None:
            pending = len(await self._pending_decisions(change_set))
        title_source = snapshot.requirement_text if snapshot is not None else None
        updated_at = None
        if change_set is not None:
            updated_at = change_set.updated_at
        elif snapshot is not None:
            updated_at = snapshot.created_at
        return {
            "delivery_id": plan.id,
            "title": _title(title_source, plan.project_id),
            "phase": phase.value,
            "phase_note": self._phase_note(phase, plan, change_set),
            "pending_decision_count": pending,
            "updated_at": updated_at,
        }

    def _draft_summary(self, snapshots: tuple[PlanSnapshotData, ...]) -> dict | None:
        if not snapshots:
            return None
        latest = snapshots[0]
        if latest.execution_plan_id is not None:
            return None
        return {
            "delivery_id": None,
            "title": _title(latest.requirement_text, latest.project_id),
            "phase": DeliveryPhase.PLAN.value,
            "phase_note": f"计划 v{latest.plan_version} 待物化",
            "pending_decision_count": 0,
            "updated_at": latest.created_at,
        }

    @staticmethod
    def _phase_note(phase: DeliveryPhase, plan, change_set: ChangeSetView | None) -> str:
        if phase is DeliveryPhase.EXECUTE:
            return f"第 {plan.current_batch_index + 1}/{len(plan.batches)} 批执行中"
        if change_set is not None:
            merged = sum(
                1
                for item in change_set.repositories
                if item.status is RepositoryDeliveryStatus.MERGED
            )
            total = len(change_set.repositories)
            if phase is DeliveryPhase.DELIVERED:
                return f"{total} 仓已全部合并"
            if phase is DeliveryPhase.RELEASE:
                return f"{merged}/{total} 已合并"
            if phase is DeliveryPhase.FAILED:
                return f"{change_set.status.value}"
        if phase is DeliveryPhase.FAILED:
            return "执行失败"
        if phase is DeliveryPhase.VALIDATE:
            return "等待交付证据"
        if phase is DeliveryPhase.ARCHIVED:
            return "已归档"
        return ""

    async def _pending_decisions(self, change_set: ChangeSetView) -> list[dict]:
        """§4.3 derivations reused for the list's pending_decision_count."""

        items: list[dict] = []
        ready_heads = {
            (decision.repository_id, decision.head_sha)
            for decision in change_set.governance_decisions
            if decision.decision.value == "ready"
        }
        active_recovery = has_active_recovery(change_set)
        for repository in change_set.repositories:
            awaits_approval = False
            if repository.status not in MERGE_GATE_MOOT_STATUSES:
                gate = await self._change_sets.merge_gate(
                    change_set.id, repository.repository_id
                )
                awaits_approval = (
                    gate.allowed
                    or repository.status is RepositoryDeliveryStatus.READY_TO_MERGE
                )
            missing_ready = (repository.repository_id, repository.commit_sha) not in ready_heads
            under_recovery = active_recovery and any(
                action.repository_id == repository.repository_id
                and action.status not in _TERMINAL_ACTION_STATUSES
                for action in change_set.recovery_plans[-1].actions
            )
            if awaits_approval and missing_ready:
                items.append({"kind": "approve", "repository_id": repository.repository_id})
            elif under_recovery:
                items.append({"kind": "watch", "repository_id": repository.repository_id})
        return items

    # ----------------------------------------------------------------- detail

    async def get_delivery(self, delivery_id: UUID) -> dict | None:
        plan = await self._plans.get(delivery_id)
        if plan is None:
            return None
        project_id = plan.project_id
        snapshots = await self._snapshots.for_project(project_id)
        snapshot = next(
            (item for item in snapshots if item.execution_plan_id == delivery_id), None
        )
        change_set = await self._change_sets.for_delivery(delivery_id)
        validation = await self._find_validation(project_id, delivery_id, change_set)
        contract = await self._specifications.engineering_contract(project_id)
        catalog = {item.id: item for item in await self._repositories.list()}
        tasks = await self._tasks.list_by_project(project_id)

        plan_repository_ids = {
            planned.repository_id for batch in plan.batches for planned in batch
        }
        leader_task_ids = {
            planned.leader_task_id
            for batch in plan.batches
            for planned in batch
            if planned.leader_task_id is not None
        }
        worker_tasks = tuple(
            task for task in tasks if task.parent_task_id in leader_task_ids
        )
        task_views = await self._task_views(worker_tasks, snapshot, change_set, catalog)
        merge_order = (
            [
                item.repository_id
                for item in sorted(change_set.repositories, key=lambda r: r.merge_order)
            ]
            if change_set is not None
            else []
        )
        return {
            "delivery_id": delivery_id,
            "project": {
                "project_id": project_id,
                "project_key": None,
                "title": _title(
                    snapshot.requirement_text if snapshot is not None else None, project_id
                ),
                "requirement_text": (
                    snapshot.requirement_text if snapshot is not None else None
                ),
                "created_at": snapshots[-1].created_at if snapshots else None,
            },
            "contract": (
                {
                    "specification_id": contract.specification_id,
                    "version": contract.version,
                    "status": contract.status,
                    "goal": contract.goal,
                    "acceptance": list(contract.acceptance),
                    "constraints": list(contract.constraints),
                    "allowed_paths": list(contract.allowed_paths),
                    "forbidden_paths": list(contract.forbidden_paths),
                    "tests": list(contract.tests),
                    "non_goals": None,
                    "release_rules": None,
                }
                if contract is not None
                else None
            ),
            "repositories": [
                {
                    "repository_id": repository_id,
                    "name": (
                        catalog[repository_id].name
                        if repository_id in catalog
                        else str(repository_id)[:8]
                    ),
                    "evidence": None,
                }
                for repository_id in sorted(plan_repository_ids, key=str)
            ],
            "plan": {
                "plan_version": snapshot.plan_version if snapshot is not None else None,
                "status": plan.status.value,
                "current_batch_index": plan.current_batch_index,
                "execution_batches": (
                    [list(batch) for batch in snapshot.execution_batches]
                    if snapshot is not None
                    else [
                        [
                            catalog[planned.repository_id].name
                            if planned.repository_id in catalog
                            else str(planned.repository_id)[:8]
                            for planned in batch
                        ]
                        for batch in plan.batches
                    ]
                ),
                "merge_order": merge_order,
            },
            "tasks": task_views,
            "change_set": self._change_set_block(change_set)
            if change_set is not None
            else None,
            "validation_snapshot": (
                {
                    "id": validation.id,
                    "status": validation.status.value,
                    "candidate_heads": {
                        str(key): value for key, value in validation.candidate_heads.items()
                    },
                    "environment_hash": validation.environment_hash,
                    "expires_at": validation.expires_at,
                }
                if validation is not None
                else None
            ),
            "diffs": _diffs(worker_tasks),
            "cost": None,
            "matrix_room_id": await self._topology.matrix_room_id(project_id),
            "trace_id": None,
        }

    async def _task_views(
        self,
        worker_tasks: tuple[TaskView, ...],
        snapshot: PlanSnapshotData | None,
        change_set: ChangeSetView | None,
        catalog: dict,
    ) -> list[dict]:
        rework_by_key: dict[tuple[UUID, UUID | None], list[TaskView]] = {}
        for task in worker_tasks:
            if task.title == REWORK_TASK_TITLE:
                rework_by_key.setdefault(
                    (task.repository_id, task.parent_task_id), []
                ).append(task)

        name_to_task: dict[str, UUID] = {}
        for task in worker_tasks:
            if task.title != REWORK_TASK_TITLE and task.repository_id in catalog:
                name_to_task[catalog[task.repository_id].name] = task.id
        dag_dependencies: dict[str, tuple[str, ...]] = {}
        if snapshot is not None:
            for node in snapshot.task_dag:
                repository = str(node.get("repository", ""))
                dag_dependencies[repository] = tuple(node.get("depends_on") or ())

        views: list[dict] = []
        for task in worker_tasks:
            chain = rework_by_key.get((task.repository_id, task.parent_task_id), [])
            is_rework = task.title == REWORK_TASK_TITLE
            active_rework = any(
                item.status
                in {TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED}
                for item in chain
            )
            repairing = is_rework or (
                active_rework and task.status is TaskStatus.IN_PROGRESS
            )
            display = task_display_status(task.status, has_active_rework=repairing)
            if display is None:
                continue
            repository_name = (
                catalog[task.repository_id].name if task.repository_id in catalog else None
            )
            depends_on = [
                name_to_task[name]
                for name in dag_dependencies.get(repository_name or "", ())
                if name in name_to_task
            ]
            repair_timeline = [
                {"at": None, "what": f"返工任务 {item.status.value}"}
                for item in chain
                if item.id != task.id
            ]
            escalated = False
            if change_set is not None:
                for recovery in change_set.recovery_plans:
                    repair_timeline.extend(
                        {
                            "at": recovery.created_at,
                            "what": f"{action.kind.value}: {action.detail}".strip(": "),
                        }
                        for action in recovery.actions
                        if action.repository_id == task.repository_id
                    )
                    escalated = escalated or any(
                        action.kind is RecoveryActionKind.MANUAL_INTERVENTION
                        and action.status not in _TERMINAL_ACTION_STATUSES
                        and action.repository_id in {task.repository_id, None}
                        for action in recovery.actions
                    )
            views.append(
                {
                    "task_id": task.id,
                    "task_key": None,
                    "repository_id": task.repository_id,
                    "title": task.title,
                    "backend_status": task.status.value,
                    "display_status": display.value,
                    "agent": await self._agents.name(task.assignee_agent_id),
                    # §5.2: 1 + length of the rework chain on the same (repository,
                    # parent) key; a rework row counts itself and its predecessors.
                    "attempt": (2 + chain.index(task)) if is_rework else 1 + len(chain),
                    "depends_on": depends_on,
                    "result_summary": task.result_summary,
                    "repair_timeline": repair_timeline,
                    "escalated_to_human": escalated,
                }
            )
        return views

    def _change_set_block(self, change_set: ChangeSetView) -> dict:
        return {
            "change_set_id": change_set.id,
            "status": change_set.status.value,
            "merge_cursor": change_set.merge_cursor,
            "repositories": [
                {
                    "repository_id": item.repository_id,
                    "task_id": item.task_id,
                    "status": item.status.value,
                    "gate_display": gate_display(item.status).value,
                    "pull_request_url": item.pull_request_url,
                    "pull_request_number": item.pull_request_number,
                    "head_sha": item.commit_sha,
                    "base_sha": item.base_sha,
                    "branch_name": item.branch_name,
                    "depends_on": list(item.depends_on),
                    "merge_order": item.merge_order,
                    "ci_checks": [
                        {
                            "check_name": check.check_name,
                            "passed": check.passed,
                            "summary": check.summary,
                        }
                        for check in item.ci_checks
                    ],
                    "required_checks": list(item.required_checks),
                    "required_approvals": item.required_approvals,
                    "reviews": [
                        {
                            "reviewer": review.reviewer,
                            "state": review.state.value,
                            "summary": review.summary,
                        }
                        for review in item.reviews
                    ],
                    "merge_gate": None,  # filled by the router (needs an async call)
                    "merge_sha": item.merge_sha,
                }
                for item in sorted(change_set.repositories, key=lambda r: r.merge_order)
            ],
            "governance_decisions": [asdict(item) for item in change_set.governance_decisions],
            "recovery_plans": [
                {
                    "trigger": plan.trigger.value,
                    "reason": plan.reason,
                    "actions": [
                        {
                            "kind": action.kind.value,
                            "status": action.status.value,
                            "repository_id": action.repository_id,
                            "detail": action.detail,
                        }
                        for action in plan.actions
                    ],
                }
                for plan in change_set.recovery_plans
            ],
        }

    async def attach_merge_gates(self, payload: dict) -> dict:
        """Fill each repository's merge_gate; separate to keep the block builder sync."""

        change_set = payload.get("change_set")
        if not change_set:
            return payload
        for repository in change_set["repositories"]:
            if RepositoryDeliveryStatus(repository["status"]) in MERGE_GATE_MOOT_STATUSES:
                continue
            gate = await self._change_sets.merge_gate(
                change_set["change_set_id"], repository["repository_id"]
            )
            repository["merge_gate"] = {
                "allowed": gate.allowed,
                "reasons": list(gate.reasons),
            }
        return payload

    async def _find_validation(
        self, project_id: UUID, delivery_id: UUID, change_set: ChangeSetView | None
    ):
        candidates = await self._validations.for_project(project_id)
        if change_set is not None and change_set.validation_snapshot_id is not None:
            for item in candidates:
                if item.id == change_set.validation_snapshot_id:
                    return item
        for item in reversed(candidates):
            if item.environment.get("execution_plan") == str(delivery_id):
                return item
        return None


def _title(requirement_text: str | None, project_id: UUID) -> str:
    text = (requirement_text or "").strip()
    if text:
        return text if len(text) <= 80 else text[:77] + "..."
    return f"Project {str(project_id)[:8]}"


def _diffs(worker_tasks: tuple[TaskView, ...]) -> list[dict]:
    """runner.completed evidence written back into task result summaries."""

    diffs: list[dict] = []
    for task in worker_tasks:
        if task.status is not TaskStatus.SUCCEEDED or not task.result_summary:
            continue
        try:
            evidence = json.loads(task.result_summary)
        except json.JSONDecodeError:
            continue
        if not isinstance(evidence, dict) or not evidence.get("commitSha"):
            continue
        diffs.append(
            {
                "repository_id": task.repository_id,
                "run_id": evidence.get("runId"),
                "commit_sha": evidence.get("commitSha"),
                "changed_files": list(evidence.get("changedFiles") or ()),
                "diffstat": None,
            }
        )
    return diffs


__all__ = [
    "DeliveryReadModelService",
    "DeliveryPhase",
    "GateDisplay",
    "REWORK_TASK_TITLE",
]
