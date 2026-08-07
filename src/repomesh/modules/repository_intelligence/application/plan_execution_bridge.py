"""Plan execution bridge.

Consumes an :class:`IntegratedPlan` produced by :class:`PlanIntegrationService`
and materialises it into the *specification* and *task_orchestration* modules:

- Engineering Spec → ``SpecificationService.create(kind=ENGINEERING)``
- Each Contract   → ``SpecificationService.create(kind=CONTRACT)``
- Each TaskNode   → ``TaskOrchestrator.assign()``

The bridge is intentionally **non-blocking** in MVP: all tasks are assigned
up-front.  Batch-level wait logic (wait for batch N to finish before
assigning batch N+1) is left for a later iteration.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from repomesh.modules.project.contracts import ProjectTopologyReader
from repomesh.modules.repository_intelligence.ports.catalog import RepositoryCatalog
from repomesh.modules.specification.contracts import (
    CreateSpecificationCommand,
    SpecificationKind,
    SpecificationView,
)
from repomesh.modules.task_orchestration.contracts import (
    AssignTaskCommand,
    TaskView,
)

from .plan_integration import IntegratedPlan, TaskNode

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocols (decouple from SpecificationService / TaskOrchestrator)
# ---------------------------------------------------------------------------


class SpecificationCreator(Protocol):
    """Subset of SpecificationService needed by the bridge."""

    async def create(
        self, command: CreateSpecificationCommand, *, idempotency_key: str
    ) -> SpecificationView: ...


class TaskAssigner(Protocol):
    """Subset of TaskOrchestrator needed by the bridge."""

    async def assign(
        self, command: AssignTaskCommand, *, idempotency_key: str
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

        bridge = PlanExecutionBridge(specs, tasks, topologies)
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
        tasks: TaskAssigner | None,
        topologies: ProjectTopologyReader,
        catalog: RepositoryCatalog,
    ) -> None:
        self._specs = specifications
        self._tasks = tasks
        self._topologies = topologies
        self._catalog = catalog

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

        # --- 4. Assign Tasks ---------------------------------------------------
        tasks_created: list[TaskView] = []
        skipped: list[str] = []

        if self._tasks is None:
            _logger.info("TaskOrchestrator not available, skipping task assignment")
            skipped.extend(t.repository for t in plan.task_dag)
        else:
            for batch_index, batch in enumerate(plan.execution_batches):
                for repo_name in batch:
                    # Find the matching task node
                    task_node = self._find_task(plan, repo_name)
                    if task_node is None:
                        _logger.warning("No task node for %s, skipping", repo_name)
                        skipped.append(repo_name)
                        continue

                    # Resolve repo name → repository_id → leader_agent_id
                    repo_id = name_to_repo_id.get(repo_name)
                    if repo_id is None:
                        _logger.warning(
                            "Repository %s not found in catalog, skipping task",
                            repo_name,
                        )
                        skipped.append(repo_name)
                        continue

                    team = repo_id_to_team.get(repo_id)
                    if team is None:
                        _logger.warning(
                            "Repository %s (id=%s) has no team in topology, skipping",
                            repo_name, repo_id,
                        )
                        skipped.append(repo_name)
                        continue

                    task = await self._tasks.assign(
                        AssignTaskCommand(
                            organization_id=org_id,
                            project_id=project_id,
                            repository_id=repo_id,
                            assigned_by_agent_id=leader_agent_id,
                            assignee_agent_id=team.leader_agent_id,
                            title=f"Implement changes for {repo_name}",
                            instruction=task_node.instruction
                            or f"Implement changes for {repo_name}",
                            acceptance=self._derive_task_acceptance(task_node),
                        ),
                        idempotency_key=f"{idempotency_prefix}-task-{repo_name}-b{batch_index}",
                    )
                    tasks_created.append(task)
                    _logger.info(
                        "Assigned task %s for %s (batch %d)",
                        task.id, repo_name, batch_index,
                    )

        return MaterializationResult(
            engineering_spec=eng_spec,
            contract_specs=contract_specs,
            tasks=tasks_created,
            skipped_repos=skipped,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

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
