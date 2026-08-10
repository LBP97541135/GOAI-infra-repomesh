"""Plan execution bridge.

Consumes an :class:`IntegratedPlan` produced by :class:`PlanIntegrationService`
and materialises it into the *specification* and *task_orchestration* modules:

- Engineering Spec → ``SpecificationService.create(kind=ENGINEERING)``
- Each Contract   → ``SpecificationService.create(kind=CONTRACT)``
- The batched task DAG → one execution plan started through
  :class:`ExecutionPlanStarter`

Only the first batch is assigned when the plan starts: the execution plan owns
batch progression, so later batches are assigned by *task_orchestration* once
the Runner reports the previous batch as terminal.

Fail-closed: when no execution plane is configured (no Matrix messenger,
therefore no task orchestrator) ``materialize`` raises
:class:`ExecutionPlaneUnavailable` **before any side effect** instead of
silently creating specs-only output — a 200 response whose ``task_ids`` is
empty proved indistinguishable from success in live use.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from opentelemetry import trace

from repomesh.modules.project.contracts import ProjectTopologyReader
from repomesh.modules.repository_intelligence.ports.catalog import RepositoryCatalog
from repomesh.modules.specification.contracts import (
    CreateSpecificationCommand,
    SpecificationKind,
    SpecificationView,
)
from repomesh.modules.task_orchestration.contracts import (
    ExecutionPlanView,
    PlannedRepositoryTaskView,
    SupersedeTaskCommand,
    TaskView,
)
from repomesh.telemetry import SpanAttributes, traced

from .plan_integration import IntegratedPlan, TaskNode

if TYPE_CHECKING:
    from repomesh.modules.repository_intelligence.application.confirmation import (
        ConfirmationSummary,
    )
    from repomesh.modules.repository_intelligence.application.dependency_graph import (
        DependencyGraphService,
    )
    from repomesh.modules.repository_intelligence.application.plan_integration import (
        PlanIntegrationService,
    )

_logger = logging.getLogger(__name__)


class ExecutionPlaneUnavailable(RuntimeError):
    """The task orchestration plane is not configured; materialization refused."""


# ---------------------------------------------------------------------------
# Protocols (decouple from SpecificationService / AdvanceExecutionPlan)
# ---------------------------------------------------------------------------


class SpecificationCreator(Protocol):
    """Subset of SpecificationService needed by the bridge."""

    async def create(
        self, command: CreateSpecificationCommand, *, idempotency_key: str
    ) -> SpecificationView: ...


@dataclass(frozen=True, slots=True)
class StartedExecutionPlan:
    """The execution plan created for a materialised :class:`IntegratedPlan`."""

    plan: ExecutionPlanView
    tasks: tuple[TaskView, ...] = ()


class ExecutionPlanStarter(Protocol):
    """Start a batched execution plan owned by *task_orchestration*.

    The composition root adapts ``AdvanceExecutionPlan`` to this port so the
    bridge never has to build another module's aggregate.
    """

    async def start_plan(
        self,
        *,
        organization_id: UUID,
        project_id: UUID,
        created_by_agent_id: UUID,
        batches: Sequence[Sequence[PlannedRepositoryTaskView]],
        idempotency_key: str,
    ) -> StartedExecutionPlan: ...


class PlanSnapshotWriter(Protocol):
    """Subset of PlanSnapshotStore needed by the bridge for DAG observability."""

    async def next_version(self, project_id: UUID) -> int: ...

    async def save(
        self,
        *,
        project_id: UUID,
        plan_version: int,
        engineering_spec: str,
        contracts: list[dict],
        task_dag: list[dict],
        execution_batches: list[list[str]],
        graph_edges: list[dict],
        created_by_agent_id: UUID | None = ...,
        execution_plan_id: UUID | None = ...,
        requirement_text: str | None = ...,
        integration_method: str | None = ...,
    ) -> object: ...


class TaskSupersederGateway(Protocol):
    """Cancel/supersede a task that has been replaced by a newer plan version."""

    async def supersede(
        self, command: SupersedeTaskCommand, *, idempotency_key: str
    ) -> TaskView: ...


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MaterializationResult:
    """Outcome of :meth:`PlanExecutionBridge.materialize`."""

    engineering_spec: SpecificationView
    contract_specs: list[SpecificationView] = field(default_factory=list)
    tasks: list[TaskView] = field(default_factory=list)
    skipped_repos: list[str] = field(default_factory=list)
    plan_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ReplanResult:
    """Outcome of :meth:`PlanExecutionBridge.replan`."""

    new_plan_version: int
    superseded_tasks: list[TaskView] = field(default_factory=list)
    new_tasks: list[TaskView] = field(default_factory=list)
    affected_repos: list[str] = field(default_factory=list)
    feedback_summary: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _truncate(text: str, limit: int = 60) -> str:
    """Return *text* truncated to *limit* chars with an ellipsis."""

    text = text.strip()
    return text[:limit] + "…" if len(text) > limit else text


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------


class PlanExecutionBridge:
    """Materialise an :class:`IntegratedPlan` into specs and tasks.

    Usage::

        bridge = PlanExecutionBridge(specs, plans, topologies, catalog)
        result = await bridge.materialize(
            plan=plan,
            requirement="fix notification email bug",
            project_id=project_uuid,
            leader_agent_id=leader_uuid,
            idempotency_prefix="tt-001",
        )
    """

    def __init__(
        self,
        specifications: SpecificationCreator,
        plans: ExecutionPlanStarter | None,
        topologies: ProjectTopologyReader,
        catalog: RepositoryCatalog,
        snapshot_store: PlanSnapshotWriter | None = None,
        superseder: TaskSupersederGateway | None = None,
    ) -> None:
        self._specs = specifications
        self._plans = plans
        self._topologies = topologies
        self._catalog = catalog
        self._snapshots = snapshot_store
        self._superseder = superseder

    @traced("planning.materialize")
    async def materialize(
        self,
        plan: IntegratedPlan,
        requirement: str,
        project_id: UUID,
        leader_agent_id: UUID,
        *,
        idempotency_prefix: str,
    ) -> MaterializationResult:
        """Create specs and tasks from *plan*.

        Args:
            plan: The integrated plan from PlanIntegrationService.
            requirement: Original requirement text.
            project_id: Target project UUID.
            leader_agent_id: ORG_LEADER agent UUID.
            idempotency_prefix: Unique prefix for idempotency keys
                (e.g. ``"tt-001"``).

        Returns:
            :class:`MaterializationResult` with created specs and tasks.

        Raises:
            ExecutionPlaneUnavailable: when no task orchestrator is configured.
                Raised before any spec is created so a refused materialization
                leaves no partial state behind.
        """

        # --- 0. Fail closed before any side effect ----------------------------
        if self._plans is None:
            raise ExecutionPlaneUnavailable(
                "task orchestration plane is not configured (no assignment "
                "gateway — is the Matrix messenger set up?); refusing to "
                "materialize a plan whose tasks cannot be assigned"
            )

        # --- 1. Load topology --------------------------------------------------
        topology = await self._topologies.get_view(project_id)
        if topology is None:
            raise ValueError(f"Project topology not found: {project_id}")

        org_id = topology.organization_id

        # --- 1b. Build name → UUID mappings -----------------------------------
        # catalog has RepositoryProfile(name, id) — gives us name → repository_id
        # topology has RepositoryTeamView(repository_id, leader_agent_id)
        profiles = await self._catalog.list()
        name_to_repo_id = {p.name: p.id for p in profiles}
        repo_id_to_team = {t.repository_id: t for t in topology.repository_teams}

        # --- 2. Create Engineering Spec ---------------------------------------
        eng_spec = await self._specs.create(
            CreateSpecificationCommand(
                organization_id=org_id,
                project_id=project_id,
                kind=SpecificationKind.ENGINEERING,
                created_by_agent_id=leader_agent_id,
                title=_truncate(requirement, 80),
                goal=plan.engineering_spec or requirement,
                acceptance=self._derive_acceptance(plan),
                scope=tuple(t.repository for t in plan.task_dag),
                dependencies=(),
                interface_changes=tuple(
                    f"{c.producer}→{c.consumer}: {c.interface}" for c in plan.contracts
                ),
            ),
            idempotency_key=f"{idempotency_prefix}-eng-spec",
        )
        _logger.info("Created Engineering Spec %s", eng_spec.id)

        # --- 3. Create Contract Specs -----------------------------------------
        contract_specs: list[SpecificationView] = []
        for i, contract in enumerate(plan.contracts):
            cs = await self._specs.create(
                CreateSpecificationCommand(
                    organization_id=org_id,
                    project_id=project_id,
                    kind=SpecificationKind.CONTRACT,
                    created_by_agent_id=leader_agent_id,
                    title=f"{contract.producer} → {contract.consumer}: {contract.interface}",
                    goal=contract.agreement,
                    acceptance=(
                        f"{contract.consumer} must adapt to {contract.producer}'s "
                        f"changes on {contract.interface}",
                    ),
                    scope=(contract.producer, contract.consumer),
                    dependencies=(),
                    interface_changes=(contract.interface,),
                ),
                idempotency_key=f"{idempotency_prefix}-contract-{i}",
            )
            contract_specs.append(cs)
            _logger.info(
                "Created Contract Spec %s for %s→%s",
                cs.id, contract.producer, contract.consumer,
            )

        # --- 4. Start the execution plan --------------------------------------
        tasks_created: list[TaskView] = []
        skipped: list[str] = []
        plan_id: UUID | None = None

        batches = self._plan_batches(
            plan,
            name_to_repo_id=name_to_repo_id,
            teamed_repository_ids=set(repo_id_to_team),
            skipped=skipped,
        )
        if batches:
            started = await self._plans.start_plan(
                organization_id=org_id,
                project_id=project_id,
                created_by_agent_id=leader_agent_id,
                batches=batches,
                idempotency_key=f"{idempotency_prefix}-plan",
            )
            plan_id = started.plan.id
            tasks_created.extend(started.tasks)
            _logger.info(
                "Started execution plan %s with %d batch(es), current batch %d",
                started.plan.id,
                len(started.plan.batches),
                started.plan.current_batch_index,
            )
        else:
            _logger.info("No executable repository in the plan, nothing to schedule")

        span = trace.get_current_span()
        span.set_attribute(SpanAttributes.PROJECT_ID, str(project_id))
        span.set_attribute(
            "repomesh.materialize.contract_spec_count", len(contract_specs)
        )
        span.set_attribute("repomesh.materialize.task_count", len(tasks_created))
        span.set_attribute("repomesh.materialize.skipped_repos", list(skipped))

        # --- 5. Save plan snapshot (if store configured) ---------------------
        if self._snapshots is not None:
            try:
                version = await self._snapshots.next_version(project_id)
                await self._snapshots.save(
                    project_id=project_id,
                    plan_version=version,
                    engineering_spec=plan.engineering_spec or requirement,
                    contracts=[
                        c.to_dict() if hasattr(c, "to_dict") else dict(c)
                        for c in plan.contracts
                    ],
                    task_dag=[
                        t.to_dict() if hasattr(t, "to_dict") else dict(t)
                        for t in plan.task_dag
                    ],
                    execution_batches=[list(b) for b in plan.execution_batches],
                    graph_edges=[],
                    created_by_agent_id=leader_agent_id,
                    execution_plan_id=plan_id,
                    requirement_text=requirement,
                    integration_method="llm_only",
                )
            except Exception:
                _logger.warning("Failed to save plan snapshot", exc_info=True)

        return MaterializationResult(
            engineering_spec=eng_spec,
            contract_specs=contract_specs,
            tasks=tasks_created,
            skipped_repos=skipped,
            plan_id=plan_id,
        )

    @traced("planning.replan")
    async def replan(
        self,
        *,
        project_id: UUID,
        leader_agent_id: UUID,
        feedback: str,
        change_source_repo: str,
        plan_version: int,
        requirement: str,
        idempotency_prefix: str,
        all_repos: list[str],
        integration_service: PlanIntegrationService | None = None,
        confirmation_summary: ConfirmationSummary | None = None,
        graph: DependencyGraphService | None = None,
    ) -> ReplanResult:
        """Partially replan after a BLOCKED task reports an upstream change.

        Flow:

        1. **Impact analysis** — use the dependency graph's reverse edges to
           find every repository that depends on *change_source_repo*. The
           affected set always contains the change source itself.
        2. **Local re-integration** (optional) — when an *integration_service*
           is supplied, re-run plan integration scoped to the affected repos
           with a stability constraint appended to the requirement. This is a
           best-effort step; the framework still works without it.
        3. **Version migration** — supersede the old tasks of the affected
           repositories so the Runner can interrupt them, then (when a plan
           starter is configured) start a new execution plan for the new batch.
        4. **Notification** — record which Team Managers must be interrupted.
           The actual collaboration push is performed by the API layer, which
           owns the :class:`CollaborationGateway`; the bridge only reports the
           affected repos so callers know who to notify.

        Args:
            project_id: Target project UUID.
            leader_agent_id: ORG_LEADER agent UUID authorising the replan.
            feedback: BLOCKED task feedback explaining the upstream change.
            change_source_repo: Repository whose change triggered the replan.
            plan_version: Current plan version being superseded.
            requirement: Original requirement text (feedback is appended).
            idempotency_prefix: Unique prefix for idempotency keys.
            all_repos: Every repository in the current plan (for scoping).
            integration_service: Optional service for local re-integration.
            confirmation_summary: Optional prior confirmation to reuse.
            graph: Dependency graph for impact analysis.

        Returns:
            :class:`ReplanResult` with superseded/new tasks and affected repos.

        Raises:
            ExecutionPlaneUnavailable: when no superseder is configured. Raised
                before any side effect so a refused replan leaves no state.
        """

        # --- 0. Fail closed before any side effect ----------------------------
        if self._superseder is None:
            raise ExecutionPlaneUnavailable(
                "task orchestration plane is not configured (no superseder "
                "gateway — is the Matrix messenger set up?); refusing to "
                "replan when tasks cannot be superseded"
            )

        # --- 1. Impact analysis ------------------------------------------------
        affected_repos = self._compute_affected_repos(
            change_source_repo=change_source_repo,
            all_repos=all_repos,
            graph=graph,
        )
        _logger.info(
            "replan: change_source=%s affected_repos=%s",
            change_source_repo,
            affected_repos,
        )

        # --- 2. Local re-integration (optional, best effort) ------------------
        new_plan: IntegratedPlan | None = None
        if integration_service is not None and confirmation_summary is not None:
            try:
                new_plan = self._local_replan(
                    integration_service=integration_service,
                    confirmation_summary=confirmation_summary,
                    requirement=requirement,
                    feedback=feedback,
                    affected_repos=affected_repos,
                )
            except Exception:
                _logger.warning(
                    "replan: local re-integration failed, "
                    "falling back to cancel-only",
                    exc_info=True,
                )
                new_plan = None

        # --- 3. Version migration: supersede old tasks of affected repos -------
        new_plan_version = plan_version + 1
        superseded_tasks = await self._supersede_affected_tasks(
            project_id=project_id,
            affected_repos=affected_repos,
            feedback=feedback,
            idempotency_prefix=idempotency_prefix,
        )

        # --- 3b. Start a new execution plan for the affected repos ------------
        new_tasks: list[TaskView] = []
        if new_plan is not None and self._plans is not None:
            new_tasks = await self._start_replan_batch(
                new_plan=new_plan,
                affected_repos=affected_repos,
                project_id=project_id,
                leader_agent_id=leader_agent_id,
                idempotency_prefix=idempotency_prefix,
                new_plan_version=new_plan_version,
            )

        # --- 4. Notification: record who needs to be interrupted --------------
        # The bridge does not own a CollaborationGateway; the API layer reads
        # ``affected_repos`` from the result and pushes the interrupt notices.
        if superseded_tasks:
            _logger.info(
                "replan: %d task(s) superseded; Team Managers for %s must be "
                "notified of the interruption",
                len(superseded_tasks),
                affected_repos,
            )

        span = trace.get_current_span()
        span.set_attribute(SpanAttributes.PROJECT_ID, str(project_id))
        span.set_attribute("repomesh.replan.new_plan_version", new_plan_version)
        span.set_attribute(
            "repomesh.replan.superseded_task_count", len(superseded_tasks)
        )
        span.set_attribute("repomesh.replan.new_task_count", len(new_tasks))
        span.set_attribute("repomesh.replan.affected_repos", list(affected_repos))

        feedback_summary = _truncate(feedback, limit=200)
        return ReplanResult(
            new_plan_version=new_plan_version,
            superseded_tasks=superseded_tasks,
            new_tasks=new_tasks,
            affected_repos=list(affected_repos),
            feedback_summary=feedback_summary,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_affected_repos(
        *,
        change_source_repo: str,
        all_repos: list[str],
        graph: DependencyGraphService | None,
    ) -> list[str]:
        """Return the affected repository set for a change source.

        The set is ``{change_source_repo}`` plus every consumer that depends
        on it (reverse dependencies). When no graph is available, the set
        collapses to the change source itself — the minimal safe assumption.
        """

        known = set(all_repos)
        affected: set[str] = set()
        if change_source_repo in known or not known:
            affected.add(change_source_repo)

        if graph is None:
            return sorted(affected)

        for edge in graph.reverse_dependencies(change_source_repo):
            # ``edge.consumer`` depends on the change source → it is affected.
            if not known or edge.consumer in known:
                affected.add(edge.consumer)
        return sorted(affected)

    @staticmethod
    def _local_replan(
        *,
        integration_service: PlanIntegrationService,
        confirmation_summary: ConfirmationSummary,
        requirement: str,
        feedback: str,
        affected_repos: list[str],
    ) -> IntegratedPlan:
        """Re-run plan integration scoped to the affected repositories.

        The feedback is appended to the requirement with a stability
        constraint so the LLM keeps the unaffected repositories stable.
        """

        scoped_requirement = (
            f"{requirement}\n\n"
            f"Replan feedback (scope: {', '.join(affected_repos)}): {feedback}\n"
            "Stability constraint: repositories outside the affected set must "
            "not change their public contracts."
        )
        return integration_service.integrate(scoped_requirement, confirmation_summary)

    async def _supersede_affected_tasks(
        self,
        *,
        project_id: UUID,
        affected_repos: list[str],
        feedback: str,
        idempotency_prefix: str,
    ) -> list[TaskView]:
        """Supersede the active tasks of the affected repositories.

        Tasks are discovered through the catalog (name → repository id) and
        the task store is not directly accessible from the bridge, so this
        helper relies on the caller having already identified the tasks via
        the snapshot/execution plan. In the current framework stage it
        records intent and returns an empty list when no tasks can be
        resolved here; the API layer passes concrete task ids when available.
        """

        # The bridge intentionally does not hold a TaskStore reference (it
        # would cross module boundaries). Concrete task discovery happens in
        # the API layer; here we only emit the structured log so operators
        # can trace which repos were targeted.
        _logger.info(
            "replan: superseding tasks for repos=%s project=%s reason=%s",
            affected_repos,
            project_id,
            _truncate(feedback, limit=120),
        )
        return []

    async def _start_replan_batch(
        self,
        *,
        new_plan: IntegratedPlan,
        affected_repos: list[str],
        project_id: UUID,
        leader_agent_id: UUID,
        idempotency_prefix: str,
        new_plan_version: int,
    ) -> list[TaskView]:
        """Start a new execution plan batch for the locally re-planned repos."""

        topology = await self._topologies.get_view(project_id)
        if topology is None:
            _logger.warning("replan: topology not found for %s", project_id)
            return []

        org_id = topology.organization_id
        profiles = await self._catalog.list()
        name_to_repo_id = {p.name: p.id for p in profiles}
        repo_id_to_team = {t.repository_id: t for t in topology.repository_teams}

        skipped: list[str] = []
        batches = self._plan_batches(
            new_plan,
            name_to_repo_id=name_to_repo_id,
            teamed_repository_ids=set(repo_id_to_team),
            skipped=skipped,
        )
        # Keep only batches that touch an affected repository.
        affected_set = set(affected_repos)
        scoped_batches = tuple(
            batch
            for batch in batches
            if any(self._repo_name_for_task(planned, profiles) in affected_set
                   for planned in batch)
        )
        if not scoped_batches or self._plans is None:
            return []

        started = await self._plans.start_plan(
            organization_id=org_id,
            project_id=project_id,
            created_by_agent_id=leader_agent_id,
            batches=scoped_batches,
            idempotency_key=f"{idempotency_prefix}-replan-v{new_plan_version}",
        )
        _logger.info(
            "replan: started new execution plan %s with %d batch(es)",
            started.plan.id,
            len(started.plan.batches),
        )
        return list(started.tasks)

    @staticmethod
    def _repo_name_for_task(
        planned: PlannedRepositoryTaskView,
        profiles: list,
    ) -> str:
        """Resolve a planned task's repository id back to its name."""

        for profile in profiles:
            if profile.id == planned.repository_id:
                return profile.name
        return ""

    # ------------------------------------------------------------------
    # Private helpers (materialize)
    # ------------------------------------------------------------------

    def _plan_batches(
        self,
        plan: IntegratedPlan,
        *,
        name_to_repo_id: dict[str, UUID],
        teamed_repository_ids: set[UUID],
        skipped: list[str],
    ) -> tuple[tuple[PlannedRepositoryTaskView, ...], ...]:
        """Translate the batched task DAG into planned repository tasks.

        Repositories the platform cannot execute (unknown to the catalog, or
        without a repository team in the project topology) are logged and
        collected in *skipped* instead of entering the execution plan: an
        execution plan must only contain assignable work.
        """

        batches: list[tuple[PlannedRepositoryTaskView, ...]] = []
        for batch_index, batch in enumerate(plan.execution_batches):
            planned: list[PlannedRepositoryTaskView] = []
            for repo_name in batch:
                task_node = self._find_task(plan, repo_name)
                if task_node is None:
                    _logger.warning("No task node for %s, skipping", repo_name)
                    skipped.append(repo_name)
                    continue

                repo_id = name_to_repo_id.get(repo_name)
                if repo_id is None:
                    _logger.warning(
                        "Repository %s not found in catalog, skipping task", repo_name
                    )
                    skipped.append(repo_name)
                    continue

                if repo_id not in teamed_repository_ids:
                    _logger.warning(
                        "Repository %s (id=%s) has no team in topology, skipping",
                        repo_name,
                        repo_id,
                    )
                    skipped.append(repo_name)
                    continue

                planned.append(
                    PlannedRepositoryTaskView(
                        repository_id=repo_id,
                        title=f"Implement changes for {repo_name}",
                        instruction=task_node.instruction
                        or f"Implement changes for {repo_name}",
                        acceptance=self._derive_task_acceptance(task_node),
                        leader_task_id=None,
                        tests=task_node.tests,
                    )
                )
                _logger.info("Planned repository task for %s (batch %d)", repo_name, batch_index)
            if planned:
                batches.append(tuple(planned))
        return tuple(batches)

    @staticmethod
    def _find_task(plan: IntegratedPlan, repo_name: str) -> TaskNode | None:
        for t in plan.task_dag:
            if t.repository == repo_name:
                return t
        return None

    @staticmethod
    def _derive_acceptance(plan: IntegratedPlan) -> tuple[str, ...]:
        """Derive acceptance criteria from the plan."""

        criteria: list[str] = []
        for t in plan.task_dag:
            if t.instruction:
                criteria.append(f"{t.repository}: {t.instruction}")
        if not criteria:
            criteria.append("All changes compile and tests pass.")
        return tuple(criteria)

    @staticmethod
    def _derive_task_acceptance(task_node: TaskNode) -> tuple[str, ...]:
        """Derive acceptance criteria for a single task."""

        criteria = ["Code compiles without errors.", "Existing tests pass."]
        if task_node.instruction:
            criteria.append(task_node.instruction)
        return tuple(criteria)
