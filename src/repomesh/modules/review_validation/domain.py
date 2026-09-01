from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from repomesh.shared.domain import new_id

from .contracts import (
    DatabaseBranchValidationView,
    DatabaseValidationResult,
    DatabaseValidationStatus,
    ValidationSnapshotView,
    ValidationStatus,
    ValidationTestInput,
)


@dataclass(frozen=True, slots=True)
class ValidationSnapshot:
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
    expires_at: datetime
    id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_view(self) -> ValidationSnapshotView:
        return ValidationSnapshotView(
            id=self.id,
            organization_id=self.organization_id,
            project_id=self.project_id,
            specification_version_id=self.specification_version_id,
            candidate_heads=dict(self.candidate_heads),
            tests=self.tests,
            environment=dict(self.environment),
            environment_hash=self.environment_hash,
            review_evidence_ids=self.review_evidence_ids,
            database_validation_ids=self.database_validation_ids,
            status=self.status,
            created_at=self.created_at,
            expires_at=self.expires_at,
        )


@dataclass(frozen=True, slots=True)
class DatabaseBranchValidation:
    organization_id: UUID
    project_id: UUID
    repository_id: UUID
    candidate_sha: str
    source_database_ref: str
    provider: str
    request_hash: str
    idempotency_key: str
    status: DatabaseValidationStatus = DatabaseValidationStatus.REQUESTED
    provider_branch_ref: str | None = None
    engine_version: str | None = None
    results: tuple[DatabaseValidationResult, ...] = ()
    failure_code: str | None = None
    cleanup_pending: bool = False
    id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_view(self) -> DatabaseBranchValidationView:
        return DatabaseBranchValidationView(
            id=self.id,
            organization_id=self.organization_id,
            project_id=self.project_id,
            repository_id=self.repository_id,
            candidate_sha=self.candidate_sha,
            source_database_ref=self.source_database_ref,
            provider=self.provider,
            provider_branch_ref=self.provider_branch_ref,
            engine_version=self.engine_version,
            status=self.status,
            results=self.results,
            failure_code=self.failure_code,
            cleanup_pending=self.cleanup_pending,
            idempotency_key=self.idempotency_key,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )
