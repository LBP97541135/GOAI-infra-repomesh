"""Ports consumed by the cross-module change workflow."""

from collections.abc import Mapping, Sequence
from typing import Any, Protocol
from uuid import UUID

from repomesh.modules.repository_intelligence.application.plan_integration import IntegratedPlan
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


class PlanSnapshotDraft(Protocol):
    """The unconsumed snapshot a round is being assembled on."""

    @property
    def id(self) -> UUID: ...

    @property
    def plan_version(self) -> int: ...


class PlanSnapshotWriter(Protocol):
    async def next_version(self, project_id: UUID) -> int: ...

    async def current_draft(self, project_id: UUID) -> PlanSnapshotDraft | None:
        """The round's own snapshot, when one is already open (v0.4 §2.4)."""
        ...

    async def set_integration(
        self,
        snapshot_id: UUID,
        *,
        engineering_spec: str,
        contracts: list[dict],
        task_dag: list[dict],
        execution_batches: list[list[str]],
        integration_method: str | None = ...,
    ) -> None: ...

    async def link_execution_plan(
        self, snapshot_id: UUID, execution_plan_id: UUID
    ) -> None: ...

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
    "PlanSnapshotWriter",
    "ProjectTaskReader",
    "SpecificationCreator",
    "TaskSupersederGateway",
]
