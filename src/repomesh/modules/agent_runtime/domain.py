from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from repomesh.modules.agent_runtime.contracts import (
    CodingRunStatus,
    CodingRunView,
    SessionBindingView,
    WorkspaceStatus,
    WorkspaceView,
)
from repomesh.shared.domain import new_id


class AgentRuntimeError(Exception):
    pass


class CodingRunConflict(AgentRuntimeError):
    pass


class CodingRunDenied(AgentRuntimeError):
    pass


class CodingRunNotFound(AgentRuntimeError):
    pass


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _validate_hash(value: str) -> None:
    prefix, separator, digest = value.partition(":")
    if prefix != "sha256" or separator != ":" or len(digest) != 64:
        raise ValueError("hash must use sha256:<64 lowercase hex characters>")
    if digest.lower() != digest or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError("hash must use sha256:<64 lowercase hex characters>")


@dataclass(frozen=True, slots=True)
class Workspace:
    organization_id: UUID
    project_id: UUID
    repository_id: UUID
    task_id: UUID
    path: str
    base_sha: str
    id: UUID = field(default_factory=new_id)
    status: WorkspaceStatus = WorkspaceStatus.READY
    bound_run_id: UUID | None = None
    revision: int = 1
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        path = Path(self.path)
        if not path.is_absolute():
            raise ValueError("workspace path must be absolute")
        if not self.base_sha.strip():
            raise ValueError("workspace base_sha is required")
        if self.status is WorkspaceStatus.BOUND and self.bound_run_id is None:
            raise ValueError("bound workspace requires bound_run_id")

    def bind(self, run_id: UUID) -> "Workspace":
        if self.status is not WorkspaceStatus.READY:
            raise CodingRunConflict("workspace is not ready")
        return replace(
            self,
            status=WorkspaceStatus.BOUND,
            bound_run_id=run_id,
            revision=self.revision + 1,
        )

    def to_view(self) -> WorkspaceView:
        return WorkspaceView(
            id=self.id,
            organization_id=self.organization_id,
            project_id=self.project_id,
            repository_id=self.repository_id,
            task_id=self.task_id,
            path=self.path,
            base_sha=self.base_sha,
            status=self.status,
            bound_run_id=self.bound_run_id,
            revision=self.revision,
        )


@dataclass(frozen=True, slots=True)
class CodingRun:
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
    id: UUID
    attempt: int = 1
    status: CodingRunStatus = CodingRunStatus.PREPARED
    native_session_id: str | None = None
    revision: int = 1
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not self.adapter_id.strip() or not self.instruction.strip():
            raise ValueError("adapter_id and instruction are required")
        if self.attempt < 1:
            raise ValueError("attempt must be positive")
        if not self.acceptance:
            raise ValueError("coding run requires acceptance criteria")
        _validate_hash(self.context_bundle_hash)
        _validate_hash(self.coding_package_hash)

    def transition(self, status: CodingRunStatus) -> "CodingRun":
        allowed = {
            CodingRunStatus.PREPARED: {CodingRunStatus.SUBMITTED, CodingRunStatus.CANCELLED},
            CodingRunStatus.SUBMITTED: {CodingRunStatus.RUNNING, CodingRunStatus.FAILED},
            CodingRunStatus.RUNNING: {
                CodingRunStatus.INPUT_REQUIRED,
                CodingRunStatus.SUCCEEDED,
                CodingRunStatus.FAILED,
                CodingRunStatus.CANCELLED,
            },
            CodingRunStatus.INPUT_REQUIRED: {
                CodingRunStatus.RUNNING,
                CodingRunStatus.FAILED,
                CodingRunStatus.CANCELLED,
            },
            CodingRunStatus.SUCCEEDED: {CodingRunStatus.REVIEWED},
        }
        if status not in allowed.get(self.status, set()):
            raise CodingRunConflict(
                f"cannot transition coding run from {self.status.value} to {status.value}"
            )
        return replace(self, status=status, revision=self.revision + 1)

    def bind_session(self, native_session_id: str) -> "CodingRun":
        session_id = native_session_id.strip()
        if not session_id:
            raise ValueError("native_session_id is required")
        if self.native_session_id and self.native_session_id != session_id:
            raise CodingRunConflict("coding run is already bound to another session")
        return replace(
            self,
            native_session_id=session_id,
            revision=self.revision + 1,
        )

    def to_view(self) -> CodingRunView:
        return CodingRunView(
            id=self.id,
            organization_id=self.organization_id,
            project_id=self.project_id,
            repository_id=self.repository_id,
            task_id=self.task_id,
            worker_agent_id=self.worker_agent_id,
            adapter_id=self.adapter_id,
            workspace_id=self.workspace_id,
            context_bundle_id=self.context_bundle_id,
            context_bundle_hash=self.context_bundle_hash,
            coding_package_hash=self.coding_package_hash,
            base_sha=self.base_sha,
            instruction=self.instruction,
            acceptance=self.acceptance,
            required_tests=self.required_tests,
            allowed_tools=self.allowed_tools,
            allowed_paths=self.allowed_paths,
            denied_paths=self.denied_paths,
            network_policy=self.network_policy,
            status=self.status,
            attempt=self.attempt,
            native_session_id=self.native_session_id,
            revision=self.revision,
        )


@dataclass(frozen=True, slots=True)
class SessionBinding:
    run_id: UUID
    task_id: UUID
    adapter_id: str
    workspace_id: UUID
    context_bundle_hash: str
    coding_package_hash: str
    native_session_id: str
    id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not self.adapter_id.strip() or not self.native_session_id.strip():
            raise ValueError("adapter_id and native_session_id are required")
        _validate_hash(self.context_bundle_hash)
        _validate_hash(self.coding_package_hash)

    def to_view(self) -> SessionBindingView:
        return SessionBindingView(
            id=self.id,
            run_id=self.run_id,
            task_id=self.task_id,
            adapter_id=self.adapter_id,
            workspace_id=self.workspace_id,
            context_bundle_hash=self.context_bundle_hash,
            coding_package_hash=self.coding_package_hash,
            native_session_id=self.native_session_id,
            created_at=self.created_at,
        )
