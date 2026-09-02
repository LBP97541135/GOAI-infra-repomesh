from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class ValidationStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ValidationTestInput:
    repository_id: UUID
    command: str
    exit_code: int
    summary: str = ""


@dataclass(frozen=True, slots=True)
class CreateValidationSnapshotCommand:
    organization_id: UUID
    project_id: UUID
    specification_version_id: UUID | None
    candidate_heads: dict[UUID, str]
    tests: tuple[ValidationTestInput, ...]
    environment: dict[str, str]
    review_evidence_ids: tuple[str, ...] = ()
    database_validation_ids: tuple[UUID, ...] = ()
    ttl_seconds: int = 86400


@dataclass(frozen=True, slots=True)
class ValidationSnapshotView:
    id: UUID
    organization_id: UUID
    project_id: UUID
    specification_version_id: UUID | None
    candidate_heads: dict[UUID, str]
    tests: tuple[ValidationTestInput, ...]
    environment: dict[str, str]
    environment_hash: str
    review_evidence_ids: tuple[str, ...]
    database_validation_ids: tuple[UUID, ...]
    status: ValidationStatus
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ValidationDecision:
    valid: bool
    reasons: tuple[str, ...]


class DatabaseValidationStatus(StrEnum):
    REQUESTED = "requested"
    PROVISIONING = "provisioning"
    READY = "ready"
    VALIDATING = "validating"
    PASSED = "passed"
    FAILED = "failed"
    CLEANING = "cleaning"
    CLEANED = "cleaned"


class DatabaseValidationStage(StrEnum):
    MIGRATION = "migration"
    BACKFILL = "backfill"
    VERIFICATION = "verification"


@dataclass(frozen=True, slots=True)
class DatabaseValidationCommand:
    stage: DatabaseValidationStage
    name: str
    command_ref: str


@dataclass(frozen=True, slots=True)
class DatabaseValidationResult:
    stage: DatabaseValidationStage
    name: str
    exit_code: int
    summary: str = ""


@dataclass(frozen=True, slots=True)
class StartDatabaseBranchValidation:
    organization_id: UUID
    project_id: UUID
    repository_id: UUID
    candidate_sha: str
    source_database_ref: str
    commands: tuple[DatabaseValidationCommand, ...]
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class DatabaseBranchValidationView:
    id: UUID
    organization_id: UUID
    project_id: UUID
    repository_id: UUID
    candidate_sha: str
    source_database_ref: str
    provider: str
    provider_branch_ref: str | None
    engine_version: str | None
    status: DatabaseValidationStatus
    results: tuple[DatabaseValidationResult, ...]
    failure_code: str | None
    cleanup_pending: bool
    idempotency_key: str
    created_at: datetime
    updated_at: datetime
