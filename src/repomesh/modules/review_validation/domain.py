from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from repomesh.shared.domain import new_id

from .contracts import ValidationSnapshotView, ValidationStatus, ValidationTestInput


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
            status=self.status,
            created_at=self.created_at,
            expires_at=self.expires_at,
        )
