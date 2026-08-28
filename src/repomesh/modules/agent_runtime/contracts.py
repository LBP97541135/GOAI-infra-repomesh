from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from repomesh.modules.context.contracts import ExecutionContextGrant

from .ports.coding_agent import CodingRunRequest


@dataclass(frozen=True, slots=True)
class CodingRunFinished:
    run_id: UUID
    task_id: UUID
    status: str


@dataclass(frozen=True, slots=True)
class AuthorizedCodingRunRequest:
    organization_id: UUID
    project_id: UUID
    repository_id: UUID
    agent_id: UUID
    coding_request: CodingRunRequest
    context_grant: ExecutionContextGrant
    requested_paths: tuple[str, ...]
    requested_tools: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DispatchWorkerTaskCommand:
    organization_id: UUID
    project_id: UUID
    repository_id: UUID
    task_id: UUID
    worker_agent_id: UUID
    bundle_id: UUID
    run_id: UUID
    correlation_id: UUID
    adapter_id: str
    base_revision: str = "main"
    attempt: int = 1
    permission_mode: str = "accept_edits"
    resume_session_id: str | None = None
    credential_refs: tuple[str, ...] = ()
    task_features: frozenset[str] = frozenset()
    assignment_attempt_id: UUID | None = None
    assignment_generation: int | None = None
    execution_id: UUID | None = None
    execution_version: int | None = None


@dataclass(frozen=True, slots=True)
class StartAssignedWorkerTaskCommand:
    task_id: UUID
    worker_agent_id: UUID
    adapter_id: str
    base_revision: str = "main"
    task_features: frozenset[str] = frozenset()
    resume_session_id: str | None = None


@dataclass(frozen=True, slots=True)
class ActiveWorkerDispatch:
    """A Runner dispatch the execution plane has not finished yet.

    ``task_payload`` is the stored ``runtime.v1`` task envelope, so callers can recover the run's
    workspace and context binding without re-deriving them.
    """

    run_id: UUID
    task_id: UUID
    worker_agent_id: UUID
    attempt: int
    status: str
    task_payload: Mapping[str, object]


class WorkerDispatchReader(Protocol):
    async def get_active_dispatch_for_task(
        self, task_id: UUID, *, worker_agent_id: UUID
    ) -> ActiveWorkerDispatch | None: ...


class WorkerExecutionStatus(StrEnum):
    PREPARING = "preparing"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class WorkerExecutionReservation:
    id: UUID
    organization_id: UUID
    project_id: UUID
    repository_id: UUID
    task_id: UUID
    worker_agent_id: UUID
    run_id: UUID
    status: WorkerExecutionStatus
    attempt: int
    version: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    task_payload: Mapping[str, object] | None
    error_detail: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    assignment_attempt_id: UUID | None = None
    assignment_generation: int | None = None


@dataclass(frozen=True, slots=True)
class ReservedWorkerExecution:
    reservation: WorkerExecutionReservation
    created: bool


class WorkerExecutionReservationPort(Protocol):
    async def reserve(
        self,
        *,
        organization_id: UUID,
        project_id: UUID,
        repository_id: UUID,
        task_id: UUID,
        worker_agent_id: UUID,
        lease_owner: str,
        lease_seconds: int,
        assignment_attempt_id: UUID | None = None,
        assignment_generation: int | None = None,
    ) -> ReservedWorkerExecution: ...

    async def get_active(self, task_id: UUID) -> WorkerExecutionReservation | None: ...

    async def get(self, execution_id: UUID) -> WorkerExecutionReservation | None: ...

    async def bind_payload(
        self,
        reservation_id: UUID,
        payload: Mapping[str, object],
        *,
        lease_owner: str,
        fencing_version: int,
    ) -> WorkerExecutionReservation: ...

    async def renew(
        self,
        reservation_id: UUID,
        *,
        lease_owner: str,
        fencing_version: int,
        lease_seconds: int,
    ) -> WorkerExecutionReservation: ...

    async def fail_preparation(
        self,
        reservation_id: UUID,
        error: str,
        *,
        lease_owner: str,
        fencing_version: int,
    ) -> WorkerExecutionReservation: ...
