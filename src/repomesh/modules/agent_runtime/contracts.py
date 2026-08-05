from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
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


class WorkspaceStatus(StrEnum):
    READY = "ready"
    BOUND = "bound"
    ARCHIVED = "archived"


class CodingRunStatus(StrEnum):
    PREPARED = "prepared"
    SUBMITTED = "submitted"
    RUNNING = "running"
    INPUT_REQUIRED = "input_required"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REVIEWED = "reviewed"


@dataclass(frozen=True, slots=True)
class RegisterWorkspaceCommand:
    organization_id: UUID
    project_id: UUID
    repository_id: UUID
    task_id: UUID
    path: str
    base_sha: str


@dataclass(frozen=True, slots=True)
class WorkspaceView:
    id: UUID
    organization_id: UUID
    project_id: UUID
    repository_id: UUID
    task_id: UUID
    path: str
    base_sha: str
    status: WorkspaceStatus
    bound_run_id: UUID | None
    revision: int


@dataclass(frozen=True, slots=True)
class PrepareCodingRunCommand:
    organization_id: UUID
    project_id: UUID
    repository_id: UUID
    task_id: UUID
    worker_agent_id: UUID
    adapter_id: str
    workspace_id: UUID
    context_bundle_id: UUID
    run_id: UUID
    attempt: int = 1


@dataclass(frozen=True, slots=True)
class CodingRunView:
    id: UUID
    organization_id: UUID
    project_id: UUID
    repository_id: UUID
    task_id: UUID
    worker_agent_id: UUID
    adapter_id: str
    workspace_id: UUID
    context_bundle_id: UUID
    context_bundle_hash: str
    coding_package_hash: str
    base_sha: str
    instruction: str
    acceptance: tuple[str, ...]
    required_tests: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    denied_paths: tuple[str, ...]
    network_policy: tuple[str, ...]
    status: CodingRunStatus
    attempt: int
    native_session_id: str | None
    revision: int


@dataclass(frozen=True, slots=True)
class SessionBindingView:
    id: UUID
    run_id: UUID
    task_id: UUID
    adapter_id: str
    workspace_id: UUID
    context_bundle_hash: str
    coding_package_hash: str
    native_session_id: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CommandExecutionResult:
    command: str
    exit_code: int


@dataclass(frozen=True, slots=True)
class RunnerResultCandidate:
    run_id: UUID
    succeeded: bool
    summary: str
    changed_files: tuple[str, ...]
    test_results: tuple[CommandExecutionResult, ...] = ()
    native_session_id: str | None = None


@dataclass(frozen=True, slots=True)
class RunnerResultValidation:
    accepted: bool
    reasons: tuple[str, ...]
