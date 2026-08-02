import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from uuid import UUID

from repomesh.modules.context.contracts import (
    ContextAccessResult,
    ContextObjectType,
    ContextScope,
    ContextStatus,
)
from repomesh.shared.domain import new_id


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _validate_sha256(value: str) -> None:
    prefix, separator, digest = value.partition(":")
    if separator != ":" or prefix != "sha256" or len(digest) != 64:
        raise ValueError("content_hash must use sha256:<64 lowercase hex characters>")
    if digest.lower() != digest or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError("content_hash must use sha256:<64 lowercase hex characters>")


def _validate_relative_path(value: str) -> None:
    path = PurePosixPath(value.replace("\\", "/"))
    if not value or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ValueError("context mount paths must be normalized relative paths")


def _content_hash(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


@dataclass(frozen=True, slots=True)
class ContextObject:
    organization_id: UUID
    object_type: ContextObjectType
    scope: ContextScope
    owner_subject: str
    title: str
    project_id: UUID | None = None
    status: ContextStatus = ContextStatus.DRAFT
    id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not self.owner_subject.strip():
            raise ValueError("owner_subject is required")
        if not self.title.strip():
            raise ValueError("title is required")
        if self.scope is not ContextScope.ORGANIZATION and self.project_id is None:
            raise ValueError("non-organization context requires project_id")


@dataclass(frozen=True, slots=True)
class ContextObjectVersion:
    context_object_id: UUID
    version: int
    content_uri: str
    content_hash: str
    mime_type: str
    size_bytes: int
    created_by: str
    id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=_utc_now)
    supersedes_version_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("version must be positive")
        if not self.content_uri.strip():
            raise ValueError("content_uri is required")
        _validate_sha256(self.content_hash)
        if not self.mime_type.strip():
            raise ValueError("mime_type is required")
        if self.size_bytes < 0:
            raise ValueError("size_bytes cannot be negative")
        if not self.created_by.strip():
            raise ValueError("created_by is required")
        if self.version == 1 and self.supersedes_version_id is not None:
            raise ValueError("the first version cannot supersede another version")
        if self.version > 1 and self.supersedes_version_id is None:
            raise ValueError("later versions must identify the superseded version")


@dataclass(frozen=True, slots=True)
class ContextRelation:
    source_version_id: UUID
    target_version_id: UUID
    relation_type: str
    id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if self.source_version_id == self.target_version_id:
            raise ValueError("a context version cannot relate to itself")
        if not self.relation_type.strip():
            raise ValueError("relation_type is required")


@dataclass(frozen=True, slots=True)
class ContextBundleItem:
    context_object_id: UUID
    version_id: UUID
    content_hash: str
    mount_path: str
    required_read: bool = False

    def __post_init__(self) -> None:
        _validate_sha256(self.content_hash)
        _validate_relative_path(self.mount_path)


@dataclass(frozen=True, slots=True)
class ContextBundle:
    project_id: UUID
    run_id: UUID
    task_spec_version_id: UUID
    agent_id: UUID
    role: str
    repository_id: UUID
    base_sha: str
    workspace_id: UUID
    items: tuple[ContextBundleItem, ...]
    allowed_tools: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    denied_paths: tuple[str, ...]
    network_policy: tuple[str, ...]
    expires_at: datetime
    content_hash: str
    id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not self.role.strip():
            raise ValueError("role is required")
        if not self.base_sha.strip():
            raise ValueError("base_sha is required")
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")
        version_ids = [item.version_id for item in self.items]
        mount_paths = [item.mount_path for item in self.items]
        if len(version_ids) != len(set(version_ids)):
            raise ValueError("bundle cannot contain a context version twice")
        if len(mount_paths) != len(set(mount_paths)):
            raise ValueError("bundle mount paths must be unique")
        _validate_sha256(self.content_hash)
        if self.content_hash != self.calculate_content_hash():
            raise ValueError("bundle content_hash does not match its immutable contents")

    @classmethod
    def create(
        cls,
        *,
        project_id: UUID,
        run_id: UUID,
        task_spec_version_id: UUID,
        agent_id: UUID,
        role: str,
        repository_id: UUID,
        base_sha: str,
        workspace_id: UUID,
        items: tuple[ContextBundleItem, ...],
        allowed_tools: tuple[str, ...],
        allowed_paths: tuple[str, ...],
        denied_paths: tuple[str, ...],
        network_policy: tuple[str, ...],
        expires_at: datetime,
        bundle_id: UUID | None = None,
        created_at: datetime | None = None,
    ) -> "ContextBundle":
        values = {
            "project_id": project_id,
            "run_id": run_id,
            "task_spec_version_id": task_spec_version_id,
            "agent_id": agent_id,
            "role": role,
            "repository_id": repository_id,
            "base_sha": base_sha,
            "workspace_id": workspace_id,
            "items": items,
            "allowed_tools": allowed_tools,
            "allowed_paths": allowed_paths,
            "denied_paths": denied_paths,
            "network_policy": network_policy,
            "expires_at": expires_at,
            "id": bundle_id or new_id(),
            "created_at": created_at or _utc_now(),
        }
        provisional = object.__new__(cls)
        for name, value in values.items():
            object.__setattr__(provisional, name, value)
        values["content_hash"] = provisional.calculate_content_hash()
        return cls(**values)

    def calculate_content_hash(self) -> str:
        payload = {
            "project_id": str(self.project_id),
            "run_id": str(self.run_id),
            "task_spec_version_id": str(self.task_spec_version_id),
            "agent_id": str(self.agent_id),
            "role": self.role,
            "repository_id": str(self.repository_id),
            "base_sha": self.base_sha,
            "workspace_id": str(self.workspace_id),
            "items": [
                {
                    "context_object_id": str(item.context_object_id),
                    "version_id": str(item.version_id),
                    "content_hash": item.content_hash,
                    "mount_path": item.mount_path,
                    "required_read": item.required_read,
                }
                for item in self.items
            ],
            "allowed_tools": list(self.allowed_tools),
            "allowed_paths": list(self.allowed_paths),
            "denied_paths": list(self.denied_paths),
            "network_policy": list(self.network_policy),
            "expires_at": self.expires_at.isoformat(),
        }
        return _content_hash(payload)


class DeltaKind(StrEnum):
    SUPPLEMENTAL = "supplemental"
    EXECUTION_CHANGE = "execution_change"


@dataclass(frozen=True, slots=True)
class ContextDelta:
    bundle_id: UUID
    sequence: int
    summary: str
    items: tuple[ContextBundleItem, ...]
    kind: DeltaKind
    content_hash: str
    id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("delta sequence must be positive")
        if not self.summary.strip():
            raise ValueError("delta summary is required")
        if len({item.version_id for item in self.items}) != len(self.items):
            raise ValueError("delta cannot contain a context version twice")
        _validate_sha256(self.content_hash)
        if self.content_hash != self.calculate_content_hash():
            raise ValueError("delta content_hash does not match its immutable contents")

    @classmethod
    def create(
        cls,
        *,
        bundle_id: UUID,
        sequence: int,
        summary: str,
        items: tuple[ContextBundleItem, ...],
        kind: DeltaKind = DeltaKind.SUPPLEMENTAL,
        delta_id: UUID | None = None,
        created_at: datetime | None = None,
    ) -> "ContextDelta":
        values = {
            "bundle_id": bundle_id,
            "sequence": sequence,
            "summary": summary,
            "items": items,
            "kind": kind,
            "id": delta_id or new_id(),
            "created_at": created_at or _utc_now(),
        }
        provisional = object.__new__(cls)
        for name, value in values.items():
            object.__setattr__(provisional, name, value)
        values["content_hash"] = provisional.calculate_content_hash()
        return cls(**values)

    def calculate_content_hash(self) -> str:
        return _content_hash(
            {
                "bundle_id": str(self.bundle_id),
                "sequence": self.sequence,
                "summary": self.summary,
                "kind": self.kind.value,
                "items": [
                    {
                        "context_object_id": str(item.context_object_id),
                        "version_id": str(item.version_id),
                        "content_hash": item.content_hash,
                        "mount_path": item.mount_path,
                        "required_read": item.required_read,
                    }
                    for item in self.items
                ],
            }
        )


@dataclass(frozen=True, slots=True)
class ContextAccessEvent:
    project_id: UUID
    bundle_id: UUID
    run_id: UUID
    agent_id: UUID
    version_id: UUID
    path: str
    content_hash: str
    result: ContextAccessResult
    id: UUID = field(default_factory=new_id)
    accessed_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        _validate_relative_path(self.path)
        _validate_sha256(self.content_hash)


@dataclass(frozen=True, slots=True)
class ConversationState:
    completed: tuple[str, ...] = ()
    next_action: str | None = None
    open_questions: tuple[str, ...] = ()
    last_error: str | None = None
    artifact_refs: tuple[UUID, ...] = ()
