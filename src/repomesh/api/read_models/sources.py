"""Read-only data sources the delivery read model aggregates.

The read model owns no facts: every protocol below is implemented in the
composition root over the producing module's stores, and the dataclasses
carry only fields the delivery read-model contract v0.1 consumes.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from repomesh.modules.collaboration.contracts import CollaborationMessageView
from repomesh.modules.delivery.contracts import (
    ChangeSetView,
    DeliveryArchiveView,
    MergeGateDecision,
    SCMObservationView,
)
from repomesh.modules.project.contracts import ProjectAgentTopologyView
from repomesh.modules.review_validation.contracts import ValidationSnapshotView
from repomesh.modules.task_orchestration.contracts import ExecutionPlanView, TaskView


@dataclass(frozen=True, slots=True)
class PlanSnapshotData:
    id: UUID
    project_id: UUID
    plan_version: int
    created_at: datetime
    engineering_spec: str
    requirement_text: str | None
    execution_batches: tuple[tuple[str, ...], ...]
    task_dag: tuple[dict, ...]
    execution_plan_id: UUID | None
    created_by_agent_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class SpecificationContractData:
    specification_id: UUID
    version: int
    status: str
    goal: str
    acceptance: tuple[str, ...]
    constraints: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    forbidden_paths: tuple[str, ...]
    tests: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RepositoryData:
    id: UUID
    name: str
    description: str


class ExecutionPlanSource(Protocol):
    async def list_all(self) -> tuple[ExecutionPlanView, ...]: ...

    async def get(self, plan_id: UUID) -> ExecutionPlanView | None: ...


class PlanSnapshotSource(Protocol):
    async def project_ids(self) -> tuple[UUID, ...]: ...

    async def for_project(self, project_id: UUID) -> tuple[PlanSnapshotData, ...]:
        """Snapshots for one project, newest plan_version first."""
        ...


class TaskSource(Protocol):
    async def list_by_project(self, project_id: UUID) -> tuple[TaskView, ...]: ...


class ChangeSetSource(Protocol):
    async def for_delivery(self, delivery_id: UUID) -> ChangeSetView | None: ...

    async def merge_gate(
        self, change_set_id: UUID, repository_id: UUID
    ) -> MergeGateDecision: ...


class ArchiveSource(Protocol):
    async def get(self, delivery_id: UUID) -> DeliveryArchiveView | None: ...


class ValidationSource(Protocol):
    async def for_project(self, project_id: UUID) -> tuple[ValidationSnapshotView, ...]: ...


class SpecificationSource(Protocol):
    async def engineering_contract(
        self, project_id: UUID
    ) -> SpecificationContractData | None: ...


class RepositorySource(Protocol):
    async def list(self) -> tuple[RepositoryData, ...]: ...


@dataclass(frozen=True, slots=True)
class RunnerEventData:
    event_id: UUID
    run_id: UUID
    sequence: int
    event_type: str
    occurred_at: datetime
    task_id: UUID
    repository_id: UUID


class RunnerEventSource(Protocol):
    async def for_project(self, project_id: UUID) -> tuple[RunnerEventData, ...]: ...


class MessageSource(Protocol):
    async def for_project(
        self, project_id: UUID
    ) -> tuple[CollaborationMessageView, ...]: ...


class ObservationSource(Protocol):
    async def for_change_set(
        self, change_set_id: UUID
    ) -> tuple[SCMObservationView, ...]: ...


class AgentNameSource(Protocol):
    async def name(self, agent_id: UUID) -> str | None: ...

    async def organization_id(self, agent_id: UUID) -> UUID | None:
        """Owning organization of one agent; None when the agent is unknown."""
        ...


class TopologySource(Protocol):
    async def matrix_room_id(self, project_id: UUID) -> str | None: ...

    async def get_view(self, project_id: UUID) -> ProjectAgentTopologyView | None:
        """Agent topology of one issue; None when no team was ever formed."""
        ...
