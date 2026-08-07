"""Plan execution bridge.

Consumes an :class:`IntegratedPlan` produced by :class:`PlanIntegrationService`
and materialises it into the *specification* and *task_orchestration* modules:

- Engineering Spec → ``SpecificationService.create(kind=ENGINEERING)``
- Each Contract   → ``SpecificationService.create(kind=CONTRACT)``
- The batched task DAG → one execution plan started through
  :class:`ExecutionPlanStarter`

Only the first batch is assigned when the plan starts: the execution plan owns
batch progression, so later batches are assigned by *task_orchestration* once
the Runner reports the previous batch as terminal.  When no execution plane is
configured (no Matrix messenger, therefore no task orchestrator) the bridge
still creates the specifications and reports every repository as skipped.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol
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
    TaskView,
)
from repomesh.telemetry import SpanAttributes, traced

from .plan_integration import IntegratedPlan, TaskNode

_logger = logging.getLogger(__name__)


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
    ) -> None:
        self._specs = specifications
        self._plans = plans
        self._topologies = topologies
        self._catalog = catalog

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
        """

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

        if self._plans is None:
            _logger.info("TaskOrchestrator not available, skipping task assignment")
            skipped.extend(t.repository for t in plan.task_dag)
        else:
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
        span.set_attribute("repomesh.materialize.contract_spec_count", len(contract_specs))
        span.set_attribute("repomesh.materialize.task_count", len(tasks_created))
        span.set_attribute("repomesh.materialize.skipped_repos", list(skipped))
        return MaterializationResult(
            engineering_spec=eng_spec,
            contract_specs=contract_specs,
            tasks=tasks_created,
            skipped_repos=skipped,
            plan_id=plan_id,
        )

    # ------------------------------------------------------------------
    # Private helpers
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
