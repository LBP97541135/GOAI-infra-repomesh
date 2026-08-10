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
    status: ValidationStatus
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ValidationDecision:
    valid: bool
    reasons: tuple[str, ...]
