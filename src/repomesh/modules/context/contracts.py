from dataclasses import dataclass
from enum import StrEnum
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
