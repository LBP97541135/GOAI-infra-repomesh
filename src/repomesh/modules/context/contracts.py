from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class ContextScope(StrEnum):
    ORGANIZATION = "organization"
    PROJECT_SHARED = "project_shared"
    TEAM_PRIVATE = "team_private"
    TASK_PRIVATE = "task_private"
    RUN_PRIVATE = "run_private"
    SECRET = "secret"


class ContextAction(StrEnum):
    DISCOVER = "discover"
    READ = "read"
    MOUNT = "mount"
    PUBLISH = "publish"
    APPROVE = "approve"
    EXPORT = "export"


class ContextObjectType(StrEnum):
    PRD = "prd"
    ENGINEERING_SPEC = "engineering_spec"
    CONTRACT = "contract"
    DECISION = "decision"
    REPOSITORY_PROFILE = "repository_profile"
    REPOSITORY_EVIDENCE = "repository_evidence"
    REPOSITORY_SCOPE_REVIEW = "repository_scope_review"
    TEST_PLAN = "test_plan"
    TASK_SPEC = "task_spec"
    TASK_RESULT = "task_result"
    PROGRESS = "progress"
    TEST_EVIDENCE = "test_evidence"
    CHANGE_REQUEST = "change_request"
    IMPACT_ASSESSMENT = "impact_assessment"
    DELIVERY_PLAN = "delivery_plan"


class ContextStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    APPROVED = "approved"
    ARCHIVED = "archived"


class ContextAccessResult(StrEnum):
    ALLOWED = "allowed"
    DENIED = "denied"
    NOT_FOUND = "not_found"
    HASH_MISMATCH = "hash_mismatch"


@dataclass(frozen=True, slots=True)
class ContextVersionRef:
    context_object_id: UUID
    version_id: UUID
    version: int
    object_type: ContextObjectType
    scope: ContextScope
    title: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class ContextBundleRef:
    bundle_id: UUID
    run_id: UUID
    task_spec_version_id: UUID
    agent_id: UUID
    content_hash: str
    item_version_ids: tuple[UUID, ...]
    required_read_version_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class ContextBundlePublished:
    project_id: UUID
    bundle: ContextBundleRef


@dataclass(frozen=True, slots=True)
class ContextAccessRecorded:
    project_id: UUID
    run_id: UUID
    agent_id: UUID
    version_id: UUID
    path: str
    content_hash: str
    result: ContextAccessResult


@dataclass(frozen=True, slots=True)
class ExecutionContextGrant:
    bundle_id: UUID
    project_id: UUID
    run_id: UUID
    agent_id: UUID
    repository_id: UUID
    allowed_tools: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    denied_paths: tuple[str, ...]
    network_policy: tuple[str, ...]
    expires_at: datetime
    content_hash: str
    base_sha: str | None = None
    workspace_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class PublishContextRequest:
    organization_id: UUID
    project_id: UUID
    object_type: ContextObjectType
    scope: ContextScope
    owner_subject: str
    title: str
    content_uri: str
    content_hash: str
    mime_type: str
    size_bytes: int
    created_by: str


class ContextPublisher(Protocol):
    async def publish(self, request: PublishContextRequest) -> ContextVersionRef: ...
