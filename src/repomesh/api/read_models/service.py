"""Delivery read-model aggregation (contract v0.1 §2-§3).

One delivery = one ExecutionPlan lifecycle (``delivery_id = execution_plan_id``).
A project's latest un-materialized plan snapshot forms a virtual draft delivery
with ``delivery_id: null``. The service only reads through module contracts and
composition-root adapters; unimplemented contract fields return ``null``.
"""

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from uuid import UUID

from repomesh.modules.delivery.contracts import (
    MERGE_GATE_GOVERNANCE_MISSING_REASON,
    ChangeSetView,
    RecoveryActionKind,
    RecoveryActionStatus,
    RepositoryDeliveryStatus,
)
from repomesh.modules.project.contracts import ProjectAgentTopologyView
from repomesh.modules.task_orchestration.contracts import (
    ExecutionPlanStatus,
    ExecutionPlanView,
    TaskStatus,
    TaskView,
)

from .mappings import (
    TERMINAL_CHANGE_SET_STATUSES,
    DeliveryPhase,
    GateDisplay,
    derive_issue_state,
    derive_phase,
    gate_display,
    select_issue_phase,
    task_display_status,
)
from .sources import (
    AgentNameSource,
    ArchiveSource,
    ChangeSetSource,
    ExecutionPlanSource,
    MessageSource,
    ObservationSource,
    PlanSnapshotData,
    PlanSnapshotSource,
    RepositorySource,
    RunnerEventSource,
    SpecificationContractData,
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

_ACTIVE_TASK_STATUSES = frozenset(
    {TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED}
)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
"""Sort floor for rounds whose only timestamp source is missing entirely."""


@dataclass(frozen=True, slots=True)
class _RoundFacts:
    """One ExecutionPlan lifecycle: v0.1 calls it a delivery, v0.2 a round."""

    plan: ExecutionPlanView
    phase: DeliveryPhase
    plan_version: int | None
    created_at: datetime | None
    updated_at: datetime | None
    change_set: ChangeSetView | None
    pending_decision_count: int


@dataclass(frozen=True, slots=True)
class _IssueBundle:
    """Everything both /issues and /issues/{id} derive from, read once."""

    summary: dict
    rounds: tuple[_RoundFacts, ...]
    snapshots: tuple[PlanSnapshotData, ...]
    topology: ProjectAgentTopologyView | None
    repository_ids: tuple[UUID, ...]


def _round_order(round_facts: _RoundFacts) -> tuple:
    """Chronological order; rounds with no timestamp at all sort first."""

    at = round_facts.updated_at or round_facts.created_at
    return (at is not None, at or _EPOCH, round_facts.plan_version or 0)


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
        runner_events: RunnerEventSource,
        messages: MessageSource,
        observations: ObservationSource,
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
        self._runner_events = runner_events
        self._messages = messages
        self._observations = observations

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

    async def _round_facts(
        self, plan: ExecutionPlanView, snapshot: PlanSnapshotData | None
    ) -> _RoundFacts:
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
            pending = len(await self._decision_items(plan, change_set))
        created_at = snapshot.created_at if snapshot is not None else None
        return _RoundFacts(
            plan=plan,
            phase=phase,
            plan_version=snapshot.plan_version if snapshot is not None else None,
            created_at=created_at,
            updated_at=(
                change_set.updated_at if change_set is not None else created_at
            ),
            change_set=change_set,
            pending_decision_count=pending,
        )

    async def _delivery_summary(self, plan, snapshot: PlanSnapshotData | None) -> dict:
        facts = await self._round_facts(plan, snapshot)
        title_source = snapshot.requirement_text if snapshot is not None else None
        return {
            "delivery_id": plan.id,
            "title": _title(title_source, plan.project_id),
            "phase": facts.phase.value,
            "phase_note": self._phase_note(facts.phase, plan, facts.change_set),
            "pending_decision_count": facts.pending_decision_count,
            "updated_at": facts.updated_at,
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

    async def _decision_items(self, plan, change_set: ChangeSetView) -> list[dict]:
        """Contract §4.3: purely derived approve/watch items, one per repository.

        approve (5a60148 wording): the merge gate reports no blocking reason
        other than the missing head-bound READY governance decision — matched
        against the delivery contracts constant, never a magic string.
        watch: the repository has a non-terminal recovery plan or rework task.
        """

        catalog = {item.id: item for item in await self._repositories.list()}
        leader_task_ids = {
            planned.leader_task_id
            for batch in plan.batches
            for planned in batch
            if planned.leader_task_id is not None
        }
        tasks = await self._tasks.list_by_project(plan.project_id)
        active_rework_repos = {
            task.repository_id
            for task in tasks
            if task.parent_task_id in leader_task_ids
            and task.title == REWORK_TASK_TITLE
            and task.status in _ACTIVE_TASK_STATUSES
        }
        revision_at: dict[UUID, datetime] = {}
        for revision in change_set.candidate_revisions:
            seen = revision_at.get(revision.repository_id)
            if seen is None or revision.created_at > seen:
                revision_at[revision.repository_id] = revision.created_at

        items: list[dict] = []
        for repository in change_set.repositories:
            name = (
                catalog[repository.repository_id].name
                if repository.repository_id in catalog
                else str(repository.repository_id)
            )
            awaiting_governance = False
            if repository.status not in MERGE_GATE_MOOT_STATUSES:
                gate = await self._change_sets.merge_gate(
                    change_set.id, repository.repository_id
                )
                awaiting_governance = bool(gate.reasons) and all(
                    reason == MERGE_GATE_GOVERNANCE_MISSING_REASON
                    for reason in gate.reasons
                )
            recovery_open = any(
                action.repository_id == repository.repository_id
                and action.status not in _TERMINAL_ACTION_STATUSES
                for recovery in change_set.recovery_plans
                for action in recovery.actions
            )
            if awaiting_governance:
                items.append(
                    {
                        "id": f"approve:{repository.repository_id}:{repository.commit_sha}",
                        "kind": "approve",
                        "title": f"待治理放行：{name}",
                        "body": (
                            f"候选 {repository.commit_sha[:12]} 已通过其余全部门禁，"
                            "仅差 head-bound READY 治理决策。"
                        ),
                        "repository_id": repository.repository_id,
                        "head_sha": repository.commit_sha,
                        "created_at": revision_at.get(
                            repository.repository_id, change_set.updated_at
                        ),
                        "actions": ["approve_merge", "view_evidence"],
                    }
                )
            elif recovery_open or repository.repository_id in active_rework_repos:
                items.append(
                    {
                        "id": f"watch:{repository.repository_id}",
                        "kind": "watch",
                        "title": f"修复观察：{name}",
                        "body": "该仓库存在未终态的恢复计划或返工任务。",
                        "repository_id": repository.repository_id,
                        "head_sha": repository.commit_sha,
                        "created_at": change_set.updated_at,
                        "actions": ["view_evidence"],
                    }
                )
        return items

    # ---------------------------------------------------------------- issues

    async def list_issues(
        self, *, state: str = "open", organization_id: UUID | None = None
    ) -> dict:
        """Contract v0.2 §2: issue-grained listing (issue_id = project_id).

        The issue universe is every project that ever produced an ExecutionPlan
        or a PlanSnapshot — the only persisted evidence an issue exists. Issues
        with neither (§2.1 rule 6) stay unreachable until a project registry
        lands; the rule is implemented so the write endpoint needs no change.
        """

        plans_by_project = await self._plans_by_project()
        project_ids = set(plans_by_project) | set(await self._snapshots.project_ids())

        issues = []
        for project_id in sorted(project_ids, key=str):
            bundle = await self._issue_bundle(
                project_id, plans_by_project.get(project_id, ())
            )
            issue = bundle.summary
            if organization_id is not None and issue["organization_id"] != organization_id:
                continue
            if state != "all" and issue["state"] != state:
                continue
            issues.append(issue)
        issues.sort(key=_issue_recency, reverse=True)
        return {"issues": issues, "next_cursor": None}

    async def get_issue(self, issue_id: UUID) -> dict | None:
        """Contract v0.2 §3: §2's fields plus the round index and chips."""

        plans_by_project = await self._plans_by_project()
        plans = plans_by_project.get(issue_id, ())
        snapshots = await self._snapshots.for_project(issue_id)
        if not plans and not snapshots:
            return None

        bundle = await self._issue_bundle(issue_id, plans)
        topology = bundle.topology
        catalog = {item.id: item for item in await self._repositories.list()}
        team_by_repository = (
            {team.repository_id: team for team in topology.repository_teams}
            if topology is not None
            else {}
        )
        contract = await self._specifications.engineering_contract(issue_id)
        return {
            **bundle.summary,
            "rounds": [
                {
                    "round_id": facts.plan.id,
                    "phase": facts.phase.value,
                    "status": facts.plan.status.value,
                    "plan_version": facts.plan_version,
                    "created_at": facts.created_at,
                    "updated_at": facts.updated_at,
                }
                for facts in bundle.rounds
            ],
            "repositories": [
                {
                    "repository_id": repository_id,
                    "name": (
                        catalog[repository_id].name
                        if repository_id in catalog
                        else str(repository_id)[:8]
                    ),
                    "team_id": (
                        team_by_repository[repository_id].id
                        if repository_id in team_by_repository
                        else None
                    ),
                    # The topology records no per-issue repository role; the
                    # producer/consumer split only exists in a CONTRACT spec.
                    "role_in_issue": None,
                }
                for repository_id in bundle.repository_ids
            ],
            "teams": [
                {
                    "team_id": team.id,
                    "agentteams_team_name": team.agentteams_team_name,
                    "repository_id": team.repository_id,
                    "runtime_status": team.runtime_status.value,
                }
                for team in (topology.repository_teams if topology else ())
            ],
            "contract": _contract_block(contract),
            "human_grants": [
                {
                    "human_principal_id": grant.human_principal_id,
                    "role": grant.role.value,
                    "code_access": grant.code_access.value,
                }
                for grant in (topology.human_grants if topology else ())
            ],
            "required_checkpoints": sorted(
                checkpoint.value
                for checkpoint in (topology.required_checkpoints if topology else ())
            ),
        }

    async def _plans_by_project(self) -> dict[UUID, list[ExecutionPlanView]]:
        plans_by_project: dict[UUID, list[ExecutionPlanView]] = {}
        for plan in await self._plans.list_all():
            plans_by_project.setdefault(plan.project_id, []).append(plan)
        return plans_by_project

    async def _issue_bundle(
        self, project_id: UUID, plans: list[ExecutionPlanView] | tuple
    ) -> _IssueBundle:
        snapshots = await self._snapshots.for_project(project_id)
        snapshot_by_plan = {
            snapshot.execution_plan_id: snapshot
            for snapshot in snapshots
            if snapshot.execution_plan_id is not None
        }
        topology = await self._topology.get_view(project_id)
        rounds = sorted(
            [
                await self._round_facts(plan, snapshot_by_plan.get(plan.id))
                for plan in plans
            ],
            key=_round_order,
        )

        # §0: title, requirement text and creation time all come from the
        # earliest snapshot; `for_project` returns newest plan_version first.
        earliest = snapshots[-1] if snapshots else None
        draft = (
            snapshots[0]
            if snapshots and snapshots[0].execution_plan_id is None
            else None
        )
        active = next(
            (
                facts
                for facts in reversed(rounds)
                if facts.plan.status is ExecutionPlanStatus.IN_PROGRESS
            ),
            None,
        )
        latest = rounds[-1] if rounds else None
        draft_phase = (
            derive_phase(
                archived=False,
                plan_status=None,
                change_set=None,
                has_validation_snapshot=False,
                has_plan_snapshot=True,
                materialized=False,
            )
            if draft is not None
            else None
        )
        phase = select_issue_phase(
            active_round_phase=active.phase if active is not None else None,
            latest_round_phase=latest.phase if latest is not None else None,
            draft_phase=draft_phase,
        )
        chosen = active or latest
        if chosen is not None:
            phase_note = self._phase_note(phase, chosen.plan, chosen.change_set)
        elif draft is not None:
            phase_note = f"计划 v{draft.plan_version} 待物化"
        else:
            phase_note = ""

        state = derive_issue_state(
            operational_status=topology.operational_status if topology else None,
            has_active_round=active is not None,
            has_open_change_set=any(
                facts.change_set is not None
                and facts.change_set.status not in TERMINAL_CHANGE_SET_STATUSES
                for facts in rounds
            ),
            has_draft=draft is not None,
            has_rounds=bool(rounds),
        )

        repository_ids = {
            planned.repository_id
            for facts in rounds
            for batch in facts.plan.batches
            for planned in batch
        }
        if topology is not None:
            repository_ids |= {
                team.repository_id for team in topology.repository_teams
            }

        opened_at = earliest.created_at if earliest is not None else None
        # §2.3: the latest persisted fact across every round and snapshot.
        timestamps = [facts.updated_at for facts in rounds if facts.updated_at] + [
            snapshot.created_at for snapshot in snapshots
        ]
        # Workspace attribution, most authoritative fact first. A draft-only
        # issue has neither round nor topology, so it falls back to the
        # organization of the agent that opened it — without this the workspace
        # filter would silently drop every un-materialized issue.
        organization_id = next(
            (facts.plan.organization_id for facts in rounds), None
        ) or (topology.organization_id if topology else None)
        if organization_id is None and (
            earliest is not None and earliest.created_by_agent_id is not None
        ):
            organization_id = await self._agents.organization_id(
                earliest.created_by_agent_id
            )

        summary = {
            "issue_id": project_id,
            "issue_key": None,  # §0: no project registry, so no human-readable id
            "organization_id": organization_id,
            "title": _title(
                earliest.requirement_text if earliest is not None else None, project_id
            ),
            "requirement_text": (
                earliest.requirement_text if earliest is not None else None
            ),
            "state": state.value,
            "phase": phase.value,
            "phase_note": phase_note,
            "round_count": len(rounds),
            "active_round_id": active.plan.id if active is not None else None,
            "latest_round_id": latest.plan.id if latest is not None else None,
            "pending_decision_count": sum(
                facts.pending_decision_count for facts in rounds
            ),
            "repository_count": len(repository_ids),
            "team_count": len(topology.repository_teams) if topology else 0,
            # Topology-only facts degrade to null rather than a fabricated
            # default when the issue never formed a team.
            "operational_status": (
                topology.operational_status.value if topology else None
            ),
            "execution_mode": topology.execution_mode.value if topology else None,
            "opened_by_agent_id": (
                earliest.created_by_agent_id if earliest is not None else None
            ),
            "opened_at": opened_at,
            "updated_at": max(timestamps) if timestamps else opened_at,
        }
        return _IssueBundle(
            summary=summary,
            rounds=tuple(rounds),
            snapshots=snapshots,
            topology=topology,
            repository_ids=tuple(sorted(repository_ids, key=str)),
        )

    # ------------------------------------------------------------- decisions

    async def list_decisions(self, delivery_id: UUID) -> dict | None:
        """§4.3 decision-folder items; None when the delivery does not exist."""

        plan = await self._plans.get(delivery_id)
        if plan is None:
            return None
        change_set = await self._change_sets.for_delivery(delivery_id)
        if change_set is None:
            return {"items": []}
        return {"items": await self._decision_items(plan, change_set)}

    # ------------------------------------------------------ events & messages

    async def list_events(
        self,
        delivery_id: UUID,
        *,
        kind: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> dict | None:
        """Contract §4.1: one timeline over runner / matrix / gate / plan facts.

        `deny` has no audit store in v0.1 and is never produced. payload_ref is
        the stable source reference and doubles as the deterministic tiebreak,
        so ordering (and therefore the offset cursor) is stable across reads.
        """

        plan = await self._plans.get(delivery_id)
        if plan is None:
            return None
        project_id = plan.project_id
        leader_task_ids = {
            planned.leader_task_id
            for batch in plan.batches
            for planned in batch
            if planned.leader_task_id is not None
        }
        tasks = await self._tasks.list_by_project(project_id)
        worker_task_ids = {
            task.id for task in tasks if task.parent_task_id in leader_task_ids
        }
        change_set = await self._change_sets.for_delivery(delivery_id)

        items: list[dict] = []
        for event in await self._runner_events.for_project(project_id):
            if event.task_id not in worker_task_ids:
                continue
            items.append(
                {
                    "at": event.occurred_at,
                    "kind": "runner",
                    "text": event.event_type,
                    "task_id": event.task_id,
                    "repository_id": event.repository_id,
                    "payload_ref": f"runner-event:{event.event_id}",
                }
            )
        for message in await self._messages.for_project(project_id):
            # Task-bound messages belong to one delivery; unbound ones are
            # project-wide broadcasts and show up on every delivery's timeline.
            if message.task_id is not None and message.task_id not in worker_task_ids:
                continue
            items.append(
                {
                    "at": message.created_at,
                    "kind": "matrix",
                    "text": f"{message.kind.value}: {message.subject}",
                    "task_id": message.task_id,
                    "repository_id": message.repository_id,
                    "payload_ref": f"collaboration-message:{message.id}",
                }
            )
        if change_set is not None:
            for observation in await self._observations.for_change_set(change_set.id):
                items.append(
                    {
                        "at": observation.observed_at,
                        "kind": "gate",
                        "text": observation.event_type,
                        "task_id": None,
                        "repository_id": observation.repository_id,
                        "payload_ref": f"scm-observation:{observation.id}",
                    }
                )
        for snapshot in await self._snapshots.for_project(project_id):
            if snapshot.execution_plan_id != delivery_id:
                continue
            items.append(
                {
                    "at": snapshot.created_at,
                    "kind": "plan",
                    "text": f"计划 v{snapshot.plan_version} 已生成",
                    "task_id": None,
                    "repository_id": None,
                    "payload_ref": f"plan-snapshot:{snapshot.id}",
                }
            )

        if kind is not None:
            items = [item for item in items if item["kind"] == kind]
        items.sort(key=lambda item: (item["at"], item["payload_ref"]))
        page = items[offset : offset + limit]
        next_cursor = str(offset + limit) if offset + limit < len(items) else None
        return {"items": page, "next_cursor": next_cursor}

    async def list_messages(self, delivery_id: UUID) -> dict | None:
        """Contract §4.2: CollaborationMessageView projection for one delivery.

        v0.1 only ingests Leader→Worker traffic, so every item carries
        direction=leader_to_worker until Worker→Leader reports are audited.
        """

        plan = await self._plans.get(delivery_id)
        if plan is None:
            return None
        leader_task_ids = {
            planned.leader_task_id
            for batch in plan.batches
            for planned in batch
            if planned.leader_task_id is not None
        }
        tasks = await self._tasks.list_by_project(plan.project_id)
        worker_task_ids = {
            task.id for task in tasks if task.parent_task_id in leader_task_ids
        }
        items = []
        for message in await self._messages.for_project(plan.project_id):
            if message.task_id is not None and message.task_id not in worker_task_ids:
                continue
            items.append(
                {
                    "id": message.id,
                    "kind": message.kind.value,
                    "subject": message.subject,
                    "body": message.body,
                    "sender_agent_id": message.sender_agent_id,
                    "sender_name": await self._agents.name(message.sender_agent_id),
                    "recipient_agent_id": message.recipient_agent_id,
                    "recipient_name": await self._agents.name(
                        message.recipient_agent_id
                    ),
                    "repository_id": message.repository_id,
                    "task_id": message.task_id,
                    "status": message.status.value,
                    "event_id": message.event_id,
                    "correlation_id": message.correlation_id,
                    "created_at": message.created_at,
                    "direction": "leader_to_worker",
                }
            )
        return {"items": items}

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
            "contract": _contract_block(contract),
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

        # Contract §3: repair_timeline[].at is a string, but task rows persist no
        # timestamps. A rework task's own persisted moment is the candidate
        # revision it produced; otherwise the owning aggregate's timestamp.
        revision_at: dict[UUID, datetime] = {}
        fallback_at: datetime | None = None
        if change_set is not None:
            fallback_at = change_set.updated_at
            for revision in change_set.candidate_revisions:
                seen = revision_at.get(revision.task_id)
                if seen is None or revision.created_at > seen:
                    revision_at[revision.task_id] = revision.created_at
        elif snapshot is not None:
            fallback_at = snapshot.created_at

        views: list[dict] = []
        for task in worker_tasks:
            chain = rework_by_key.get((task.repository_id, task.parent_task_id), [])
            is_rework = task.title == REWORK_TASK_TITLE
            active_rework = any(
                item.status in _ACTIVE_TASK_STATUSES for item in chain
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
            repair_timeline = []
            for item in chain:
                if item.id == task.id:
                    continue
                at = revision_at.get(item.id, fallback_at)
                if at is None:
                    # No persisted fact anywhere to date this entry with.
                    continue
                repair_timeline.append(
                    {"at": at, "what": f"返工任务 {item.status.value}"}
                )
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


def _contract_block(contract: SpecificationContractData | None) -> dict | None:
    """§3 contract block, shared by the delivery detail and the issue overview."""

    if contract is None:
        return None
    return {
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


def _issue_recency(issue: dict) -> tuple:
    """§2.3 default ordering key: newest activity first, undated issues last."""

    at = issue["updated_at"]
    return (at is not None, at or _EPOCH)


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
