from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Index, Integer, String, and_, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.persistence import Database
from repomesh.persistence.base import Base

from .bootstrap import (
    ACTIVE_BOOTSTRAP_STATES,
    BootstrapErrorCode,
    BootstrapKind,
    BootstrapOperation,
    BootstrapPhase,
    BootstrapState,
    BootstrapTransitionError,
    assert_bootstrap_transition,
)

_ACTIVE_STATE_VALUES = tuple(state.value for state in ACTIVE_BOOTSTRAP_STATES)
_ACTIVE_STATE_SQL = "state IN ('pending', 'running', 'waiting_for_user', 'retryable_failure')"


class BootstrapOperationRecord(Base):
    __tablename__ = "bootstrap_operations"
    __table_args__ = (
        Index(
            "uq_bootstrap_operations_active_kind",
            "kind",
            unique=True,
            postgresql_where=text(_ACTIVE_STATE_SQL),
            sqlite_where=text(_ACTIVE_STATE_SQL),
        ),
        {"schema": "platform"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    phase: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    requested_by: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_detail: Mapped[str | None] = mapped_column(String(2000))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PostgresBootstrapOperationStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def ensure_requested(self, *, requested_by: UUID | None) -> BootstrapOperation:
        try:
            async with self._database.transaction() as session:
                existing = await session.scalar(self._active_statement())
                if existing is not None:
                    return self._domain(existing)
                now = datetime.now(UTC)
                record = BootstrapOperationRecord(
                    id=uuid4(),
                    kind=BootstrapKind.CONFIGURE_EXECUTION_PLANE.value,
                    state=BootstrapState.PENDING.value,
                    phase=BootstrapPhase.INSTALLING_AGENTTEAMS.value,
                    attempt=0,
                    requested_by=requested_by,
                    lease_owner=None,
                    lease_expires_at=None,
                    error_code=None,
                    error_detail=None,
                    requested_at=now,
                    started_at=None,
                    updated_at=now,
                    finished_at=None,
                )
                session.add(record)
                await session.flush()
                return self._domain(record)
        except IntegrityError:
            async with self._database.transaction() as session:
                existing = await session.scalar(self._active_statement())
                if existing is None:
                    raise
                return self._domain(existing)

    async def latest(self) -> BootstrapOperation | None:
        statement = select(BootstrapOperationRecord).order_by(
            BootstrapOperationRecord.requested_at.desc(),
            BootstrapOperationRecord.id.desc(),
        )
        async with self._database.transaction() as session:
            record = await session.scalar(statement.limit(1))
            return self._domain(record) if record is not None else None

    async def claim(
        self, lease_owner: str, *, lease_seconds: int = 300
    ) -> BootstrapOperation | None:
        self._validate_lease(lease_owner, lease_seconds)
        statement = (
            select(BootstrapOperationRecord)
            .where(
                BootstrapOperationRecord.kind
                == BootstrapKind.CONFIGURE_EXECUTION_PLANE.value,
                or_(
                    BootstrapOperationRecord.state == BootstrapState.PENDING.value,
                    and_(
                        BootstrapOperationRecord.state == BootstrapState.RUNNING.value,
                        BootstrapOperationRecord.lease_expires_at < func.now(),
                    ),
                ),
            )
            .order_by(BootstrapOperationRecord.requested_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        async with self._database.transaction() as session:
            record = await session.scalar(statement)
            if record is None:
                return None
            now = datetime.now(UTC)
            record.state = BootstrapState.RUNNING.value
            record.attempt += 1
            record.lease_owner = lease_owner
            record.lease_expires_at = now + timedelta(seconds=lease_seconds)
            record.started_at = record.started_at or now
            record.updated_at = now
            record.finished_at = None
            record.error_code = None
            record.error_detail = None
            return self._domain(record)

    async def renew(
        self, operation_id: UUID, lease_owner: str, *, lease_seconds: int = 300
    ) -> BootstrapOperation:
        self._validate_lease(lease_owner, lease_seconds)
        async with self._database.transaction() as session:
            record = await self._locked(session, operation_id)
            self._assert_lease_owner(record, lease_owner)
            now = datetime.now(UTC)
            record.lease_expires_at = now + timedelta(seconds=lease_seconds)
            record.updated_at = now
            return self._domain(record)

    async def transition(
        self,
        operation_id: UUID,
        *,
        target: BootstrapState,
        phase: BootstrapPhase,
        lease_owner: str | None = None,
        error_code: BootstrapErrorCode | None = None,
        error_detail: str | None = None,
    ) -> BootstrapOperation:
        if error_detail is not None and len(error_detail) > 2000:
            raise ValueError("bootstrap error detail exceeds 2000 characters")
        async with self._database.transaction() as session:
            record = await self._locked(session, operation_id)
            current = BootstrapState(record.state)
            assert_bootstrap_transition(current, target, phase)
            if current is BootstrapState.RUNNING:
                self._assert_lease_owner(record, lease_owner)
            now = datetime.now(UTC)
            record.state = target.value
            record.phase = phase.value
            record.updated_at = now
            record.error_code = error_code.value if error_code is not None else None
            record.error_detail = error_detail
            if target is not BootstrapState.RUNNING:
                record.lease_owner = None
                record.lease_expires_at = None
            if target in {BootstrapState.COMPLETED, BootstrapState.TERMINAL_FAILURE}:
                record.finished_at = now
            return self._domain(record)

    async def retry(self, operation_id: UUID) -> BootstrapOperation:
        async with self._database.transaction() as session:
            record = await self._locked(session, operation_id)
            current = BootstrapState(record.state)
            if current is BootstrapState.RUNNING:
                expires_at = record.lease_expires_at
                now = datetime.now(UTC)
                if expires_at is None or self._aware(expires_at) >= now:
                    raise BootstrapTransitionError("running bootstrap lease has not expired")
                record.state = BootstrapState.RETRYABLE_FAILURE.value
                current = BootstrapState.RETRYABLE_FAILURE
            if current is not BootstrapState.RETRYABLE_FAILURE:
                raise BootstrapTransitionError(
                    "only retryable bootstrap failures can be retried"
                )
            retry_phase = BootstrapPhase(record.phase)
            assert_bootstrap_transition(
                current,
                BootstrapState.PENDING,
                retry_phase,
            )
            now = datetime.now(UTC)
            record.state = BootstrapState.PENDING.value
            record.phase = retry_phase.value
            record.lease_owner = None
            record.lease_expires_at = None
            record.error_code = None
            record.error_detail = None
            record.updated_at = now
            record.finished_at = None
            return self._domain(record)

    @staticmethod
    def _active_statement():
        return (
            select(BootstrapOperationRecord)
            .where(
                BootstrapOperationRecord.kind
                == BootstrapKind.CONFIGURE_EXECUTION_PLANE.value,
                BootstrapOperationRecord.state.in_(_ACTIVE_STATE_VALUES),
            )
            .order_by(BootstrapOperationRecord.requested_at)
            .limit(1)
        )

    @staticmethod
    async def _locked(session, operation_id: UUID) -> BootstrapOperationRecord:
        record = await session.scalar(
            select(BootstrapOperationRecord)
            .where(BootstrapOperationRecord.id == operation_id)
            .with_for_update()
        )
        if record is None:
            raise KeyError(f"bootstrap operation {operation_id} does not exist")
        return record

    @staticmethod
    def _assert_lease_owner(record: BootstrapOperationRecord, lease_owner: str | None) -> None:
        if not lease_owner or record.lease_owner != lease_owner:
            raise BootstrapTransitionError("bootstrap operation is owned by another lease")

    @staticmethod
    def _validate_lease(lease_owner: str, lease_seconds: int) -> None:
        if not lease_owner.strip() or len(lease_owner) > 128:
            raise ValueError("lease owner must contain 1-128 characters")
        if lease_seconds < 1:
            raise ValueError("lease seconds must be positive")

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    @classmethod
    def _domain(cls, record: BootstrapOperationRecord) -> BootstrapOperation:
        return BootstrapOperation(
            id=record.id,
            kind=BootstrapKind(record.kind),
            state=BootstrapState(record.state),
            phase=BootstrapPhase(record.phase),
            attempt=record.attempt,
            requested_by=record.requested_by,
            lease_owner=record.lease_owner,
            lease_expires_at=record.lease_expires_at,
            error_code=BootstrapErrorCode(record.error_code) if record.error_code else None,
            error_detail=record.error_detail,
            requested_at=record.requested_at,
            started_at=record.started_at,
            updated_at=record.updated_at,
            finished_at=record.finished_at,
        )
