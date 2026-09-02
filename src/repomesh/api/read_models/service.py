"""Delivery read-model aggregation (contract v0.1 §2-§3).

One delivery = one ExecutionPlan lifecycle (``delivery_id = execution_plan_id``).
A project's latest un-materialized plan snapshot forms a virtual draft delivery
with ``delivery_id: null``. The service only reads through module contracts and
composition-root adapters; unimplemented contract fields return ``null``.
"""

import asyncio
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from uuid import UUID

from repomesh.modules.agent_directory.contracts import AgentRole
from repomesh.modules.collaboration.contracts import (
    CollaborationMessageView,
    RoomTimelineEntryView,
)
from repomesh.modules.delivery.contracts import (
    MERGE_GATE_GOVERNANCE_MISSING_REASON,
    ChangeSetView,
    RecoveryActionKind,
    RecoveryActionStatus,
    RecoveryActionView,
    RepositoryDeliveryStatus,
)
from repomesh.modules.project.contracts import ProjectAgentTopologyView
from repomesh.modules.repository_intelligence.contracts import (
    classification_fingerprint,
    discovery_step,
    discovery_step_state,
    effective_tiers,
)
from repomesh.modules.task_orchestration.contracts import (
    ExecutionPlanStatus,
    ExecutionPlanView,
    TaskOrigin,
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
    DiscoveryTaskProbe,
    ExecutionPlanSource,
    MessageSource,
    ObservationSource,
    PlanSnapshotData,
    PlanSnapshotSource,
    RepositorySource,
    RoomTimelineSource,
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

_RUNTIME_PROBE_CONCURRENCY = 16
"""How many controller probes may be in flight at once.

The fan-out had no ceiling, and httpx caps a client at 100 connections by
default. Past that the requests queue inside the pool — and queueing time
counts against each probe's own timeout, so rows start reporting
`reachable: false` while the controller is perfectly healthy. A false outage
is worse than the serial wait §4.4 set out to remove, because it looks like
a real one."""

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

_ROLLBACK_ACTION_KINDS = {
    RecoveryActionKind.CLOSE_PULL_REQUEST: "withhold",
    RecoveryActionKind.CREATE_REVERT_PULL_REQUEST: "revert_pull_request",
}
"""§4.6: the two planned action kinds a rollback scope row can report.

MERGE_REVERT_PULL_REQUEST is the second half of the same repository's revert
and REVALIDATE_CHANGESET belongs to the whole set, so neither opens a row."""

_ACTIVE_TASK_STATUSES = frozenset({TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED})

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
"""Sort floor for rounds whose only timestamp source is missing entirely."""

_SETTLED_PHASES = frozenset({DeliveryPhase.DELIVERED, DeliveryPhase.ARCHIVED})
"""Rounds a stale delivery refusal must not be allowed to talk over (A-19)."""


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
        room_timeline: RoomTimelineSource | None = None,
        runtime: RuntimeProbe | None = None,
        discovery_tasks: DiscoveryTaskProbe | None = None,
        probe_timeout: float | None = None,
        probe_concurrency: int | None = None,
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
        # None where no room-timeline store is composed. The stream then shows
        # RepoMesh's outbound messages and nothing else — which is what it
        # showed before the ingest lane existed, and an honest "we recorded
        # nothing" rather than a claim that the rooms were silent.
        self._room_timeline = room_timeline
        # None when AgentTeams is not configured: the roster still answers, with
        # runtime null rather than a fabricated "unreachable".
        self._runtime = runtime
        # None outside the API process: the projection then reports the block's
        # own state and no "running", which is true — this process knows of no
        # step in flight — rather than guessed.
        self._discovery_tasks = discovery_tasks
        # Per-request memo for reads this aggregation repeats. The container
        # builds a fresh service for every request — delivery_read_model_service
        # is deliberately not a cached_service — so nothing memoised here can
        # outlive the request that read it, and no request can serve another
        # request's snapshot of the world.
        self._repository_memo: list | None = None
        self._plans_memo: dict[UUID, list[ExecutionPlanView]] | None = None
        self._tasks_memo: dict[UUID, tuple] = {}
        self._validation_memo: dict[UUID, tuple] = {}
        self._name_memo: dict[UUID | None, str | None] = {}
        # Both default to the module constants so a test can monkeypatch them;
        # the composition root passes the configured values.
        self._probe_timeout = probe_timeout if probe_timeout is not None else _RUNTIME_PROBE_TIMEOUT
        self._probe_concurrency = (
            probe_concurrency if probe_concurrency is not None else _RUNTIME_PROBE_CONCURRENCY
        )

    async def _all_repositories(self) -> list:
        """The catalog, read once per request rather than once per round."""

        if self._repository_memo is None:
            self._repository_memo = list(await self._repositories.list())
        return self._repository_memo

    async def _catalog(self) -> dict:
        return {item.id: item for item in await self._all_repositories()}

    async def _tasks_of(self, project_id: UUID) -> tuple:
        if project_id not in self._tasks_memo:
            self._tasks_memo[project_id] = tuple(await self._tasks.list_by_project(project_id))
        return self._tasks_memo[project_id]

    async def _validations_of(self, project_id: UUID) -> tuple:
        if project_id not in self._validation_memo:
            self._validation_memo[project_id] = tuple(
                await self._validations.for_project(project_id)
            )
        return self._validation_memo[project_id]

    async def _issue_repository_ids(self, issue_id: UUID) -> set[UUID]:
        """The repositories a §3 issue owns: every round's plan plus the topology.

        Same set _issue_bundle publishes, derived without the round facts —
        callers that only need membership should not pay for change sets and
        decision counts.
        """

        plans = (await self._plans_by_project()).get(issue_id, ())
        owned = {
            planned.repository_id for plan in plans for batch in plan.batches for planned in batch
        }
        topology = await self._topology.get_view(issue_id)
        if topology is not None:
            owned |= {team.repository_id for team in topology.repository_teams}
        return owned

    def _name_resolver(self, catalog: dict, owned: set[UUID]) -> dict[str, UUID | None]:
        """Map repository name to id, preferring the issue's own repositories.

        `repositories.name` carries no unique constraint and holds the platform
        short name, so two owners' `api` are both legitimate rows. A plain
        `{item.name: item.id}` comprehension resolved such a name to whichever
        row came last, which silently pointed nodes, edges and is_focus at a
        repository from another issue.

        Names outside the issue fall back to the catalog at large, and a name
        that is ambiguous there resolves to None — the same treatment as a name
        the catalog does not know, because picking one of two is a guess.
        """

        def collect(items) -> dict[str, UUID | None]:
            found: dict[str, UUID | None] = {}
            for item in items:
                if item.name in found and found[item.name] != item.id:
                    found[item.name] = None  # ambiguous: refuse to guess
                else:
                    found.setdefault(item.name, item.id)
            return found

        mine = collect(item for item in catalog.values() if item.id in owned)
        everywhere = collect(catalog.values())
        # The issue's own repositories win outright; only names it does not
        # own fall through, and there an ambiguous name stays unresolved.
        return {**everywhere, **mine}

    async def _agent_name(self, agent_id: UUID | None) -> str | None:
        """Resolve a name once per agent instead of once per mention.

        A room stream projects two names per message and the same leader can
        appear on every line, so this used to be 2N queries for a handful of
        distinct agents.
        """

        if agent_id not in self._name_memo:
            self._name_memo[agent_id] = await self._agents.name(agent_id)
        return self._name_memo[agent_id]

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
        refusal = plan.delivery_refusal
        if refusal is not None and phase not in _SETTLED_PHASES:
            # Defect A-19. Ahead of every other note on purpose: the batch did
            # succeed and the plan is in progress, so "第 n/m 批执行中" is what
            # this round used to say while it was in fact refused and going
            # nowhere. The reason is the delivering side's own sentence,
            # unedited — a reworded refusal is one the operator cannot match
            # against the log or the evidence.
            return f"交付被拒:{refusal.reason}"
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

        catalog = await self._catalog()
        leader_task_ids = {
            planned.leader_task_id
            for batch in plan.batches
            for planned in batch
            if planned.leader_task_id is not None
        }
        tasks = await self._tasks_of(plan.project_id)
        active_rework_repos = {
            task.repository_id
            for task in tasks
            if task.parent_task_id in leader_task_ids
            and task.origin is TaskOrigin.REWORK
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
        """Contract v0.2 §3: §2's fields plus the round index and chips.

        v0.4 §3.3 adds two scalars and no more. The badge on a detail page
        needs "which step, what state"; the follow-up questions, the scored
        candidates, the rationales and the tiering are an open-the-panel read
        and live only on ``GET /issues/{id}/discovery``. Folding them in here
        would make every detail render drag a page of rationale prose with it,
        and ``GET /issues`` gets nothing at all — the list has no place to show
        discovery progress, so a field there would have no consumer.
        """

        plans_by_project = await self._plans_by_project()
        plans = plans_by_project.get(issue_id, ())
        snapshots = await self._snapshots.for_project(issue_id)
        if not plans and not snapshots:
            return None

        bundle = await self._issue_bundle(issue_id, plans)
        topology = bundle.topology
        catalog = await self._catalog()
        team_by_repository = (
            {team.repository_id: team for team in topology.repository_teams}
            if topology is not None
            else {}
        )
        contract = await self._specifications.engineering_contract(issue_id)
        discovery_snapshot = self._draft_of(snapshots) or (
            snapshots[0] if snapshots else None
        )
        step, step_state, _running = self._discovery_state(issue_id, discovery_snapshot)
        return {
            **bundle.summary,
            "discovery_step": step,
            "discovery_state": step_state,
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
        """Every plan, grouped, read once per request.

        Seven call sites ask for this and each one used to scan the whole
        execution_plans table — including the projection that answers a single
        POST /issues.
        """

        if self._plans_memo is None:
            plans_by_project: dict[UUID, list[ExecutionPlanView]] = {}
            for plan in await self._plans.list_all():
                plans_by_project.setdefault(plan.project_id, []).append(plan)
            self._plans_memo = plans_by_project
        return self._plans_memo

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
            # §2.1 rule 4b (A-22). `_round_facts` feeds `archived` into
            # `derive_phase`, which returns ARCHIVED before it ever returns
            # FAILED — so phase FAILED already means "failed and not yet
            # archived" and needs no second archive lookup here.
            latest_round_failed_and_unarchived=(
                latest is not None and latest.phase is DeliveryPhase.FAILED
            ),
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
            await self._agent_name(opened_by_agent_id) if opened_by_agent_id is not None else None
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
            "document_filename": (
                earliest.document_filename if earliest is not None else None
            ),
            "state": state.value,
            "phase": phase.value,
            "phase_note": phase_note,
            "round_count": len(rounds),
            "active_round_id": active.plan.id if active is not None else None,
            "latest_round_id": latest.plan.id if latest is not None else None,
            "pending_decision_count": sum(facts.pending_decision_count for facts in rounds),
            # §2.3: a requirement is "pending planning" while its draft has not
            # been materialized into an execution plan yet — the Org Leader's
            # next action on it is to drive discovery and materialize. Same
            # branch as the "计划 vN 待物化" phase note above: draft present and
            # not a single round recorded.
            "pending_planning": draft is not None and not rounds,
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
        tasks = await self._tasks_of(project_id)
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
        tasks = await self._tasks_of(plan.project_id)
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
            "sender_name": await self._agent_name(message.sender_agent_id),
            "recipient_agent_id": message.recipient_agent_id,
            "recipient_name": await self._agent_name(message.recipient_agent_id),
            "repository_id": message.repository_id,
            "task_id": message.task_id,
            "room_id": message.room_id,
            "status": message.status.value,
            "event_id": message.event_id,
            "correlation_id": message.correlation_id,
            "created_at": message.created_at,
            "direction": "leader_to_worker",
        }

    async def _timeline_message_item(self, entry: RoomTimelineEntryView) -> dict:
        """A message the room itself carried, projected as a chat bubble.

        Shaped like ``_message_item`` because it renders through the same
        component — the frontend's one branch is ``message !== null`` — but
        built here rather than there because almost every business field of an
        outbound message is a fact this one does not have. A recorded room
        message has no kind, no subject, no recipient, no correlation and no
        task: it is a person or an external agent typing. Those come out null
        rather than filled with plausible defaults, because a defaulted
        ``kind`` would render a chip asserting something nobody said.

        ``sender_name`` is the exception that must never be null. It is the
        resolved principal's name when the ingest could map the Matrix user
        onto one, and otherwise the raw Matrix id (adjudication D-4) — the
        honest unknown, rendered as itself. It is also the only field the
        bubble falls back *from*: with a name present the frontend never
        dereferences ``sender_agent_id``, which is null for exactly these
        unresolved senders.
        """

        resolved_name = (
            await self._agent_name(entry.sender_agent_id)
            if entry.sender_agent_id is not None
            else None
        )
        return {
            # The Matrix event id is this message's whole identity; there is no
            # ``collaboration.messages`` row behind it to borrow a UUID from.
            "id": entry.event_id,
            "kind": None,
            "subject": None,
            "body": entry.body,
            "sender_agent_id": entry.sender_agent_id,
            # The raw handle also covers a resolved principal the registry no
            # longer names: a known id with an unknown name is still a sender
            # we can show honestly.
            "sender_name": resolved_name or entry.sender_matrix_user_id,
            "sender_matrix_user_id": entry.sender_matrix_user_id,
            "recipient_agent_id": None,
            "recipient_name": None,
            "repository_id": entry.repository_id,
            "task_id": None,
            "room_id": entry.room_id,
            # Not "delivered": that column tracks whether *our* send reached
            # Matrix, and an inbound message has no such lifecycle. Reusing the
            # word would assert a delivery nobody performed.
            "status": None,
            "event_id": entry.event_id,
            "correlation_id": None,
            "created_at": entry.occurred_at,
            "direction": None,
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

        catalog = await self._catalog()
        tasks = await self._tasks_of(issue_id)
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
            "name": await self._agent_name(agent_id),
            "role": role,
        }

    async def room_stream(self, room_id: str, *, offset: int = 0, limit: int = 100) -> dict | None:
        """Contract v0.2 §5.2: one room's real messages plus console projections.

        Two sources *did* happen inside the room and render as chat bubbles:
        `source == "message"` (RepoMesh's outbound rows) and `source ==
        "matrix"` (what the ingest recorded from the room's own timeline).
        Governance decisions project into the owning repository's leaderDM (Q4
        ruling A); gate and runner facts project into its teamRoom, because
        that is where the work they describe was carried out. Every non-message
        item carries a payload_ref so the frontend can link back to the
        underlying fact — and so ordering stays stable, as in v0.1 §4.1.

        **Every message RepoMesh sends comes back through the room's own
        timeline**, so the two bubble sources overlap exactly on RepoMesh's own
        traffic. They are de-duplicated by Matrix event id and the outbound row
        wins (adjudication D-3): both describe the same event, but the outbound
        row knows what the message *was for* — its kind, its task, its
        recipient — while the timeline copy is only an echo. Dropping the echo
        rather than the record is what keeps a dispatch from appearing twice.
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
        outbound_event_ids: set[str] = set()
        for message in await self._messages.for_room(room_id):
            if message.event_id:
                outbound_event_ids.add(message.event_id)
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
        if self._room_timeline is not None:
            for entry in await self._room_timeline.for_room(room_id):
                if entry.event_id in outbound_event_ids:
                    continue
                items.append(
                    {
                        "at": entry.occurred_at,
                        "source": "matrix",
                        "room_id": room_id,
                        "message": await self._timeline_message_item(entry),
                        # The body doubles as the summary: a recorded message
                        # has no subject to put here, and repeating the body is
                        # honest where inventing a title would not be.
                        "text": entry.body,
                        "repository_id": entry.repository_id,
                        "task_id": None,
                        "payload_ref": f"matrix-event:{entry.event_id}",
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
            tasks = await self._tasks_of(project_id)
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

    def _draft_of(
        self, snapshots: tuple[PlanSnapshotData, ...]
    ) -> PlanSnapshotData | None:
        """The unconsumed snapshot, if this round still has one (v0.4 §2.3).

        ``for_project`` is newest-first, so the first unconsumed row is the
        highest such version.
        """

        return next((s for s in snapshots if s.execution_plan_id is None), None)

    def _discovery_state(
        self, issue_id: UUID, snapshot: PlanSnapshotData | None
    ) -> tuple[int, str, UUID | None]:
        """§3.2's stepper cell and state, derived in exactly one place.

        Both the discovery projection and the issue detail's two badge scalars
        come through here, and the rules themselves live in the producing
        module's contracts — so the panel, the badge and the write side cannot
        end up with three opinions about which step an issue is on.
        """

        block = snapshot.discovery if snapshot is not None else None
        in_flight = (
            self._discovery_tasks.running(issue_id)
            if self._discovery_tasks is not None
            else None
        )
        running_task_id, running_step = in_flight if in_flight else (None, None)
        has_plan = bool(snapshot is not None and snapshot.task_dag)
        step = discovery_step(block)
        state = discovery_step_state(
            block, has_plan=has_plan, running_step=running_step
        )
        return step, state, running_task_id

    async def discovery(self, issue_id: UUID) -> dict | None:
        """Contract v0.4 §3.1: the discovery panel's whole read.

        An issue that exists but never started a chain answers 200 with every
        block null — the same call as v0.2 §7.2's "no team yet is an empty
        list, not a 404". Only an issue with no snapshot at all is a 404.

        Nothing here is summarised or trimmed. Rationales are long and the
        temptation to cut them is real, but this is the text an approver is
        deciding on, and a truncated reason is a reason that cannot be checked.
        """

        snapshots = await self._snapshots.for_project(issue_id)
        if not snapshots:
            return None
        snapshot = self._draft_of(snapshots) or snapshots[0]
        block = snapshot.discovery or {}
        classification = block.get("classification")
        step, state, running_task_id = self._discovery_state(issue_id, snapshot)

        integration = None
        if snapshot.task_dag or snapshot.execution_batches:
            integration = {
                "task_dag_count": len(snapshot.task_dag),
                "batch_count": len(snapshot.execution_batches),
                "contract_count": len(snapshot.contracts),
            }

        return {
            "issue_id": issue_id,
            "plan_version": snapshot.plan_version,
            "step": step,
            "step_state": state,
            "running_task_id": running_task_id,
            "requirement_text": snapshot.requirement_text,
            "analyzed_requirement": (block.get("analysis") or {}).get(
                "analyzed_requirement"
            ),
            "analysis": block.get("analysis"),
            "candidates": block.get("candidates"),
            "classification": classification,
            # The fingerprint of the tiering as it stands, which is what an
            # approval must be submitted against (§5.3). Distinct from
            # ``approval.evidence_version``, which records what a past decision
            # was bound to and is null until someone decides.
            "classification_evidence_version": (
                classification_fingerprint(classification) if classification else None
            ),
            "effective_tiers": effective_tiers(classification),
            "approval": block.get("approval")
            or {
                "state": "not_requested",
                "evidence_version": None,
                "decided_by_agent_id": None,
                "reason": "",
                "decided_at": None,
            },
            "integration": integration,
            "materialization": _materialization_receipt(block),
        }

    async def repository_plan(self, issue_id: UUID, repository_id: UUID) -> dict | None:
        """Contract v0.2 §5.4: the DAG / PLAN / SPEC sheet for one repository.

        The DAG is repository-grained: nodes are the planned repositories,
        layers are execution_batches and edges come from task_dag[].depends_on.

        ``graph_edges`` is still not projected here, but the reason changed with
        the 2026-08-14 single-graph merge: the column is now live (materialize
        writes the plan-layer edges on both the draft and the new-version path),
        it is simply a *different* grain — plan-layer edges carry interface and
        agreement, and are addressed by repository **name**. The console reads
        them separately from ``/plans/{project_id}/versions/{v}`` and uses them
        to annotate the connections this sheet draws, not to draw them. Keep the
        two apart: projecting them here would put candidate edges, which have no
        place in a confirmed topology, into the executed DAG.
        """

        snapshots = await self._snapshots.for_project(issue_id)
        if not snapshots:
            # Two cases here — the issue does not exist, and the issue exists
            # but was never planned — and both are a 404 to the caller. They
            # used to be written as separate branches returning the same
            # value, so the existence query ran for nothing.
            return None
        snapshot = snapshots[0]
        catalog = await self._catalog()
        owned = await self._issue_repository_ids(issue_id)
        id_by_name = self._name_resolver(catalog, owned)

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
        drawn = {
            id_by_name[name]
            for batch in snapshot.execution_batches
            for name in batch
            if id_by_name.get(name) is not None
        }
        if repository_id not in drawn and repository_id not in owned:
            return None

        nodes = []
        unresolved_nodes: list[str] = []
        for batch_index, batch in enumerate(snapshot.execution_batches):
            for name in batch:
                node_id = id_by_name.get(name)
                if node_id is None:
                    # The node stays — dropping it would leave a hole in the
                    # batch and mislay the layout — but §7.2's "never truncate
                    # silently" applies to it just as much as to an edge.
                    unresolved_nodes.append(name)
                nodes.append(
                    {
                        "repository_id": node_id,
                        "name": name,
                        "batch_index": batch_index,
                        "is_focus": node_id is not None and node_id == repository_id,
                    }
                )
        if unresolved_nodes:
            _logger.warning(
                "issue %s repository %s: %d DAG node(s) have no catalog match: %s",
                issue_id,
                repository_id,
                len(unresolved_nodes),
                ", ".join(unresolved_nodes),
            )
        edges = []
        dropped: list[str] = []
        dropped_unresolved = 0
        dropped_off_batch = 0
        for node in snapshot.task_dag:
            target_name = str(node.get("repository", ""))
            target = id_by_name.get(target_name)
            for dependency in node.get("depends_on") or ():
                source = id_by_name.get(str(dependency))
                if source is None or target is None:
                    # A name the catalog cannot resolve is not an edge; an edge
                    # with a null endpoint would draw a line to nowhere.
                    dropped.append(f"{dependency} -> {target_name}")
                    dropped_unresolved += 1
                    continue
                if source not in drawn or target not in drawn:
                    # nodes come from execution_batches and edges from task_dag;
                    # §5.5 reads them as one graph, but nothing made them agree.
                    # An endpoint the layout never drew is a line to an empty
                    # spot on the canvas.
                    dropped.append(f"{dependency} -> {target_name} (not in any batch)")
                    dropped_off_batch += 1
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
                # v0.2 §7.2 left the door open for self-reporting fields and
                # C-2 walked through it: the DAG panel's own contract note
                # says it may be drawing an incomplete graph and must not
                # claim otherwise — but until now nothing gave it a way to
                # tell the user. The drops only reached the log, which the
                # person looking at the picture cannot see.
                #
                # Two edge counts rather than one, because the two causes need
                # different responses: an endpoint the catalog cannot resolve
                # means a missing catalog row, while an endpoint that is in no
                # batch means the planning output disagrees with itself
                # (nodes come from execution_batches, edges from task_dag, and
                # nothing constrains them to agree). A single number would
                # leave "why is an edge missing" unanswerable.
                #
                # Additive and optional for consumers: existing renderers keep
                # working without reading them.
                "unresolved_node_count": len(unresolved_nodes),
                "dropped_edge_unresolved_count": dropped_unresolved,
                "dropped_edge_off_batch_count": dropped_off_batch,
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
                    # Defect A-19: read-only, and empty is the answer that
                    # explains why a repository's rounds verify nothing.
                    "test_commands": list(profile.test_commands),
                    # Defect A-21: the paths those commands read. Shown beside
                    # them because a command without its path is the trap.
                    "test_paths": list(profile.test_paths),
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

        catalog = await self._catalog()
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
                        # Who decomposes this team's tasks (adjudication D-2).
                        # A persisted fact like ``runtime_status`` beside it,
                        # and a third one again from the controller's live
                        # ``runtime`` block below: an operator confirming that
                        # an external Repository Leader was really adopted is
                        # reading a row, not a probe, so it stays truthful with
                        # the controller unreachable.
                        "decomposition_mode": team.decomposition_mode.value,
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

        catalog = await self._catalog()
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

        # The ceiling sits outside the probe, not inside it: waiting for a slot
        # must not spend the timeout budget the probe needs to answer.
        slots = asyncio.Semaphore(self._probe_concurrency)

        async def bounded(kind: str, name: str):
            async with slots:
                return await self._runtime_block(kind, name, shape)

        blocks = await asyncio.gather(
            *(bounded(kind, name) for kind, name in probes),
            return_exceptions=True,
        )
        unreachable = 0
        absent = 0
        for row, block in zip(rows, blocks, strict=True):
            if isinstance(block, BaseException):
                _logger.warning("runtime block failed unexpectedly", exc_info=block)
                block = {"reachable": False}
            if block is None:
                absent += 1
            elif block.get("reachable") is False:
                unreachable += 1
            row["runtime"] = block
        self._report_degradation(len(rows), unreachable, absent)

    def _report_degradation(self, total: int, unreachable: int, absent: int) -> None:
        """One line per page saying how much of it degraded.

        The per-row warnings say a probe failed; none of them says how much of
        the page that adds up to, and the response cannot — it answers 200
        either way, which is the whole point of §4.4. So an entire roster
        coming back unreachable, one agent's worker being gone, and a healthy
        page all look the same to anything watching from outside.

        A log line rather than a counter because this repository has no metrics
        facility — the observability module is still a planned shell, and
        inventing one here would be a larger decision than the gap warrants.
        """

        if self._runtime is None:
            return  # AgentTeams is not configured; nothing degraded, nothing to say
        if not unreachable and not absent:
            return
        _logger.warning(
            "runtime probes degraded: %d/%d unreachable, %d/%d absent (404)",
            unreachable,
            total,
            absent,
            total,
        )

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
            snapshot = await asyncio.wait_for(probe(name), timeout=self._probe_timeout)
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
        catalog = await self._catalog()
        tasks = await self._tasks_of(project_id)

        plan_repository_ids = {planned.repository_id for batch in plan.batches for planned in batch}
        leader_task_ids = {
            planned.leader_task_id
            for batch in plan.batches
            for planned in batch
            if planned.leader_task_id is not None
        }
        worker_tasks = tuple(task for task in tasks if task.parent_task_id in leader_task_ids)
        # §8.7.4: when each of these was last told to do its work. One
        # aggregate query for the whole project, not one per task — see
        # ``PostgresCollaborationMessageStore.last_assignment_at``. It is the
        # context the console's re-dispatch entry needs and refuses to invent:
        # "上次派工 HH:MM" is a fact, "this looks stuck" would not be.
        dispatched_at = await self._messages.last_assignment_at(project_id)
        task_views = await self._task_views(
            worker_tasks, snapshot, change_set, catalog, dispatched_at
        )
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
            # Defect A-19: why this round has no change set, when that is a
            # decision rather than a wait. Null is the ordinary case.
            "delivery_refusal": _delivery_refusal_block(plan, catalog),
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
        dispatched_at: dict[UUID, datetime] | None = None,
    ) -> list[dict]:
        rework_by_key: dict[tuple[UUID, UUID | None], list[TaskView]] = {}
        for task in worker_tasks:
            if task.origin is TaskOrigin.REWORK:
                rework_by_key.setdefault((task.repository_id, task.parent_task_id), []).append(task)

        name_to_task: dict[str, UUID] = {}
        for task in worker_tasks:
            if task.origin is not TaskOrigin.REWORK and task.repository_id in catalog:
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
            is_rework = task.origin is TaskOrigin.REWORK
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
                    "agent": await self._agent_name(task.assignee_agent_id),
                    # §5.2, as an attempt number: the original try is 1 and the
                    # k-th rework on the same (repository, parent) key is 1 + k.
                    # The original row used to report the total instead, so one
                    # original plus two reworks read 3 / 2 / 3 — two rows both
                    # claiming to be the third attempt, and the first try
                    # labelled as the third.
                    "attempt": (2 + chain.index(task)) if is_rework else 1,
                    "depends_on": depends_on,
                    "result_summary": task.result_summary,
                    "evidence": _task_evidence(task),
                    "repair_timeline": repair_timeline,
                    "escalated_to_human": escalated,
                    # §8.7.4. When the assignment message for this task was
                    # last *written*, which is not when the Worker read it —
                    # the row has no delivered_at and inventing one would be
                    # the kind of judgement §3.1 forbids. ``null`` when no
                    # assignment message was ever recorded, which is itself
                    # worth seeing: it means the dispatch never happened.
                    "last_dispatched_at": (dispatched_at or {}).get(task.id),
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

    # -------------------------------------------------------- rollback scope

    async def rollback_scope(self, delivery_id: UUID) -> dict | None:
        """Contract v0.1 §4.6: what a whole-ChangeSet rollback would undo.

        One row per repository, in the order the recovery Saga will act — which
        is reverse merge order, and which this projection does not compute: it
        asks delivery's own recovery planner for the plan it *would* create and
        reads the answer off it. Two implementations of "which repository is
        revert step k" is exactly the drift the read-model rule exists to stop.

        `action` is the machine's word, not the operator's: `withhold` for a
        candidate that never merged (its PR is closed, nothing lands in the
        base branch) and `revert_pull_request` for one that did (a revert PR
        that still has to pass its own CI). Labels are the console's business.
        """

        plan = await self._plans.get(delivery_id)
        if plan is None:
            return None
        change_set = await self._change_sets.for_delivery(delivery_id)
        if change_set is None:
            return {
                "delivery_id": delivery_id,
                "change_set_id": None,
                "available": False,
                "unavailable_reason": "no_change_set",
                "recovery_in_progress": False,
                "repositories": [],
            }
        actions = await self._change_sets.recovery_preview(change_set.id)
        catalog = await self._catalog()
        # A merged repository is planned twice (create then merge the revert
        # PR); the row reports the first of the pair, so `step` is the position
        # at which that repository's rollback starts.
        planned: dict[UUID, RecoveryActionView] = {}
        for action in sorted(actions, key=lambda item: item.sequence):
            if action.repository_id is None or action.kind not in _ROLLBACK_ACTION_KINDS:
                continue
            planned.setdefault(action.repository_id, action)
        repositories = [
            {
                "repository_id": item.repository_id,
                "name": (
                    catalog[item.repository_id].name
                    if item.repository_id in catalog
                    else str(item.repository_id)[:8]
                ),
                "state": (
                    "merged"
                    if item.status is RepositoryDeliveryStatus.MERGED
                    else "unmerged"
                ),
                "action": (
                    _ROLLBACK_ACTION_KINDS[planned[item.repository_id].kind]
                    if item.repository_id in planned
                    else "none"
                ),
                "step": (
                    planned[item.repository_id].sequence
                    if item.repository_id in planned
                    else None
                ),
                "merge_sha": item.merge_sha,
                "pull_request_number": item.pull_request_number,
            }
            for item in sorted(change_set.repositories, key=lambda r: r.merge_order)
        ]
        active = change_set.recovery_plans[-1] if change_set.recovery_plans else None
        in_progress = active is not None and any(
            action.status not in _TERMINAL_ACTION_STATUSES for action in active.actions
        )
        available = any(row["action"] != "none" for row in repositories)
        return {
            "delivery_id": delivery_id,
            "change_set_id": change_set.id,
            "available": available,
            "unavailable_reason": None if available else "nothing_delivered",
            "recovery_in_progress": in_progress,
            "repositories": repositories,
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
        candidates = await self._validations_of(project_id)
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

    ``kind`` says who runs this member: "container" when the controller
    confirms it owns one, "external" when it confirms it does not, and null
    when the probe never asked (the manager probe carries no such field).
    """

    addressing = {
        "matrix_user_id": snapshot.matrix_user_id,
        "room_id": snapshot.room_id,
        "message": snapshot.message,
        "awake": None,
        "uptime_seconds": None,
    }
    if snapshot.container_managed is False:
        # phase and runtime_kind are container lifecycle words, and the
        # controller keeps emitting them for a member whose container it will
        # never start: an unset phase comes back "Pending", which reads as
        # "wait, it is coming up". For a confirmed external member those two
        # columns have no subject at all, so they are null here rather than
        # the controller's defaults.
        return {"kind": "external", "phase": None, "runtime_kind": None, **addressing}
    return {
        "kind": "container" if snapshot.container_managed else None,
        "phase": snapshot.phase,
        "runtime_kind": snapshot.runtime_kind,
        **addressing,
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


def _delivery_refusal_block(plan, catalog: dict) -> dict | None:
    """The delivering side's stated refusal for this round, or None (A-19).

    Names the repository so the panel can point at one row rather than at the
    round; falls back to the id when the catalog does not know it, and to null
    when the refusal itself names no repository. ``reason`` is passed through
    verbatim — the projection's job is to carry the server's words to the
    operator, not to phrase them.
    """

    refusal = plan.delivery_refusal
    if refusal is None:
        return None
    repository_id = refusal.repository_id
    name = None
    if repository_id is not None:
        name = (
            catalog[repository_id].name if repository_id in catalog else str(repository_id)[:8]
        )
    return {
        "reason": refusal.reason,
        "batch_index": refusal.batch_index,
        "repository_id": repository_id,
        "repository_name": name,
        "task_id": refusal.task_id,
        "at": refusal.at,
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


def _materialization_receipt(block: dict) -> dict | None:
    """§8.3's materialization receipt, as much of it as a client may see.

    Projected because of defect B-12: materialize has been re-entrant since
    7659c89 — a round that half-executed can be finished by calling it again —
    but the receipt saying a round *did* half-execute never reached the panel,
    so the GUI had no way to know a retry was warranted and no entry to trigger
    one. The receipt is the honest signal; it was simply not being carried.

    Four of the receipt's fields are deliberately withheld. ``idempotency_key``,
    ``prefix`` and ``plan_fingerprint`` are the server's replay bookkeeping:
    handing them out invites a client to mint a key that collides with a real
    one, and §8.3's whole guarantee rests on the server owning that namespace.
    The success-only fields (``task_ids`` / ``team_count`` / ``repositories`` /
    ``skipped_repos``) are omitted because what they describe is already
    projected as rounds, teams and rooms — a second copy is a second chance to
    disagree.

    What is left is projected verbatim, ``error`` included and untouched: the
    panel shows the server's words. No judgment is derived here — no "stuck",
    no "retryable". The reader decides from ``status`` alone, because the moment
    this function invents a distinction, the panel is rendering our opinion of
    the failure rather than the failure.
    """

    receipt = block.get("materialization")
    if not receipt:
        return None
    return {
        "status": receipt.get("status"),
        "at": receipt.get("at"),
        "by_agent_id": receipt.get("by_agent_id"),
        # Absent by construction on the failure path (`_record_failure` writes
        # no `plan_id`), so `.get` is the honest read, not a defensive one.
        "error": receipt.get("error"),
        "plan_id": receipt.get("plan_id"),
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


def _task_evidence(task: TaskView) -> dict | None:
    """§3 tasks[].evidence: what the coding agent said about its own run (A-18).

    All of this was already in ``result_summary`` -- as a JSON string the GUI
    received and never opened -- so a task whose agent wrote "Nothing was
    executed ... Please re-run before merging" rendered as a green success, and
    with delivery_auto on, nobody read it before the merge.

    This block only transcribes ``TaskEvidenceView``. In particular ``verified``
    is the producing module's property, not a judgement made here: the read
    model has no second opinion about whether a run checked its own work.

    ``None`` when the task carries no structured evidence, which is a real
    shape (superseded tasks, plain-prose reports, anything pre-Runner) and must
    not be flattened into ``verified: false`` -- "we do not know" and "it did
    not verify" are different claims and the GUI shows them differently.
    """

    evidence = task.evidence
    if evidence is None:
        return None
    return {
        "verified": evidence.verified,
        "blockers": list(evidence.blockers),
        "summary_text": evidence.summary_text,
        "test_command": evidence.test_command,
        "test_results": [
            {
                "command": result.command,
                "exit_code": result.exit_code,
                "summary": result.summary,
            }
            for result in evidence.test_results
        ],
        "artifact_count": evidence.artifact_count,
    }


def _diffs(worker_tasks: tuple[TaskView, ...]) -> list[dict]:
    """§3 diffs[]: the Runner evidence task orchestration declares per task.

    This used to json.loads(result_summary) here — parsing structure out of a
    field the producer only ever declared as free text. The producer now owns
    that parse and publishes TaskEvidenceView, so a task with no structured
    evidence says so instead of being rescued by a JSONDecodeError handler.

    The ``commit_sha is not None`` filter is not defensive padding: since A-18's
    fourth face, evidence survives a failed run and its commit is null. §3
    declares ``diffs[].commit_sha`` as a string, and a diff is a *change that
    exists* — a run that never committed produced none. Reporting it with a
    null head would put a row in diffs[] pointing at nothing.
    """

    return [
        {
            "repository_id": task.repository_id,
            "run_id": task.evidence.run_id,
            "commit_sha": task.evidence.commit_sha,
            "changed_files": list(task.evidence.changed_files),
            "diffstat": None,
        }
        for task in worker_tasks
        if task.status is TaskStatus.SUCCEEDED
        and task.evidence is not None
        and task.evidence.commit_sha is not None
    ]


__all__ = [
    "DeliveryReadModelService",
    "DeliveryPhase",
    "GateDisplay",
]
