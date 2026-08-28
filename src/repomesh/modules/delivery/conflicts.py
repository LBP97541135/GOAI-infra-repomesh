from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Index, Integer, String, Text, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.persistence import Database
from repomesh.persistence.base import Base

from .domain import DeliveryConflict


class DeliveryConflictKind(StrEnum):
    BASE_DRIFT = "base_drift"
    CONTENT_CONFLICT = "content_conflict"


class DeliveryConflictCaseStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    ESCALATED = "escalated"


@dataclass(frozen=True, slots=True)
class DeliveryConflictCase:
    id: UUID
    change_set_id: UUID
    project_id: UUID
    repository_id: UUID
    candidate_head_sha: str
    kind: DeliveryConflictKind
    expected_base_sha: str
    observed_base_sha: str
    detail: str
    status: DeliveryConflictCaseStatus
    repair_task_id: UUID | None
    version: int
    detected_at: datetime
    updated_at: datetime
    resolved_at: datetime | None


class DeliveryConflictCaseRecord(Base):
    __tablename__ = "conflict_cases"
    __table_args__ = (
        Index(
            "uq_delivery_conflict_cases_active_repository",
            "change_set_id", "repository_id", unique=True,
            postgresql_where=text("status = 'open'"),
            sqlite_where=text("status = 'open'"),
        ),
        {"schema": "delivery"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    change_set_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    repository_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    candidate_head_sha: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    expected_base_sha: Mapped[str] = mapped_column(String(64))
    observed_base_sha: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), index=True)
    repair_task_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PostgresDeliveryConflictCaseStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def ensure(
        self, *, change_set_id: UUID, project_id: UUID, repository_id: UUID,
        candidate_head_sha: str, kind: DeliveryConflictKind,
        expected_base_sha: str, observed_base_sha: str, detail: str,
    ) -> DeliveryConflictCase:
        try:
            async with self._database.transaction() as session:
                record = await session.scalar(
                    self._active_statement(change_set_id, repository_id).with_for_update()
                )
                now = datetime.now(UTC)
                if record is not None:
                    if record.candidate_head_sha != candidate_head_sha:
                        record.status = DeliveryConflictCaseStatus.RESOLVED.value
                        record.resolved_at = now
                    else:
                        record.kind = kind.value
                        record.observed_base_sha = observed_base_sha
                        record.detail = detail[:2000]
                        record.version += 1
                        record.updated_at = now
                        return self._domain(record)
                created = DeliveryConflictCaseRecord(
                    id=uuid4(), change_set_id=change_set_id, project_id=project_id,
                    repository_id=repository_id, candidate_head_sha=candidate_head_sha,
                    kind=kind.value, expected_base_sha=expected_base_sha,
                    observed_base_sha=observed_base_sha, detail=detail[:2000],
                    status=DeliveryConflictCaseStatus.OPEN.value, repair_task_id=None,
                    version=1, detected_at=now, updated_at=now, resolved_at=None,
                )
                session.add(created)
                await session.flush()
                return self._domain(created)
        except IntegrityError as error:
            active = await self.active_for(change_set_id, repository_id)
            if active is not None:
                return active
            raise DeliveryConflict("delivery conflict case changed concurrently") from error

    async def active_for(
        self, change_set_id: UUID, repository_id: UUID
    ) -> DeliveryConflictCase | None:
        async with self._database.transaction() as session:
            record = await session.scalar(self._active_statement(change_set_id, repository_id))
            return self._domain(record) if record is not None else None

    async def set_repair_task(
        self, case_id: UUID, task_id: UUID
    ) -> DeliveryConflictCase:
        async with self._database.transaction() as session:
            record = await session.get(DeliveryConflictCaseRecord, case_id)
            if record is None or record.status != DeliveryConflictCaseStatus.OPEN.value:
                raise DeliveryConflict("delivery conflict case is not open")
            if record.repair_task_id is not None and record.repair_task_id != task_id:
                raise DeliveryConflict("delivery conflict case already has another repair task")
            record.repair_task_id = task_id
            record.version += 1
            record.updated_at = datetime.now(UTC)
            return self._domain(record)

    async def resolve_for_revision(
        self, change_set_id: UUID, repository_id: UUID, previous_head_sha: str
    ) -> None:
        now = datetime.now(UTC)
        async with self._database.transaction() as session:
            await session.execute(
                update(DeliveryConflictCaseRecord)
                .where(
                    DeliveryConflictCaseRecord.change_set_id == change_set_id,
                    DeliveryConflictCaseRecord.repository_id == repository_id,
                    DeliveryConflictCaseRecord.candidate_head_sha == previous_head_sha,
                    DeliveryConflictCaseRecord.status == DeliveryConflictCaseStatus.OPEN.value,
                )
                .values(
                    status=DeliveryConflictCaseStatus.RESOLVED.value,
                    resolved_at=now, updated_at=now,
                    version=DeliveryConflictCaseRecord.version + 1,
                )
            )

    async def list_for_change_set(self, change_set_id: UUID):
        async with self._database.transaction() as session:
            records = (
                await session.scalars(
                    select(DeliveryConflictCaseRecord)
                    .where(DeliveryConflictCaseRecord.change_set_id == change_set_id)
                    .order_by(DeliveryConflictCaseRecord.detected_at)
                )
            ).all()
            return tuple(self._domain(record) for record in records)

    @staticmethod
    def _active_statement(change_set_id, repository_id):
        return select(DeliveryConflictCaseRecord).where(
            DeliveryConflictCaseRecord.change_set_id == change_set_id,
            DeliveryConflictCaseRecord.repository_id == repository_id,
            DeliveryConflictCaseRecord.status == DeliveryConflictCaseStatus.OPEN.value,
        )

    @staticmethod
    def _domain(record) -> DeliveryConflictCase:
        return DeliveryConflictCase(
            id=record.id, change_set_id=record.change_set_id, project_id=record.project_id,
            repository_id=record.repository_id, candidate_head_sha=record.candidate_head_sha,
            kind=DeliveryConflictKind(record.kind), expected_base_sha=record.expected_base_sha,
            observed_base_sha=record.observed_base_sha, detail=record.detail,
            status=DeliveryConflictCaseStatus(record.status), repair_task_id=record.repair_task_id,
            version=record.version, detected_at=record.detected_at,
            updated_at=record.updated_at, resolved_at=record.resolved_at,
        )
