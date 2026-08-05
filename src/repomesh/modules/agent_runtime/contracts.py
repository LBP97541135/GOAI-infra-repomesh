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
