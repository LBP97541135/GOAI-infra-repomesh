"""Delivery read-model aggregation (contract v0.1 §2-§3).

One delivery = one ExecutionPlan lifecycle (``delivery_id = execution_plan_id``).
A project's latest un-materialized plan snapshot forms a virtual draft delivery
with ``delivery_id: null``. The service only reads through module contracts and
composition-root adapters; unimplemented contract fields return ``null``.
"""

import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from uuid import UUID

from repomesh.modules.agent_directory.contracts import AgentRole
from repomesh.modules.collaboration.contracts import CollaborationMessageView
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
    IssueState,
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
    RuntimeProbe,
    RuntimeSnapshot,
    SpecificationContractData,
    SpecificationSource,
    TaskSource,
    TopologySource,
    ValidationSource,
)

_logger = logging.getLogger(__name__)

_RUNTIME_PROBE_TIMEOUT = 2.0
"""§4.4: each controller call is bounded on its own so one hang cannot stall a page."""

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

_ACTIVE_TASK_STATUSES = frozenset({TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED})

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
    """Order rounds as they happened: §3 wants round 1 first.

    Keyed on plan_version and creation, never on updated_at. One sort used to
    serve both this and "which round is the latest" (§2.2, which is defined on
    updated_at), and the two disagree exactly when an older round's ChangeSet
    keeps moving after a newer round opens — a rework or a compensation, which
    is the most ordinary reason to open another round in the first place.
    """

    at = round_facts.created_at
    return (at is not None, at or _EPOCH, round_facts.plan_version or 0)


def _round_recency(round_facts: _RoundFacts) -> tuple:
    """§2.2's separate question: which round moved most recently."""

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
        runtime: RuntimeProbe | None = None,
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
        # None when AgentTeams is not configured: the roster still answers, with
        # runtime null rather than a fabricated "unreachable".
        self._runtime = runtime

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
                summary = await self._delivery_summary(plan, snapshot_by_plan.get(plan.id))
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
            updated_at=(change_set.updated_at if change_set is not None else created_at),
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
                gate = await self._change_sets.merge_gate(change_set.id, repository.repository_id)
                awaiting_governance = bool(gate.reasons) and all(
                    reason == MERGE_GATE_GOVERNANCE_MISSING_REASON for reason in gate.reasons
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
        self,
        *,
        state: str = "open",
        organization_id: UUID | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> dict:
        """Contract v0.2 §2: issue-grained listing (issue_id = project_id).

        The issue universe is every project that ever produced an ExecutionPlan
        or a PlanSnapshot — the only persisted evidence an issue exists. Issues
        with neither (§2.1 rule 6) stay unreachable until a project registry
        lands; the rule is implemented so the write endpoint needs no change.

        open_count / closed_count are the workspace totals behind the two tabs:
        they honour organization_id but ignore `state` and the page window, so
        the tab the caller is not looking at still shows a true total.
        """

        plans_by_project = await self._plans_by_project()
        project_ids = set(plans_by_project) | set(await self._snapshots.project_ids())

        scoped = []
        for project_id in sorted(project_ids, key=str):
            bundle = await self._issue_bundle(project_id, plans_by_project.get(project_id, ()))
            issue = bundle.summary
            if organization_id is not None and issue["organization_id"] != organization_id:
                continue
            scoped.append(issue)

        open_count = sum(1 for issue in scoped if issue["state"] == IssueState.OPEN.value)
        issues = (
            scoped if state == "all" else [issue for issue in scoped if issue["state"] == state]
        )
        issues.sort(key=_issue_recency, reverse=True)
        page = issues[offset : offset + limit]
        return {
            "issues": page,
            "open_count": open_count,
            "closed_count": len(scoped) - open_count,
            "next_cursor": (str(offset + limit) if offset + limit < len(issues) else None),
        }

    async def issue_summary(self, issue_id: UUID) -> dict | None:
        """Contract v0.2 §2 single-item shape for one issue.

        Public reuse point for the intake write endpoint (contract v0.3 §1.4):
        the response of POST /issues must be this projection, not a second
        serializer. Returns None when no plan or snapshot evidences the issue.
        """

        plans = (await self._plans_by_project()).get(issue_id, ())
        snapshots = await self._snapshots.for_project(issue_id)
        if not plans and not snapshots:
            return None
        return (await self._issue_bundle(issue_id, plans)).summary

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
            [await self._round_facts(plan, snapshot_by_plan.get(plan.id)) for plan in plans],
            key=_round_order,
        )

        # §0: title, requirement text and creation time all come from the
        # earliest snapshot; `for_project` returns newest plan_version first.
        earliest = snapshots[-1] if snapshots else None
        draft = snapshots[0] if snapshots and snapshots[0].execution_plan_id is None else None
        active = next(
            (
                facts
                for facts in reversed(rounds)
                if facts.plan.status is ExecutionPlanStatus.IN_PROGRESS
            ),
            None,
        )
        # §3 order and §2.2 recency are different questions; asking the
        # sorted-by-order list for its last element answered the wrong one.
        latest = max(rounds, key=_round_recency) if rounds else None
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
            repository_ids |= {team.repository_id for team in topology.repository_teams}

        opened_at = earliest.created_at if earliest is not None else None
        opened_by_agent_id = earliest.created_by_agent_id if earliest is not None else None
        # Same source and precision as v0.1's messages sender_name: an
        # AgentTeams resource name, never a human name.
        opened_by_name = (
            await self._agents.name(opened_by_agent_id) if opened_by_agent_id is not None else None
        )
        # §2.3: the latest persisted fact across every round and snapshot.
        timestamps = [facts.updated_at for facts in rounds if facts.updated_at] + [
            snapshot.created_at for snapshot in snapshots
        ]
        # Workspace attribution, most authoritative fact first. A draft-only
        # issue has neither round nor topology, so it falls back to the
        # organization of the agent that opened it — without this the workspace
        # filter would silently drop every un-materialized issue.
        organization_id = next((facts.plan.organization_id for facts in rounds), None) or (
            topology.organization_id if topology else None
        )
        if organization_id is None and (
            earliest is not None and earliest.created_by_agent_id is not None
        ):
            organization_id = await self._agents.organization_id(earliest.created_by_agent_id)

        summary = {
            "issue_id": project_id,
            "issue_key": None,  # §0: no project registry, so no human-readable id
            "organization_id": organization_id,
            "title": _title(
                earliest.requirement_text if earliest is not None else None, project_id
            ),
            "requirement_text": (earliest.requirement_text if earliest is not None else None),
            "state": state.value,
            "phase": phase.value,
            "phase_note": phase_note,
            "round_count": len(rounds),
            "active_round_id": active.plan.id if active is not None else None,
            "latest_round_id": latest.plan.id if latest is not None else None,
            "pending_decision_count": sum(facts.pending_decision_count for facts in rounds),
            "repository_count": len(repository_ids),
            "team_count": len(topology.repository_teams) if topology else 0,
            # Topology-only facts degrade to null rather than a fabricated
            # default when the issue never formed a team.
            "operational_status": (topology.operational_status.value if topology else None),
            "execution_mode": topology.execution_mode.value if topology else None,
            "opened_by_agent_id": opened_by_agent_id,
            "opened_by_name": opened_by_name,
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
        worker_task_ids = {task.id for task in tasks if task.parent_task_id in leader_task_ids}
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
        worker_task_ids = {task.id for task in tasks if task.parent_task_id in leader_task_ids}
        items = []
        for message in await self._messages.for_project(plan.project_id):
            if message.task_id is not None and message.task_id not in worker_task_ids:
                continue
            items.append(await self._message_item(message))
        return {"items": items}

    async def _message_item(self, message: CollaborationMessageView) -> dict:
        """§4.2 projection, shared by /messages and the room stream (v0.2 §5.2).

        room_id was missing from v0.1's projection even though the view always
        carried it; the room stream needs it, and both endpoints must agree.
        """

        return {
            "id": message.id,
            "kind": message.kind.value,
            "subject": message.subject,
            "body": message.body,
            "sender_agent_id": message.sender_agent_id,
            "sender_name": await self._agents.name(message.sender_agent_id),
            "recipient_agent_id": message.recipient_agent_id,
            "recipient_name": await self._agents.name(message.recipient_agent_id),
            "repository_id": message.repository_id,
            "task_id": message.task_id,
            "room_id": message.room_id,
            "status": message.status.value,
            "event_id": message.event_id,
            "correlation_id": message.correlation_id,
            "created_at": message.created_at,
            "direction": "leader_to_worker",
        }

    # ----------------------------------------------------------------- rooms

    async def list_rooms(self, issue_id: UUID) -> dict | None:
        """Contract v0.2 §5.1: two rooms per team — teamRoom and leaderDM.

        Rooms come from the topology's persisted room ids; `kind` is decided by
        which field held the id, never guessed. An empty room reports
        last_message null and message_count 0 rather than a placeholder.
        """

        topology = await self._topology.get_view(issue_id)
        if topology is None:
            if not await self._issue_exists(issue_id):
                return None
            # The issue exists but never formed a team, so it has no rooms.
            return {"rooms": []}

        catalog = {item.id: item for item in await self._repositories.list()}
        tasks = await self._tasks.list_by_project(issue_id)
        live_repositories = {
            task.repository_id for task in tasks if task.status is TaskStatus.IN_PROGRESS
        }
        rooms: list[dict] = []
        for team in topology.repository_teams:
            leader = await self._member(team.leader_agent_id, "repository_leader")
            # Membership differs per room kind: the team room holds the workers,
            # the leader DM is the repository leader talking to the organization
            # leader. Listing workers in the DM would misdescribe who can read it.
            members_by_kind = {
                "team_room": [leader]
                + [await self._member(worker_id, "worker") for worker_id in team.worker_agent_ids],
                "leader_dm": [
                    leader,
                    await self._member(topology.organization_leader_id, "organization_leader"),
                ],
            }
            for room_id, kind in (
                (team.room_id, "team_room"),
                (team.leader_room_id, "leader_dm"),
            ):
                if room_id is None:
                    continue  # the team never got that room provisioned
                messages = await self._messages.for_room(room_id)
                last = max(messages, key=lambda item: item.created_at, default=None)
                rooms.append(
                    {
                        "room_id": room_id,
                        "kind": kind,
                        "issue_id": issue_id,
                        "team_id": team.id,
                        "repository_id": team.repository_id,
                        "repository_name": (
                            catalog[team.repository_id].name
                            if team.repository_id in catalog
                            else None
                        ),
                        "members": members_by_kind[kind],
                        "last_message": (
                            {
                                "at": last.created_at,
                                "kind": last.kind.value,
                                "subject": last.subject,
                                "sender_agent_id": last.sender_agent_id,
                            }
                            if last is not None
                            else None
                        ),
                        "message_count": len(messages),
                        # §5.3: derived from in-flight work, never presence.
                        "live": team.repository_id in live_repositories,
                    }
                )
        return {"rooms": rooms}

    async def _member(self, agent_id: UUID, role: str) -> dict:
        return {
            "agent_id": agent_id,
            "name": await self._agents.name(agent_id),
            "role": role,
        }

    async def room_stream(self, room_id: str, *, offset: int = 0, limit: int = 100) -> dict | None:
        """Contract v0.2 §5.2: one room's real messages plus console projections.

        Only `source == "message"` happened inside the room. Governance
        decisions project into the owning repository's leaderDM (Q4 ruling A);
        gate and runner facts project into its teamRoom, because that is where
        the work they describe was carried out. Every non-message item carries a
        payload_ref so the frontend can link back to the underlying fact — and
        so ordering stays stable, as in v0.1 §4.1.
        """

        topology = await self._topology.find_by_room(room_id)
        if topology is None:
            return None
        team = next(
            (
                item
                for item in topology.repository_teams
                if room_id in {item.room_id, item.leader_room_id}
            ),
            None,
        )
        if team is None:
            return None
        is_leader_dm = room_id == team.leader_room_id

        items: list[dict] = []
        for message in await self._messages.for_room(room_id):
            items.append(
                {
                    "at": message.created_at,
                    "source": "message",
                    "room_id": room_id,
                    "message": await self._message_item(message),
                    "text": message.subject,
                    "repository_id": message.repository_id,
                    "task_id": message.task_id,
                    "payload_ref": f"collaboration-message:{message.id}",
                }
            )

        project_id = topology.project_id
        plans = (await self._plans_by_project()).get(project_id, ())
        if is_leader_dm:
            for plan in plans:
                change_set = await self._change_sets.for_delivery(plan.id)
                if change_set is None:
                    continue
                for decision in change_set.governance_decisions:
                    if decision.repository_id != team.repository_id:
                        continue
                    items.append(
                        _projected_item(
                            at=decision.decided_at,
                            source="governance",
                            room_id=room_id,
                            text=(f"治理决策 {decision.decision.value}: {decision.reason}"),
                            repository_id=decision.repository_id,
                            payload_ref=f"governance-decision:{decision.id}",
                        )
                    )
        else:
            tasks = await self._tasks.list_by_project(project_id)
            repository_task_ids = {
                task.id for task in tasks if task.repository_id == team.repository_id
            }
            for event in await self._runner_events.for_project(project_id):
                if event.task_id not in repository_task_ids:
                    continue
                items.append(
                    _projected_item(
                        at=event.occurred_at,
                        source="runner",
                        room_id=room_id,
                        text=event.event_type,
                        repository_id=event.repository_id,
                        payload_ref=f"runner-event:{event.event_id}",
                        task_id=event.task_id,
                    )
                )
            for plan in plans:
                change_set = await self._change_sets.for_delivery(plan.id)
                if change_set is None:
                    continue
                for observation in await self._observations.for_change_set(change_set.id):
                    if observation.repository_id != team.repository_id:
                        continue
                    items.append(
                        _projected_item(
                            at=observation.observed_at,
                            source="gate",
                            room_id=room_id,
                            text=observation.event_type,
                            repository_id=observation.repository_id,
                            payload_ref=f"scm-observation:{observation.id}",
                        )
                    )

        items.sort(key=lambda item: (item["at"], item["payload_ref"]))
        return {
            "items": items[offset : offset + limit],
            "next_cursor": (str(offset + limit) if offset + limit < len(items) else None),
        }

    async def repository_plan(self, issue_id: UUID, repository_id: UUID) -> dict | None:
        """Contract v0.2 §5.4: the DAG / PLAN / SPEC sheet for one repository.

        The DAG is repository-grained: nodes are the planned repositories,
        layers are execution_batches and edges come from task_dag[].depends_on.
        graph_edges is not projected — the column is persisted but its only
        producer writes an empty list, so it would render as an empty DAG.
        """

        snapshots = await self._snapshots.for_project(issue_id)
        if not snapshots:
            # Two cases here — the issue does not exist, and the issue exists
            # but was never planned — and both are a 404 to the caller. They
            # used to be written as separate branches returning the same
            # value, so the existence query ran for nothing.
            return None
        snapshot = snapshots[0]
        catalog = {item.id: item for item in await self._repositories.list()}
        id_by_name = {item.name: item.id for item in catalog.values()}

        # Membership check. Without it the sheet for an unrelated repository
        # came back 200 carrying this issue's DAG, is_focus false everywhere
        # and spec null — which §5.4 tells the frontend to read as "this
        # repository has no spec of its own", so a wrong page rendered as a
        # perfectly normal one.
        #
        # The set is the union of two things that are not the same: the
        # repositories this sheet is drawn from (the snapshot's batches, which
        # is what carries a repository into the DAG) and the issue's own
        # repositories per §3 (every round's plan plus the topology). A
        # repository can sit in either one alone and still legitimately belong
        # here, so checking only the bundle rejects rows the sheet itself
        # draws.
        planned = {
            id_by_name[name]
            for batch in snapshot.execution_batches
            for name in batch
            if name in id_by_name
        }
        if repository_id not in planned:
            plans = (await self._plans_by_project()).get(issue_id, ())
            bundle = await self._issue_bundle(issue_id, plans)
            if repository_id not in bundle.repository_ids:
                return None

        nodes = []
        for batch_index, batch in enumerate(snapshot.execution_batches):
            for name in batch:
                node_id = id_by_name.get(name)
                nodes.append(
                    {
                        "repository_id": node_id,
                        "name": name,
                        "batch_index": batch_index,
                        "is_focus": node_id == repository_id,
                    }
                )
        edges = []
        dropped: list[str] = []
        for node in snapshot.task_dag:
            target_name = str(node.get("repository", ""))
            target = id_by_name.get(target_name)
            for dependency in node.get("depends_on") or ():
                source = id_by_name.get(str(dependency))
                if source is None or target is None:
                    # A name the catalog cannot resolve is not an edge; an edge
                    # with a null endpoint would draw a line to nowhere.
                    dropped.append(f"{dependency} -> {target_name}")
                    continue
                edges.append({"from_repository_id": source, "to_repository_id": target})
        if dropped:
            # Never truncate silently: a DAG that is quietly missing edges reads
            # as a complete one.
            _logger.warning(
                "issue %s repository %s: dropped %d unresolvable DAG edge(s): %s",
                issue_id,
                repository_id,
                len(dropped),
                ", ".join(dropped),
            )

        spec = await self._specifications.repository_spec(issue_id, repository_id)
        return {
            "issue_id": issue_id,
            "repository_id": repository_id,
            "plan_version": snapshot.plan_version,
            "dag": {
                "nodes": nodes,
                "edges": edges,
                "granularity": "repository",
                "edge_source": "task_dag.depends_on",
            },
            "execution_batches": [list(batch) for batch in snapshot.execution_batches],
            "spec": (
                {
                    "specification_id": spec.specification_id,
                    "kind": spec.kind,
                    "status": spec.status,
                    "revision": spec.revision,
                    "goal": spec.goal,
                    "acceptance": list(spec.acceptance),
                    "allowed_paths": list(spec.allowed_paths),
                    "forbidden_paths": list(spec.forbidden_paths),
                    "tests": list(spec.tests),
                }
                if spec is not None
                else None
            ),
            "engineering_contract": _contract_block(
                await self._specifications.engineering_contract(issue_id)
            ),
        }

    # ------------------------------------------- grid / teams / agent roster

    async def list_repositories(self) -> dict:
        """Contract v0.2 §4.1: the catalog plus what is happening in each repo.

        Business activity is derived from the same issue aggregation /issues
        uses, so a repository's open_issue_count can never disagree with the
        issue list's own state.
        """

        profiles = await self._repositories.profiles()
        topologies = await self._topology.list_views()
        teams_by_repository: dict[UUID, list[dict]] = {}
        for topology in topologies:
            for team in topology.repository_teams:
                teams_by_repository.setdefault(team.repository_id, []).append(
                    {
                        "team_id": team.id,
                        "issue_id": topology.project_id,
                        "runtime_status": team.runtime_status.value,
                    }
                )

        open_issues: dict[UUID, int] = {}
        last_delivery: dict[UUID, datetime] = {}
        for bundle in await self._issue_bundles():
            for repository_id in bundle.repository_ids:
                if bundle.summary["state"] == IssueState.OPEN.value:
                    open_issues[repository_id] = open_issues.get(repository_id, 0) + 1
            for facts in bundle.rounds:
                if facts.change_set is None:
                    continue
                for item in facts.change_set.repositories:
                    seen = last_delivery.get(item.repository_id)
                    if seen is None or facts.change_set.updated_at > seen:
                        last_delivery[item.repository_id] = facts.change_set.updated_at

        active_tasks: dict[UUID, int] = {}
        for task in await self._tasks.list_all():
            if task.status in _ACTIVE_TASK_STATUSES:
                active_tasks[task.repository_id] = active_tasks.get(task.repository_id, 0) + 1

        return {
            "repositories": [
                {
                    "repository_id": profile.id,
                    "name": profile.name,
                    "url": profile.url,
                    "description": profile.description,
                    "topics": list(profile.topics),
                    "languages": list(profile.languages),
                    "profiled_at": profile.profiled_at,
                    "resident_team_count": len(teams_by_repository.get(profile.id, ())),
                    "open_issue_count": open_issues.get(profile.id, 0),
                    "active_task_count": active_tasks.get(profile.id, 0),
                    "last_delivery_at": last_delivery.get(profile.id),
                    "teams": teams_by_repository.get(profile.id, []),
                }
                for profile in sorted(profiles, key=lambda item: item.name)
            ]
        }

    async def list_teams(self, *, with_runtime: bool = True) -> dict:
        """Contract v0.2 §4.2.

        `runtime_status` (what team formation recorded) and `runtime.phase`
        (what the controller reports now) are two different facts and are never
        merged: the first is history, the second may be unreachable.
        """

        catalog = {item.id: item for item in await self._repositories.list()}
        teams: list[dict] = []
        probes: list[str] = []
        for topology in await self._topology.list_views():
            for team in topology.repository_teams:
                probes.append(team.agentteams_team_name)
                teams.append(
                    {
                        "team_id": team.id,
                        "agentteams_team_name": team.agentteams_team_name,
                        "issue_id": topology.project_id,
                        "repository_id": team.repository_id,
                        "repository_name": (
                            catalog[team.repository_id].name
                            if team.repository_id in catalog
                            else None
                        ),
                        "runtime_status": team.runtime_status.value,
                        "team_room_id": team.room_id,
                        "leader_room_id": team.leader_room_id,
                        "leader": await self._member(team.leader_agent_id, "repository_leader"),
                        "workers": [
                            await self._member(worker_id, "worker")
                            for worker_id in team.worker_agent_ids
                        ],
                        "runtime": None,
                    }
                )
        if with_runtime:
            await self._attach_runtime(
                teams, [("team", name) for name in probes], _team_runtime_fields
            )
        return {"teams": teams}

    async def list_agents(self, *, with_runtime: bool = True) -> dict:
        """Contract v0.2 §4.3: the persisted roster plus a live runtime proxy."""

        topologies = await self._topology.list_views()
        team_by_agent: dict[UUID, tuple[UUID, UUID]] = {}
        for topology in topologies:
            for team in topology.repository_teams:
                for agent_id in (team.leader_agent_id, *team.worker_agent_ids):
                    team_by_agent[agent_id] = (team.id, topology.project_id)

        catalog = {item.id: item for item in await self._repositories.list()}
        active_tasks: dict[UUID, int] = {}
        for task in await self._tasks.list_all():
            if task.status in _ACTIVE_TASK_STATUSES and task.assignee_agent_id:
                active_tasks[task.assignee_agent_id] = (
                    active_tasks.get(task.assignee_agent_id, 0) + 1
                )

        agents: list[dict] = []
        probes: list[tuple[str, str]] = []
        for principal in await self._agents.list_all():
            team_id, issue_id = team_by_agent.get(principal.id, (None, None))
            is_manager = principal.role is AgentRole.ORGANIZATION_LEADER
            probes.append(
                (
                    "manager" if is_manager else "worker",
                    principal.agentteams_resource_name,
                )
            )
            agents.append(
                {
                    "agent_id": principal.id,
                    "organization_id": principal.organization_id,
                    "role": principal.role.value,
                    "status": principal.status.value,
                    "agentteams_resource_name": principal.agentteams_resource_name,
                    "leader_agent_id": principal.leader_agent_id,
                    "repository_id": principal.repository_id,
                    "repository_name": (
                        catalog[principal.repository_id].name
                        if principal.repository_id in catalog
                        else None
                    ),
                    "responsibility_paths": list(principal.responsibility_paths),
                    "team_id": team_id,
                    "issue_id": issue_id,
                    "active_task_count": active_tasks.get(principal.id, 0),
                    "runtime": None,
                }
            )
        if with_runtime:
            await self._attach_runtime(agents, probes, _agent_runtime_fields)
        return {"agents": agents}

    async def _attach_runtime(self, rows: list[dict], probes: list[tuple[str, str]], shape) -> None:
        """Probe every row concurrently and fill its runtime block in place.

        Concurrency is not a micro-optimisation here: probes are bounded by a
        timeout, so running them one after another makes an offline controller
        cost `rows x timeout` — a roster of nine agents took 18s before this,
        which is indistinguishable from an outage even though every row
        degrades correctly.

        Isolation is belt and braces. _runtime_block absorbs the failures it
        can see, but it used to claim more than it delivered: the docstring
        said gather could never propagate one row's problem to another while
        two of its lines sat outside the try. gather now also collects
        exceptions instead of cancelling its siblings, so a row that fails in
        a way nobody anticipated still costs one row.
        """

        blocks = await asyncio.gather(
            *(self._runtime_block(kind, name, shape) for kind, name in probes),
            return_exceptions=True,
        )
        for row, block in zip(rows, blocks, strict=True):
            if isinstance(block, BaseException):
                _logger.warning("runtime block failed unexpectedly", exc_info=block)
                block = {"reachable": False}
            row["runtime"] = block

    async def _runtime_block(self, kind: str, name: str, shape) -> dict | None:
        """§4.4 live proxy with the per-item isolation the contract mandates.

        The isolation is enforced here rather than trusted to the adapter: one
        slow or broken resource degrades its own row to `reachable: false` and
        the page still answers 200, because a roster whose persisted half is
        perfectly readable must not fail wholesale over a runtime probe.
        """

        if self._runtime is None:
            return None  # AgentTeams is not configured: no fact to report
        try:
            probe = getattr(self._runtime, kind)
            snapshot = await asyncio.wait_for(probe(name), timeout=_RUNTIME_PROBE_TIMEOUT)
            if snapshot is None:
                return None  # 404: the controller has no such resource
            return {"reachable": True, **shape(snapshot)}
        except Exception as error:
            # exc_info because the three ways this fires — a misconfigured
            # token, a controller that is down, and a bug in the adapter —
            # produce the same reachable:false and nobody could tell them
            # apart from the old message alone.
            _logger.warning(
                "runtime probe failed for %s %s; degrading this row only",
                kind,
                name,
                exc_info=error,
            )
            return {"reachable": False}

    async def _issue_bundles(self) -> list[_IssueBundle]:
        plans_by_project = await self._plans_by_project()
        project_ids = set(plans_by_project) | set(await self._snapshots.project_ids())
        return [
            await self._issue_bundle(project_id, plans_by_project.get(project_id, ()))
            for project_id in sorted(project_ids, key=str)
        ]

    async def _issue_exists(self, issue_id: UUID) -> bool:
        if (await self._plans_by_project()).get(issue_id):
            return True
        return bool(await self._snapshots.for_project(issue_id))

    # ----------------------------------------------------------------- detail

    async def get_delivery(self, delivery_id: UUID) -> dict | None:
        plan = await self._plans.get(delivery_id)
        if plan is None:
            return None
        project_id = plan.project_id
        snapshots = await self._snapshots.for_project(project_id)
        snapshot = next((item for item in snapshots if item.execution_plan_id == delivery_id), None)
        change_set = await self._change_sets.for_delivery(delivery_id)
        validation = await self._find_validation(project_id, delivery_id, change_set)
        contract = await self._specifications.engineering_contract(project_id)
        catalog = {item.id: item for item in await self._repositories.list()}
        tasks = await self._tasks.list_by_project(project_id)

        plan_repository_ids = {planned.repository_id for batch in plan.batches for planned in batch}
        leader_task_ids = {
            planned.leader_task_id
            for batch in plan.batches
            for planned in batch
            if planned.leader_task_id is not None
        }
        worker_tasks = tuple(task for task in tasks if task.parent_task_id in leader_task_ids)
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
                "requirement_text": (snapshot.requirement_text if snapshot is not None else None),
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
            "change_set": self._change_set_block(change_set) if change_set is not None else None,
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
                rework_by_key.setdefault((task.repository_id, task.parent_task_id), []).append(task)

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
            active_rework = any(item.status in _ACTIVE_TASK_STATUSES for item in chain)
            repairing = is_rework or (active_rework and task.status is TaskStatus.IN_PROGRESS)
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
                repair_timeline.append({"at": at, "what": f"返工任务 {item.status.value}"})
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
                    # §5.2, as an attempt number: the original try is 1 and the
                    # k-th rework on the same (repository, parent) key is 1 + k.
                    # The original row used to report the total instead, so one
                    # original plus two reworks read 3 / 2 / 3 — two rows both
                    # claiming to be the third attempt, and the first try
                    # labelled as the third.
                    "attempt": (2 + chain.index(task)) if is_rework else 1,
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


def _team_runtime_fields(snapshot: RuntimeSnapshot) -> dict:
    return {
        "phase": snapshot.phase,
        "ready_workers": snapshot.ready_workers,
        "total_workers": snapshot.total_workers,
    }


def _agent_runtime_fields(snapshot: RuntimeSnapshot) -> dict:
    """§4.4: awake and uptime_seconds have no source and stay null.

    The controller exposes no start timestamp, and DesiredRuntimeState is what
    we asked for rather than what is observed — passing it off as an observation
    would be fabrication.
    """

    return {
        "phase": snapshot.phase,
        "runtime_kind": snapshot.runtime_kind,
        "matrix_user_id": snapshot.matrix_user_id,
        "room_id": snapshot.room_id,
        "message": snapshot.message,
        "awake": None,
        "uptime_seconds": None,
    }


def _projected_item(
    *,
    at: datetime,
    source: str,
    room_id: str,
    text: str,
    repository_id: UUID | None,
    payload_ref: str,
    task_id: UUID | None = None,
) -> dict:
    """A §5.2 stream entry that did NOT happen inside the room.

    `message` is None precisely so the frontend cannot render it as a chat
    bubble: the contract requires system-entry styling for every non-message
    source, or a user would read a console projection as something an agent said.
    """

    return {
        "at": at,
        "source": source,
        "room_id": room_id,
        "message": None,
        "text": text,
        "repository_id": repository_id,
        "task_id": task_id,
        "payload_ref": payload_ref,
    }


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
