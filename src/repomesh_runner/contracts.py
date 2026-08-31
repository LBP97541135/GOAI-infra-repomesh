from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

RUNTIME_SCHEMA_VERSION = "runtime.v1"


class RunnerPermissionMode(StrEnum):
    DEFAULT = "default"
    ACCEPT_EDITS = "accept_edits"
    AUTO = "auto"
    BYPASS_PERMISSIONS = "bypass_permissions"


class RunnerResultStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    INPUT_REQUIRED = "input_required"


class RunnerEventType(StrEnum):
    ACCEPTED = "runner.accepted"
    SESSION_STARTED = "runner.session_started"
    PROGRESS = "runner.progress"
    TEST_COMPLETED = "runner.test_completed"
    INPUT_REQUIRED = "runner.input_required"
    COMPLETED = "runner.completed"
    FAILED = "runner.failed"
    INTERRUPTED = "runner.interrupted"


def _validate_sha256(value: str) -> None:
    prefix, separator, digest = value.partition(":")
    if prefix != "sha256" or separator != ":" or len(digest) != 64:
        raise ValueError("content_hash must be a sha256 reference")
    if any(character not in "0123456789abcdef" for character in digest):
        raise ValueError("content_hash must use lowercase hexadecimal")


def _validate_unique_strings(name: str, values: tuple[str, ...]) -> None:
    if any(not value.strip() for value in values):
        raise ValueError(f"{name} cannot contain empty values")
    if len(set(values)) != len(values):
        raise ValueError(f"{name} must contain unique values")


def _validate_timezone(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")


@dataclass(frozen=True, slots=True)
class RepositoryCheckout:
    repository_id: UUID
    url: str
    base_revision: str

    def __post_init__(self) -> None:
        if not self.url.strip():
            raise ValueError("repository url is required")
        if not self.base_revision.strip():
            raise ValueError("base_revision is required")


@dataclass(frozen=True, slots=True)
class WorkspaceAssignment:
    """Workspace prepared by the platform before the task is dispatched.

    When a task carries a workspace assignment it is the single source of truth for where
    execution happens and at which base revision. ``RepositoryCheckout`` (``repository.url`` and
    ``baseRevision``) is then reference metadata only: the Runner never clones.
    """

    workspace_id: str
    path: str
    base_sha: str

    def __post_init__(self) -> None:
        if not self.workspace_id.strip():
            raise ValueError("workspace_id is required")
        if not self.path.strip():
            raise ValueError("workspace path is required")
        if not self.base_sha.strip():
            raise ValueError("workspace base_sha is required")


@dataclass(frozen=True, slots=True)
class ContextBundleRef:
    bundle_id: UUID
    version: int
    manifest_uri: str
    content_hash: str
    coding_package_hash: str | None = None

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("context bundle version must be positive")
        if not self.manifest_uri.strip():
            raise ValueError("context bundle manifest_uri is required")
        _validate_sha256(self.content_hash)
        if self.coding_package_hash is not None:
            _validate_sha256(self.coding_package_hash)


@dataclass(frozen=True, slots=True)
class RunnerPermissions:
    mode: RunnerPermissionMode = RunnerPermissionMode.DEFAULT
    allowed_tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()
    network_targets: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    denied_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_unique_strings("allowed_tools", self.allowed_tools)
        _validate_unique_strings("disallowed_tools", self.disallowed_tools)
        _validate_unique_strings("network_targets", self.network_targets)
        _validate_unique_strings("allowed_paths", self.allowed_paths)
        _validate_unique_strings("denied_paths", self.denied_paths)


@dataclass(frozen=True, slots=True)
class RunnerSkillRef:
    skill_id: str
    version: str
    release_id: UUID
    assignment_id: UUID
    content_hash: str

    def to_wire(self) -> dict[str, str]:
        return {
            "skillId": self.skill_id,
            "version": self.version,
            "releaseId": str(self.release_id),
            "assignmentId": str(self.assignment_id),
            "contentHash": self.content_hash,
        }


@dataclass(frozen=True, slots=True)
class RunnerTask:
    organization_id: UUID
    project_id: UUID
    task_id: UUID
    run_id: UUID
    correlation_id: UUID
    attempt: int
    adapter_id: str
    instruction: str
    repository: RepositoryCheckout
    context_bundle: ContextBundleRef
    permissions: RunnerPermissions
    idempotency_key: str
    issued_at: datetime
    resume_session_id: str | None = None
    credential_refs: tuple[str, ...] = ()
    workspace: WorkspaceAssignment | None = None
    worker_agent_id: UUID | None = None
    test_commands: tuple[str, ...] = ()
    assignment_attempt_id: UUID | None = None
    assignment_generation: int | None = None
    execution_id: UUID | None = None
    execution_version: int | None = None
    skills: tuple[RunnerSkillRef, ...] = ()

    def __post_init__(self) -> None:
        if self.attempt < 1:
            raise ValueError("attempt must be positive")
        if not self.adapter_id.strip():
            raise ValueError("adapter_id is required")
        if not self.instruction.strip():
            raise ValueError("instruction is required")
        if not self.idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        _validate_timezone("issued_at", self.issued_at)
        _validate_unique_strings("credential_refs", self.credential_refs)
        _validate_unique_strings("test_commands", self.test_commands)

    def to_wire(self) -> dict[str, Any]:
        return {
            "schemaVersion": RUNTIME_SCHEMA_VERSION,
            "organizationId": str(self.organization_id),
            "projectId": str(self.project_id),
            "taskId": str(self.task_id),
            "runId": str(self.run_id),
            "correlationId": str(self.correlation_id),
            "attempt": self.attempt,
            "adapterId": self.adapter_id,
            "instruction": self.instruction,
            "repository": {
                "repositoryId": str(self.repository.repository_id),
                "url": self.repository.url,
                "baseRevision": self.repository.base_revision,
            },
            "workspace": (
                {
                    "workspaceId": self.workspace.workspace_id,
                    "path": self.workspace.path,
                    "baseSha": self.workspace.base_sha,
                }
                if self.workspace is not None
                else None
            ),
            "contextBundle": {
                "bundleId": str(self.context_bundle.bundle_id),
                "version": self.context_bundle.version,
                "manifestUri": self.context_bundle.manifest_uri,
                "contentHash": self.context_bundle.content_hash,
                "codingPackageHash": self.context_bundle.coding_package_hash,
            },
            "permissions": {
                "mode": self.permissions.mode.value,
                "allowedTools": list(self.permissions.allowed_tools),
                "disallowedTools": list(self.permissions.disallowed_tools),
                "networkTargets": list(self.permissions.network_targets),
                "allowedPaths": list(self.permissions.allowed_paths),
                "deniedPaths": list(self.permissions.denied_paths),
            },
            "resumeSessionId": self.resume_session_id,
            "credentialRefs": list(self.credential_refs),
            "workerAgentId": (
                str(self.worker_agent_id) if self.worker_agent_id is not None else None
            ),
            "testCommands": list(self.test_commands),
            "assignmentAttemptId": (
                str(self.assignment_attempt_id) if self.assignment_attempt_id else None
            ),
            "assignmentGeneration": self.assignment_generation,
            "executionId": str(self.execution_id) if self.execution_id else None,
            "executionVersion": self.execution_version,
            "skills": [skill.to_wire() for skill in self.skills],
            "idempotencyKey": self.idempotency_key,
            "issuedAt": self.issued_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    kind: str
    uri: str
    content_hash: str

    def __post_init__(self) -> None:
        if not self.kind.strip() or not self.uri.strip():
            raise ValueError("artifact kind and uri are required")
        _validate_sha256(self.content_hash)

    def to_wire(self) -> dict[str, str]:
        return {"kind": self.kind, "uri": self.uri, "contentHash": self.content_hash}


@dataclass(frozen=True, slots=True)
class TestCommandResult:
    command: str
    exit_code: int

    def __post_init__(self) -> None:
        if not self.command.strip():
            raise ValueError("test command is required")


@dataclass(frozen=True, slots=True)
class RunnerExecutionResult:
    status: RunnerResultStatus
    summary: str
    native_session_id: str | None = None
    artifacts: tuple[ArtifactRef, ...] = ()
    test_command: str | None = None
    changed_files: tuple[str, ...] = ()
    test_results: tuple[TestCommandResult, ...] = ()
    commit_sha: str | None = None

    def __post_init__(self) -> None:
        _validate_unique_strings("changed_files", self.changed_files)
        if self.commit_sha is not None:
            normalized = self.commit_sha.strip().lower()
            if len(normalized) not in {40, 64} or any(
                character not in "0123456789abcdef" for character in normalized
            ):
                raise ValueError("commit_sha must be a full Git object id")


@dataclass(frozen=True, slots=True)
class RunnerEvent:
    event_id: UUID
    event_type: RunnerEventType
    task: RunnerTask
    sequence: int
    occurred_at: datetime
    payload: Mapping[str, Any]
    native_session_id: str | None = None

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("event sequence must be positive")
        _validate_timezone("occurred_at", self.occurred_at)

    def to_wire(self) -> dict[str, Any]:
        return {
            "schemaVersion": RUNTIME_SCHEMA_VERSION,
            "eventId": str(self.event_id),
            "eventType": self.event_type.value,
            "organizationId": str(self.task.organization_id),
            "projectId": str(self.task.project_id),
            "taskId": str(self.task.task_id),
            "runId": str(self.task.run_id),
            "correlationId": str(self.task.correlation_id),
            "attempt": self.task.attempt,
            "sequence": self.sequence,
            "occurredAt": self.occurred_at.isoformat(),
            "nativeSessionId": self.native_session_id,
            "assignmentAttemptId": (
                str(self.task.assignment_attempt_id)
                if self.task.assignment_attempt_id
                else None
            ),
            "assignmentGeneration": self.task.assignment_generation,
            "executionId": str(self.task.execution_id) if self.task.execution_id else None,
            "executionVersion": self.task.execution_version,
            "payload": dict(self.payload),
        }
