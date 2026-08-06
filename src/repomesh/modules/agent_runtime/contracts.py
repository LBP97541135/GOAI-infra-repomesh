from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class StartAssignedWorkerTaskCommand:
    task_id: UUID
    worker_agent_id: UUID
    adapter_id: str
    base_revision: str = "main"
    task_features: frozenset[str] = frozenset()
