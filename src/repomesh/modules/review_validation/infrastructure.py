from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.persistence import Database
from repomesh.persistence.base import Base

from .contracts import ValidationStatus, ValidationTestInput
from .domain import ValidationSnapshot

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


class ValidationSnapshotRecord(Base):
    __tablename__ = "validation_snapshots"
    __table_args__ = ({"schema": "review_validation"},)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    specification_version_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    environment_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSON_DOCUMENT)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class InMemoryValidationSnapshotStore:
    def __init__(self) -> None:
        self.items: dict[UUID, ValidationSnapshot] = {}

    async def add(self, snapshot: ValidationSnapshot) -> None:
        if snapshot.id in self.items:
            raise ValueError("duplicate validation snapshot")
        self.items[snapshot.id] = snapshot

    async def get(self, snapshot_id: UUID) -> ValidationSnapshot | None:
        return self.items.get(snapshot_id)


class PostgresValidationSnapshotStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def add(self, snapshot: ValidationSnapshot) -> None:
        async with self._database.transaction() as session:
            session.add(
                ValidationSnapshotRecord(
                    id=snapshot.id,
                    organization_id=snapshot.organization_id,
                    project_id=snapshot.project_id,
                    specification_version_id=snapshot.specification_version_id,
                    environment_hash=snapshot.environment_hash,
                    status=snapshot.status.value,
                    payload={
                        "candidate_heads": {
                            str(key): value for key, value in snapshot.candidate_heads.items()
                        },
                        "tests": [
                            {
                                "repository_id": str(item.repository_id),
                                "command": item.command,
                                "exit_code": item.exit_code,
                                "summary": item.summary,
                            }
                            for item in snapshot.tests
                        ],
                        "environment": snapshot.environment,
                        "review_evidence_ids": list(snapshot.review_evidence_ids),
                    },
                    created_at=snapshot.created_at,
                    expires_at=snapshot.expires_at,
                )
            )

    async def get(self, snapshot_id: UUID) -> ValidationSnapshot | None:
        async with self._database.transaction() as session:
            record = await session.get(ValidationSnapshotRecord, snapshot_id)
        if record is None:
            return None
        payload = record.payload
        return ValidationSnapshot(
            id=record.id,
            organization_id=record.organization_id,
            project_id=record.project_id,
            specification_version_id=record.specification_version_id,
            candidate_heads={
                UUID(key): str(value) for key, value in dict(payload["candidate_heads"]).items()
            },
            tests=tuple(
                ValidationTestInput(
                    repository_id=UUID(str(item["repository_id"])),
                    command=str(item["command"]),
                    exit_code=int(item["exit_code"]),
                    summary=str(item.get("summary", "")),
                )
                for item in payload["tests"]
            ),
            environment={
                str(key): str(value) for key, value in dict(payload["environment"]).items()
            },
            environment_hash=record.environment_hash,
            review_evidence_ids=tuple(payload.get("review_evidence_ids", ())),
            status=ValidationStatus(record.status),
            created_at=_aware(record.created_at),
            expires_at=_aware(record.expires_at),
        )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
