from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, Index, Integer, String, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.persistence import Database
from repomesh.persistence.base import Base

from .contracts import (
    ReservedWorkerExecution,
    WorkerExecutionReservation,
    WorkerExecutionStatus,
)

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")
ACTIVE_EXECUTION_STATES = frozenset(
    {WorkerExecutionStatus.PREPARING.value, WorkerExecutionStatus.RUNNING.value}
)
_ACTIVE_SQL = "status IN ('preparing', 'running')"


class WorkerExecutionReservationConflict(RuntimeError):
    pass


class WorkerCapacityUnavailable(WorkerExecutionReservationConflict):
    pass


class WorkerExecutionReservationRecord(Base):
    __tablename__ = "worker_execution_reservations"
    __table_args__ = (
        Index(
            "uq_worker_execution_reservations_active_task",
            "task_id",
            unique=True,
            postgresql_where=text(_ACTIVE_SQL),
            sqlite_where=text(_ACTIVE_SQL),
        ),
        Index(
            "uq_worker_execution_reservations_active_worker",
            "worker_agent_id",
            unique=True,
            postgresql_where=text(_ACTIVE_SQL),
            sqlite_where=text(_ACTIVE_SQL),
        ),
        {"schema": "agent_runtime"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    repository_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    worker_agent_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), unique=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    task_payload: Mapped[dict[str, object] | None] = mapped_column(JSON_DOCUMENT)
    error_detail: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PostgresWorkerExecutionReservationStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def reserve(
        self,
        *,
        organization_id: UUID,
        project_id: UUID,
        repository_id: UUID,
        task_id: UUID,
        worker_agent_id: UUID,
        lease_owner: str,
        lease_seconds: int,
    ) -> ReservedWorkerExecution:
        self._validate_lease(lease_owner, lease_seconds)
        try:
            async with self._database.transaction() as session:
                existing = await session.scalar(self._active_task(task_id).with_for_update())
                if existing is not None:
                    self._assert_binding(
                        existing,
                        organization_id,
                        project_id,
                        repository_id,
                        worker_agent_id,
                    )
                    if not self._expired(existing):
                        return ReservedWorkerExecution(self._domain(existing), False)
                    self._expire(existing)
                    await session.flush()
                busy = await session.scalar(
                    select(WorkerExecutionReservationRecord)
                    .where(
                        WorkerExecutionReservationRecord.worker_agent_id == worker_agent_id,
                        WorkerExecutionReservationRecord.status.in_(ACTIVE_EXECUTION_STATES),
                    )
                    .with_for_update()
                    .limit(1)
                )
                if busy is not None:
                    if not self._expired(busy):
                        raise WorkerCapacityUnavailable(
                            "worker already has an active execution"
                        )
                    self._expire(busy)
                    await session.flush()
                now = datetime.now(UTC)
                attempt = 1 + int(
                    await session.scalar(
                        select(WorkerExecutionReservationRecord.attempt)
                        .where(WorkerExecutionReservationRecord.task_id == task_id)
                        .order_by(WorkerExecutionReservationRecord.attempt.desc())
                        .limit(1)
                    )
                    or 0
                )
                record = WorkerExecutionReservationRecord(
                    id=uuid4(),
                    organization_id=organization_id,
                    project_id=project_id,
                    repository_id=repository_id,
                    task_id=task_id,
                    worker_agent_id=worker_agent_id,
                    run_id=uuid4(),
                    status=WorkerExecutionStatus.PREPARING.value,
                    attempt=attempt,
                    version=1,
                    lease_owner=lease_owner,
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    task_payload=None,
                    error_detail=None,
                    created_at=now,
                    updated_at=now,
                    completed_at=None,
                )
                session.add(record)
                await session.flush()
                return ReservedWorkerExecution(self._domain(record), True)
        except IntegrityError:
            existing = await self.get_active(task_id)
            if existing is not None:
                self._assert_domain_binding(
                    existing,
                    organization_id,
                    project_id,
                    repository_id,
                    worker_agent_id,
                )
                return ReservedWorkerExecution(existing, False)
            raise WorkerCapacityUnavailable("worker already has an active execution") from None

    async def get_active(self, task_id: UUID) -> WorkerExecutionReservation | None:
        async with self._database.transaction() as session:
            record = await session.scalar(self._active_task(task_id))
            return self._domain(record) if record is not None else None

    async def bind_payload(
        self,
        reservation_id: UUID,
        payload,
        *,
        lease_owner: str,
        fencing_version: int,
    ) -> WorkerExecutionReservation:
        async with self._database.transaction() as session:
            record = await self._owned(
                session, reservation_id, lease_owner, fencing_version
            )
            record.status = WorkerExecutionStatus.RUNNING.value
            record.task_payload = dict(payload)
            record.version += 1
            record.updated_at = datetime.now(UTC)
            return self._domain(record)

    async def renew(
        self,
        reservation_id: UUID,
        *,
        lease_owner: str,
        fencing_version: int,
        lease_seconds: int,
    ) -> WorkerExecutionReservation:
        self._validate_lease(lease_owner, lease_seconds)
        async with self._database.transaction() as session:
            record = await self._owned(
                session, reservation_id, lease_owner, fencing_version
            )
            now = datetime.now(UTC)
            record.lease_expires_at = now + timedelta(seconds=lease_seconds)
            record.updated_at = now
            return self._domain(record)

    async def fail_preparation(
        self,
        reservation_id: UUID,
        error: str,
        *,
        lease_owner: str,
        fencing_version: int,
    ) -> WorkerExecutionReservation:
        async with self._database.transaction() as session:
            record = await self._owned(
                session, reservation_id, lease_owner, fencing_version
            )
            now = datetime.now(UTC)
            record.status = WorkerExecutionStatus.FAILED.value
            record.error_detail = error[:2000]
            record.lease_owner = None
            record.lease_expires_at = None
            record.version += 1
            record.updated_at = now
            record.completed_at = now
            return self._domain(record)

    @staticmethod
    def _active_task(task_id: UUID):
        return (
            select(WorkerExecutionReservationRecord)
            .where(
                WorkerExecutionReservationRecord.task_id == task_id,
                WorkerExecutionReservationRecord.status.in_(ACTIVE_EXECUTION_STATES),
            )
            .order_by(WorkerExecutionReservationRecord.attempt.desc())
            .limit(1)
        )

    @staticmethod
    async def _owned(session, reservation_id, owner, version):
        record = await session.scalar(
            select(WorkerExecutionReservationRecord)
            .where(WorkerExecutionReservationRecord.id == reservation_id)
            .with_for_update()
        )
        if (
            record is None
            or record.status not in ACTIVE_EXECUTION_STATES
            or record.lease_owner != owner
            or record.version != version
        ):
            raise WorkerExecutionReservationConflict("execution reservation ownership changed")
        if record.lease_expires_at is None or _aware(record.lease_expires_at) <= datetime.now(UTC):
            raise WorkerExecutionReservationConflict("execution reservation lease expired")
        return record

    @staticmethod
    def _assert_binding(record, organization_id, project_id, repository_id, worker_agent_id):
        if (
            record.organization_id != organization_id
            or record.project_id != project_id
            or record.repository_id != repository_id
            or record.worker_agent_id != worker_agent_id
        ):
            raise WorkerExecutionReservationConflict(
                "active execution reservation has a different task binding"
            )

    @classmethod
    def _assert_domain_binding(
        cls, record, organization_id, project_id, repository_id, worker_agent_id
    ):
        cls._assert_binding(
            record, organization_id, project_id, repository_id, worker_agent_id
        )

    @staticmethod
    def _validate_lease(owner: str, seconds: int) -> None:
        if not owner.strip() or len(owner) > 128:
            raise ValueError("lease owner must contain 1-128 characters")
        if seconds < 1:
            raise ValueError("lease seconds must be positive")

    @staticmethod
    def _expired(record) -> bool:
        return (
            record.lease_expires_at is None
            or _aware(record.lease_expires_at) <= datetime.now(UTC)
        )

    @staticmethod
    def _expire(record) -> None:
        now = datetime.now(UTC)
        record.status = WorkerExecutionStatus.EXPIRED.value
        record.lease_owner = None
        record.lease_expires_at = None
        record.version += 1
        record.updated_at = now
        record.completed_at = now

    @staticmethod
    def _domain(record) -> WorkerExecutionReservation:
        return WorkerExecutionReservation(
            id=record.id,
            organization_id=record.organization_id,
            project_id=record.project_id,
            repository_id=record.repository_id,
            task_id=record.task_id,
            worker_agent_id=record.worker_agent_id,
            run_id=record.run_id,
            status=WorkerExecutionStatus(record.status),
            attempt=record.attempt,
            version=record.version,
            lease_owner=record.lease_owner,
            lease_expires_at=record.lease_expires_at,
            task_payload=dict(record.task_payload) if record.task_payload else None,
            error_detail=record.error_detail,
            created_at=record.created_at,
            updated_at=record.updated_at,
            completed_at=record.completed_at,
        )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
