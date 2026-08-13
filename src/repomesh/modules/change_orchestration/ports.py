"""Ports consumed by the cross-module change workflow."""

from collections.abc import Mapping, Sequence
from typing import Any, Protocol
from uuid import UUID

from repomesh.modules.repository_intelligence.contracts import IntegratedPlan, PlanGraph
from repomesh.modules.specification.contracts import CreateSpecificationCommand, SpecificationView
from repomesh.modules.task_orchestration.contracts import (
    PlannedRepositoryTaskView,
    ProjectTaskReader,
    SupersedeTaskCommand,
    TaskView,
)

from .contracts import StartedExecutionPlan


class SpecificationCreator(Protocol):
    async def create(
        self, command: CreateSpecificationCommand, *, idempotency_key: str
    ) -> SpecificationView: ...


class ExecutionPlanStarter(Protocol):
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


class PlanSnapshotReader(Protocol):
    """Read access to the latest immutable plan-layer snapshot.

    The plan-layer graph is the single source of truth for replan impact
    analysis: its confirmed edges drive the affected set exactly, unlike the
    world-layer scan graph which also carries candidate edges.
    """

    async def get_latest_graph(self, project_id: UUID) -> PlanGraph | None: ...


class TaskSupersederGateway(Protocol):
    async def supersede(
        self, command: SupersedeTaskCommand, *, idempotency_key: str
    ) -> TaskView: ...


class HandoffDocGenerator(Protocol):
    async def generate_for_plan(
        self,
        *,
        project_id: UUID,
        plan_version: int,
        plan: IntegratedPlan,
        requirement: str,
        created_by_agent_id: UUID | None = ...,
        repositories: Sequence[str] | None = ...,
        details: Mapping[str, Mapping[str, Any]] | None = ...,
    ) -> list[Any]: ...

    async def supersede_for_repos(
        self,
        *,
        project_id: UUID,
        repositories: Sequence[str],
        superseded_by_version: int,
    ) -> int: ...


__all__ = [
    "ExecutionPlanStarter",
    "HandoffDocGenerator",
    "PlanSnapshotReader",
    "PlanSnapshotWriter",
    "ProjectTaskReader",
    "SpecificationCreator",
    "TaskSupersederGateway",
]
