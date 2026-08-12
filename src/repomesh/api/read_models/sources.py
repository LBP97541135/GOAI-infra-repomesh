"""Read-only data sources the delivery read model aggregates.

The read model owns no facts: every protocol below is implemented in the
composition root over the producing module's stores, and the dataclasses
carry only fields the delivery read-model contract v0.1 consumes.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from repomesh.modules.agent_directory.contracts import AgentPrincipalView
from repomesh.modules.collaboration.contracts import CollaborationMessageView
from repomesh.modules.delivery.contracts import (
    ChangeSetView,
    DeliveryArchiveView,
    MergeGateDecision,
    RecoveryActionView,
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
    contracts: tuple[dict, ...] = ()
    discovery: dict | None = None
    """Contract v0.4 §2: the round's discovery chain, or None if it never ran.

    None and ``{}`` are different answers — "never started" versus "started and
    holds nothing" — and §3.1 projects them differently, so this must not be
    defaulted to an empty dict for convenience.
    """


class DiscoveryTaskProbe(Protocol):
    """Whether a discovery step is in flight right now (v0.4 §3.1).

    Task records live in the writing module's process memory, and the read
    model does not reach into other modules' internals — the composition root
    supplies this the same way it supplies the AgentTeams runtime probe.
    Returns ``(task_id, gui_step)`` or None.
    """

    def running(self, issue_id: UUID) -> tuple[UUID, int] | None: ...


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

    async def list_all(self) -> tuple[TaskView, ...]:
        """Every task, for the repository grid and the roster's task counts."""
        ...


class ChangeSetSource(Protocol):
    async def for_delivery(self, delivery_id: UUID) -> ChangeSetView | None: ...

    async def merge_gate(
        self, change_set_id: UUID, repository_id: UUID
    ) -> MergeGateDecision: ...

    async def recovery_preview(
        self, change_set_id: UUID
    ) -> tuple[RecoveryActionView, ...]:
        """The actions an operator-requested rollback would run, unsaved.

        The rollback scope projection (§4.6) must not re-derive "which
        repository gets a revert PR, and in which position" — that rule lives
        in delivery's recovery planner, and this is the same planner run in
        preview mode.
        """
        ...


class ArchiveSource(Protocol):
    async def get(self, delivery_id: UUID) -> DeliveryArchiveView | None: ...


class ValidationSource(Protocol):
    async def for_project(self, project_id: UUID) -> tuple[ValidationSnapshotView, ...]: ...


@dataclass(frozen=True, slots=True)
class RepositorySpecData:
    """A per-repository spec (v0.2 §5.4), distinct from the project contract."""

    specification_id: UUID
    kind: str
    status: str
    revision: int
    goal: str
    acceptance: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    forbidden_paths: tuple[str, ...]
    tests: tuple[str, ...]


class SpecificationSource(Protocol):
    async def engineering_contract(
        self, project_id: UUID
    ) -> SpecificationContractData | None: ...

    async def repository_spec(
        self, project_id: UUID, repository_id: UUID
    ) -> RepositorySpecData | None:
        """§5.4: FROZEN wins over APPROVED, then the highest revision."""
        ...


@dataclass(frozen=True, slots=True)
class RepositoryProfileData:
    """The catalog fields the repository grid renders (v0.2 §4.1).

    `auto_card` is deliberately absent: discovery evidence is not stored per
    project (v0.1 §6.10), so the grid's evidence slot stays 未接入.
    """

    id: UUID
    name: str
    url: str
    description: str
    topics: tuple[str, ...]
    languages: tuple[str, ...]
    profiled_at: datetime
    test_commands: tuple[str, ...] = ()
    """Defect A-19: the verification commands every round over this repo inherits."""


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    """One AgentTeams resource as the controller currently reports it.

    `uptime_seconds` and `awake` are absent on purpose: the controller exposes
    no start timestamp, and the desired state is not an observation (§4.4).
    """

    phase: str | None = None
    runtime_kind: str | None = None
    matrix_user_id: str | None = None
    room_id: str | None = None
    message: str | None = None
    ready_workers: int | None = None
    total_workers: int | None = None


class RuntimeProbe(Protocol):
    """Live proxy to the AgentTeams controller; None means 404 (§4.4)."""

    async def worker(self, name: str) -> RuntimeSnapshot | None: ...

    async def manager(self, name: str) -> RuntimeSnapshot | None: ...

    async def team(self, name: str) -> RuntimeSnapshot | None: ...


class RepositorySource(Protocol):
    async def list(self) -> tuple[RepositoryData, ...]: ...

    async def profiles(self) -> tuple[RepositoryProfileData, ...]:
        """The full catalog rows the grid needs, not just id and name."""
        ...


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

    async def for_room(self, room_id: str) -> tuple[CollaborationMessageView, ...]:
        """Messages delivered to one Matrix room, oldest first."""
        ...


class ObservationSource(Protocol):
    async def for_change_set(
        self, change_set_id: UUID
    ) -> tuple[SCMObservationView, ...]: ...


class AgentNameSource(Protocol):
    async def name(self, agent_id: UUID) -> str | None: ...

    async def organization_id(self, agent_id: UUID) -> UUID | None:
        """Owning organization of one agent; None when the agent is unknown."""
        ...

    async def list_all(self) -> tuple[AgentPrincipalView, ...]:
        """The whole registry, for the agent roster (§4.3)."""
        ...


class TopologySource(Protocol):
    async def matrix_room_id(self, project_id: UUID) -> str | None: ...

    async def get_view(self, project_id: UUID) -> ProjectAgentTopologyView | None:
        """Agent topology of one issue; None when no team was ever formed."""
        ...

    async def find_by_room(self, room_id: str) -> ProjectAgentTopologyView | None:
        """The topology owning a room, so a room id resolves without a scan."""
        ...

    async def list_views(self) -> tuple[ProjectAgentTopologyView, ...]:
        """Every topology, for the repository grid and the team list (§4.1/§4.2)."""
        ...
