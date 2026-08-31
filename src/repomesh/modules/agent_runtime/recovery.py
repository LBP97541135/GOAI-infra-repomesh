import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.persistence import Database
from repomesh.persistence.base import Base

logger = logging.getLogger(__name__)


class WorkerRecoveryState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ESCALATED = "escalated"
    FAILED = "failed"


class WorkerRecoveryDecision(StrEnum):
    NO_ACTION = "no_action"
    RESUME = "resume"
    REASSIGN = "reassign"
    ESCALATE = "escalate"


@dataclass(frozen=True, slots=True)
class WorkerRecoveryOperation:
    id: UUID
    execution_id: UUID
    task_id: UUID
    assignment_attempt_id: UUID | None
    assignment_generation: int | None
    failed_worker_id: UUID
    state: WorkerRecoveryState
    reason: str
    native_session_id: str | None
    attempts: int
    lease_owner: str | None
    decision: WorkerRecoveryDecision | None


class WorkerRecoveryOperationRecord(Base):
    __tablename__ = "worker_recovery_operations"
    __table_args__ = (
        UniqueConstraint("execution_id", name="uq_worker_recovery_operations_execution"),
        {"schema": "agent_runtime"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    execution_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), unique=True)
    task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    assignment_attempt_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    assignment_generation: Mapped[int | None] = mapped_column(Integer)
    failed_worker_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    state: Mapped[str] = mapped_column(String(30), index=True)
    reason: Mapped[str] = mapped_column(String(40))
    native_session_id: Mapped[str | None] = mapped_column(String(512))
    attempts: Mapped[int] = mapped_column(Integer)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision: Mapped[str | None] = mapped_column(String(30))
    error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PostgresWorkerRecoveryStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def ensure(
        self,
        *,
        execution_id: UUID,
        task_id: UUID,
        assignment_attempt_id: UUID | None,
        assignment_generation: int | None,
        failed_worker_id: UUID,
        reason: str,
        native_session_id: str | None,
    ) -> WorkerRecoveryOperation:
        try:
            async with self._database.transaction() as session:
                existing = await session.scalar(
                    select(WorkerRecoveryOperationRecord).where(
                        WorkerRecoveryOperationRecord.execution_id == execution_id
                    )
                )
                if existing is not None:
                    return self._domain(existing)
                now = datetime.now(UTC)
                record = WorkerRecoveryOperationRecord(
                    id=uuid4(), execution_id=execution_id, task_id=task_id,
                    assignment_attempt_id=assignment_attempt_id,
                    assignment_generation=assignment_generation,
                    failed_worker_id=failed_worker_id,
                    state=WorkerRecoveryState.PENDING.value, reason=reason,
                    native_session_id=native_session_id, attempts=0, lease_owner=None,
                    lease_expires_at=None, decision=None, error_code=None,
                    created_at=now, updated_at=now, finished_at=None,
                )
                session.add(record)
                await session.flush()
                return self._domain(record)
        except IntegrityError:
            async with self._database.transaction() as session:
                record = await session.scalar(
                    select(WorkerRecoveryOperationRecord).where(
                        WorkerRecoveryOperationRecord.execution_id == execution_id
                    )
                )
                if record is None:
                    raise
                return self._domain(record)

    async def claim(self, owner: str, *, lease_seconds: int = 60):
        if not owner.strip() or len(owner) > 128 or lease_seconds < 1:
            raise ValueError("invalid Worker recovery lease")
        now = datetime.now(UTC)
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(WorkerRecoveryOperationRecord)
                .where(
                    or_(
                        WorkerRecoveryOperationRecord.state
                        == WorkerRecoveryState.PENDING.value,
                        (
                            (WorkerRecoveryOperationRecord.state
                             == WorkerRecoveryState.RUNNING.value)
                            & (WorkerRecoveryOperationRecord.lease_expires_at < now)
                        ),
                    )
                )
                .order_by(WorkerRecoveryOperationRecord.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if record is None:
                return None
            record.state = WorkerRecoveryState.RUNNING.value
            record.attempts += 1
            record.lease_owner = owner
            record.lease_expires_at = now + timedelta(seconds=lease_seconds)
            record.updated_at = now
            return self._domain(record)

    async def finish(
        self,
        operation_id: UUID,
        owner: str,
        decision: WorkerRecoveryDecision,
        *,
        error_code: str | None = None,
    ) -> WorkerRecoveryOperation:
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(WorkerRecoveryOperationRecord)
                .where(WorkerRecoveryOperationRecord.id == operation_id)
                .with_for_update()
            )
            if (
                record is None
                or record.lease_owner != owner
                or record.lease_expires_at is None
                or _aware(record.lease_expires_at) <= datetime.now(UTC)
            ):
                raise RuntimeError("worker recovery lease ownership changed")
            now = datetime.now(UTC)
            record.state = (
                WorkerRecoveryState.ESCALATED.value
                if decision is WorkerRecoveryDecision.ESCALATE
                else WorkerRecoveryState.COMPLETED.value
            )
            record.decision = decision.value
            record.error_code = error_code
            record.lease_owner = None
            record.lease_expires_at = None
            record.updated_at = now
            record.finished_at = now
            return self._domain(record)

    async def recent_failures(
        self, worker_id: UUID, *, window_hours: int = 24
    ) -> int:
        since = datetime.now(UTC) - timedelta(hours=window_hours)
        async with self._database.transaction() as session:
            return int(
                await session.scalar(
                    select(func.count(WorkerRecoveryOperationRecord.id)).where(
                        WorkerRecoveryOperationRecord.failed_worker_id == worker_id,
                        WorkerRecoveryOperationRecord.created_at >= since,
                    )
                )
                or 0
            )

    async def list_all(self) -> tuple[WorkerRecoveryOperation, ...]:
        async with self._database.transaction() as session:
            records = (
                await session.scalars(
                    select(WorkerRecoveryOperationRecord).order_by(
                        WorkerRecoveryOperationRecord.created_at
                    )
                )
            ).all()
            return tuple(self._domain(record) for record in records)

    async def retry(
        self, operation_id: UUID, decision: WorkerRecoveryDecision
    ) -> WorkerRecoveryOperation:
        if decision not in {
            WorkerRecoveryDecision.RESUME,
            WorkerRecoveryDecision.REASSIGN,
        }:
            raise ValueError("Worker recovery retry must request resume or reassign")
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(WorkerRecoveryOperationRecord)
                .where(WorkerRecoveryOperationRecord.id == operation_id)
                .with_for_update()
            )
            if record is None:
                raise KeyError("Worker recovery operation does not exist")
            record.state = WorkerRecoveryState.PENDING.value
            record.decision = decision.value
            record.lease_owner = None
            record.lease_expires_at = None
            record.error_code = None
            record.updated_at = datetime.now(UTC)
            record.finished_at = None
            return self._domain(record)

    @staticmethod
    def _domain(record: WorkerRecoveryOperationRecord) -> WorkerRecoveryOperation:
        return WorkerRecoveryOperation(
            id=record.id, execution_id=record.execution_id, task_id=record.task_id,
            assignment_attempt_id=record.assignment_attempt_id,
            assignment_generation=record.assignment_generation,
            failed_worker_id=record.failed_worker_id,
            state=WorkerRecoveryState(record.state), reason=record.reason,
            native_session_id=record.native_session_id, attempts=record.attempts,
            lease_owner=record.lease_owner,
            decision=WorkerRecoveryDecision(record.decision) if record.decision else None,
        )


@dataclass(frozen=True, slots=True)
class WorkerRecoveryCandidate:
    worker_id: UUID
    active_executions: int = 0
    recent_failures: int = 0


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def select_replacement_worker(
    candidates: Sequence[WorkerRecoveryCandidate], *, failed_worker_id: UUID
) -> WorkerRecoveryCandidate | None:
    eligible = [item for item in candidates if item.worker_id != failed_worker_id]
    return min(
        eligible,
        key=lambda item: (item.active_executions, item.recent_failures, str(item.worker_id)),
        default=None,
    )


class WorkerRecoveryReconciler:
    def __init__(
        self,
        store: PostgresWorkerRecoveryStore,
        decide: Callable[[WorkerRecoveryOperation], Awaitable[WorkerRecoveryDecision]],
        *,
        owner: str,
        interval_seconds: float = 15,
        discover: Callable[[], Awaitable[None]] | None = None,
        metrics: "WorkerRecoveryMetrics | None" = None,
    ) -> None:
        self._store = store
        self._decide = decide
        self._owner = owner
        self._interval_seconds = interval_seconds
        self._discover = discover
        self._metrics = metrics or WorkerRecoveryMetrics()
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def run_once(self) -> bool:
        if self._discover is not None:
            await self._discover()
        operation = await self._store.claim(self._owner)
        if operation is None:
            return False
        decision = await self._decide(operation)
        await self._store.finish(operation.id, self._owner, decision)
        self._metrics.record(decision)
        logger.info(
            "Worker recovery completed task_id=%s decision=%s",
            operation.task_id,
            decision.value,
        )
        return True

    async def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="worker-recovery-reconciler")

    async def close(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        await self._task
        self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception:
                logger.exception("Worker recovery reconciliation failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), self._interval_seconds)


class WorkerRecoveryMetrics:
    def __init__(self) -> None:
        self.detected = 0
        self.resumed = 0
        self.reassigned = 0
        self.escalated = 0
        self.no_action = 0

    def record(self, decision: WorkerRecoveryDecision) -> None:
        self.detected += 1
        if decision is WorkerRecoveryDecision.RESUME:
            self.resumed += 1
        elif decision is WorkerRecoveryDecision.REASSIGN:
            self.reassigned += 1
        elif decision is WorkerRecoveryDecision.ESCALATE:
            self.escalated += 1
        else:
            self.no_action += 1

    def snapshot(self) -> dict[str, int]:
        return {
            "worker_recovery_detected_total": self.detected,
            "worker_execution_resumed_total": self.resumed,
            "worker_task_reassigned_total": self.reassigned,
            "worker_recovery_escalated_total": self.escalated,
            "worker_recovery_no_action_total": self.no_action,
        }
