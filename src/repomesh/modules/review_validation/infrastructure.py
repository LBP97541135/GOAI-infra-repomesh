import asyncio
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import JSON, Boolean, DateTime, String, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.persistence import Database
from repomesh.persistence.base import Base

from .contracts import (
    DatabaseValidationResult,
    DatabaseValidationStage,
    DatabaseValidationStatus,
    ValidationStatus,
    ValidationTestInput,
)
from .domain import DatabaseBranchValidation, ValidationSnapshot
from .ports import ProvisionedDatabaseBranch

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


class DatabaseBranchValidationRecord(Base):
    __tablename__ = "database_branch_validations"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_database_branch_validations_org_idempotency",
        ),
        {"schema": "review_validation"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    repository_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    candidate_sha: Mapped[str] = mapped_column(String(40))
    source_database_ref: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(80))
    provider_branch_ref: Mapped[str | None] = mapped_column(String(200))
    engine_version: Mapped[str | None] = mapped_column(String(120))
    request_hash: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), index=True)
    failure_code: Mapped[str | None] = mapped_column(String(80))
    cleanup_pending: Mapped[bool] = mapped_column(Boolean, default=False)
    results: Mapped[list[dict[str, object]]] = mapped_column(JSON_DOCUMENT)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class InMemoryValidationSnapshotStore:
    def __init__(self) -> None:
        self.items: dict[UUID, ValidationSnapshot] = {}

    async def add(self, snapshot: ValidationSnapshot) -> None:
        if snapshot.id in self.items:
            raise ValueError("duplicate validation snapshot")
        self.items[snapshot.id] = snapshot

    async def get(self, snapshot_id: UUID) -> ValidationSnapshot | None:
        return self.items.get(snapshot_id)

    async def list_by_project(self, project_id: UUID) -> tuple[ValidationSnapshot, ...]:
        return tuple(
            item for item in self.items.values() if item.project_id == project_id
        )


class InMemoryDatabaseBranchValidationStore:
    def __init__(self) -> None:
        self.items: dict[UUID, DatabaseBranchValidation] = {}
        self.keys: dict[tuple[UUID, str], UUID] = {}
        self._lock = asyncio.Lock()

    async def reserve(
        self, run: DatabaseBranchValidation
    ) -> tuple[DatabaseBranchValidation, bool]:
        async with self._lock:
            key = (run.organization_id, run.idempotency_key)
            existing_id = self.keys.get(key)
            if existing_id is not None:
                return self.items[existing_id], False
            self.items[run.id] = run
            self.keys[key] = run.id
            return run, True

    async def update(self, run: DatabaseBranchValidation) -> None:
        if run.id not in self.items:
            raise ValueError("database validation run was not reserved")
        self.items[run.id] = run

    async def get(self, run_id: UUID) -> DatabaseBranchValidation | None:
        return self.items.get(run_id)


class UnavailableDatabaseBranchProvider:
    """Honest production default until a live provider is configured."""

    name = "unavailable"

    async def create_branch(
        self, *, source_database_ref: str, idempotency_key: str
    ) -> ProvisionedDatabaseBranch:
        raise RuntimeError("database branch provider is not configured")

    async def execute(self, branch, command):
        raise RuntimeError("database branch provider is not configured")

    async def delete_branch(self, branch_ref: str) -> None:
        raise RuntimeError("database branch provider is not configured")


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
                        "database_validation_ids": [
                            str(item) for item in snapshot.database_validation_ids
                        ],
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
        return self._to_domain(record)

    async def list_by_project(self, project_id: UUID) -> tuple[ValidationSnapshot, ...]:
        async with self._database.transaction() as session:
            records = (
                await session.scalars(
                    select(ValidationSnapshotRecord)
                    .where(ValidationSnapshotRecord.project_id == project_id)
                    .order_by(ValidationSnapshotRecord.created_at)
                )
            ).all()
        return tuple(self._to_domain(record) for record in records)

    @staticmethod
    def _to_domain(record: ValidationSnapshotRecord) -> ValidationSnapshot:
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
            database_validation_ids=tuple(
                UUID(str(item)) for item in payload.get("database_validation_ids", ())
            ),
            status=ValidationStatus(record.status),
            created_at=_aware(record.created_at),
            expires_at=_aware(record.expires_at),
        )


class PostgresDatabaseBranchValidationStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def reserve(
        self, run: DatabaseBranchValidation
    ) -> tuple[DatabaseBranchValidation, bool]:
        try:
            async with self._database.transaction() as session:
                session.add(self._to_record(run))
                await session.flush()
            return run, True
        except IntegrityError:
            async with self._database.transaction() as session:
                existing = await session.scalar(
                    select(DatabaseBranchValidationRecord).where(
                        DatabaseBranchValidationRecord.organization_id
                        == run.organization_id,
                        DatabaseBranchValidationRecord.idempotency_key
                        == run.idempotency_key,
                    )
                )
            if existing is None:
                raise
            return self._to_domain(existing), False

    async def update(self, run: DatabaseBranchValidation) -> None:
        async with self._database.transaction() as session:
            record = await session.get(DatabaseBranchValidationRecord, run.id)
            if record is None:
                raise ValueError("database validation run was not reserved")
            record.provider_branch_ref = run.provider_branch_ref
            record.engine_version = run.engine_version
            record.status = run.status.value
            record.failure_code = run.failure_code
            record.cleanup_pending = run.cleanup_pending
            record.results = self._results(run)
            record.updated_at = run.updated_at

    async def get(self, run_id: UUID) -> DatabaseBranchValidation | None:
        async with self._database.transaction() as session:
            record = await session.get(DatabaseBranchValidationRecord, run_id)
        return self._to_domain(record) if record else None

    @classmethod
    def _to_record(cls, run: DatabaseBranchValidation) -> DatabaseBranchValidationRecord:
        return DatabaseBranchValidationRecord(
            id=run.id,
            organization_id=run.organization_id,
            project_id=run.project_id,
            repository_id=run.repository_id,
            candidate_sha=run.candidate_sha,
            source_database_ref=run.source_database_ref,
            provider=run.provider,
            provider_branch_ref=run.provider_branch_ref,
            engine_version=run.engine_version,
            request_hash=run.request_hash,
            idempotency_key=run.idempotency_key,
            status=run.status.value,
            failure_code=run.failure_code,
            cleanup_pending=run.cleanup_pending,
            results=cls._results(run),
            created_at=run.created_at,
            updated_at=run.updated_at,
        )

    @staticmethod
    def _results(run: DatabaseBranchValidation) -> list[dict[str, object]]:
        return [
            {
                "stage": item.stage.value,
                "name": item.name,
                "exit_code": item.exit_code,
                "summary": item.summary,
            }
            for item in run.results
        ]

    @staticmethod
    def _to_domain(record: DatabaseBranchValidationRecord) -> DatabaseBranchValidation:
        return DatabaseBranchValidation(
            id=record.id,
            organization_id=record.organization_id,
            project_id=record.project_id,
            repository_id=record.repository_id,
            candidate_sha=record.candidate_sha,
            source_database_ref=record.source_database_ref,
            provider=record.provider,
            provider_branch_ref=record.provider_branch_ref,
            engine_version=record.engine_version,
            request_hash=record.request_hash,
            idempotency_key=record.idempotency_key,
            status=DatabaseValidationStatus(record.status),
            failure_code=record.failure_code,
            cleanup_pending=record.cleanup_pending,
            results=tuple(
                DatabaseValidationResult(
                    stage=DatabaseValidationStage(str(item["stage"])),
                    name=str(item["name"]),
                    exit_code=int(item["exit_code"]),
                    summary=str(item.get("summary", "")),
                )
                for item in record.results
            ),
            created_at=_aware(record.created_at),
            updated_at=_aware(record.updated_at),
        )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
