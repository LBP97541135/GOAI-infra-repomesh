from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, Integer, String, UniqueConstraint, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.persistence import Database
from repomesh.persistence.base import Base

from .contracts import ActiveWorkerDispatch
from .execution_reservation import (
    ACTIVE_EXECUTION_STATES,
    WorkerExecutionReservationRecord,
    WorkerExecutionStatus,
)
from .recovery import WorkerRecoveryOperationRecord, WorkerRecoveryState

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")

TERMINAL_DISPATCH_STATUSES = frozenset({"completed", "failed", "interrupted", "input_required"})
"""Statuses a dispatch never leaves: the Runner has stopped working on that run."""

ACTIVE_DISPATCH_STATUSES = frozenset({"queued", "leased", "accepted"})
"""Statuses where the execution plane still owns the run and may still produce a result."""

_TERMINAL_EVENT_TYPES = frozenset(f"runner.{status}" for status in TERMINAL_DISPATCH_STATUSES)


class RunnerDispatchRecord(Base):
    __tablename__ = "runner_dispatches"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_runner_dispatches_idempotency"),
        {"schema": "agent_runtime"},
    )

    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    repository_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    worker_agent_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    task_payload: Mapped[dict[str, object]] = mapped_column(JSON_DOCUMENT)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    attempt: Mapped[int] = mapped_column(Integer)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assignment_attempt_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    assignment_generation: Mapped[int | None] = mapped_column(Integer)
    execution_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    execution_version: Mapped[int | None] = mapped_column(Integer)


class RunnerEventRecord(Base):
    __tablename__ = "runner_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_runner_events_sequence"),
        {"schema": "agent_runtime"},
    )

    event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(50), index=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSON_DOCUMENT)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    projection_status: Mapped[str] = mapped_column(String(20), default="accepted")
    rejection_reason: Mapped[str | None] = mapped_column(String(200))


class RunnerGatewayConflict(ValueError):
    pass


class RunnerGatewayForbidden(Exception):
    """An event written to a run that belongs to a different worker.

    Deliberately not a ``ValueError`` like its sibling above: the API turns
    those into 409, and this is a 403 — the event is well formed and the run is
    real, the credential presenting it simply does not own it.
    """


class PostgresRunnerGatewayStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def enqueue(self, payload: dict[str, object]) -> None:
        idempotency_key = str(payload.get("idempotencyKey", "")).strip()
        if not idempotency_key:
            raise RunnerGatewayConflict("runner idempotency key is required")
        existing = await self._get_by_idempotency_key(idempotency_key)
        if existing is not None:
            if existing.task_payload == payload:
                return
            raise RunnerGatewayConflict("runner idempotency key was used for a different task")
        try:
            async with self._database.transaction() as session:
                session.add(
                    RunnerDispatchRecord(
                        run_id=UUID(str(payload["runId"])),
                        organization_id=UUID(str(payload["organizationId"])),
                        project_id=UUID(str(payload["projectId"])),
                        repository_id=UUID(str(dict(payload["repository"])["repositoryId"])),
                        task_id=UUID(str(payload["taskId"])),
                        worker_agent_id=UUID(str(payload["workerAgentId"])),
                        status="queued",
                        task_payload=payload,
                        idempotency_key=idempotency_key,
                        attempt=int(payload["attempt"]),
                        lease_until=None,
                        created_at=datetime.now(UTC),
                        completed_at=None,
                        assignment_attempt_id=(
                            UUID(str(payload["assignmentAttemptId"]))
                            if payload.get("assignmentAttemptId")
                            else None
                        ),
                        assignment_generation=(
                            int(payload["assignmentGeneration"])
                            if payload.get("assignmentGeneration") is not None
                            else None
                        ),
                        execution_id=(
                            UUID(str(payload["executionId"]))
                            if payload.get("executionId")
                            else None
                        ),
                        execution_version=(
                            int(payload["executionVersion"])
                            if payload.get("executionVersion") is not None
                            else None
                        ),
                    )
                )
        except IntegrityError as error:
            replay = await self._get_by_idempotency_key(idempotency_key)
            if replay is not None and replay.task_payload == payload:
                return
            raise RunnerGatewayConflict("invalid or duplicate runner task") from error
        except (KeyError, TypeError, ValueError) as error:
            raise RunnerGatewayConflict("invalid or duplicate runner task") from error

    async def _get_by_idempotency_key(self, idempotency_key: str) -> RunnerDispatchRecord | None:
        if not idempotency_key:
            return None
        async with self._database.transaction() as session:
            return await session.scalar(
                select(RunnerDispatchRecord).where(
                    RunnerDispatchRecord.idempotency_key == idempotency_key
                )
            )

    async def lease_next(
        self, worker_agent_id: UUID | None, *, lease_seconds: int = 60
    ) -> dict[str, object] | None:
        now = datetime.now(UTC)
        async with self._database.transaction() as session:
            statement = (
                select(RunnerDispatchRecord)
                .where(
                    RunnerDispatchRecord.status.in_(("queued", "leased")),
                    or_(
                        RunnerDispatchRecord.lease_until.is_(None),
                        RunnerDispatchRecord.lease_until < now,
                    ),
                )
                .order_by(RunnerDispatchRecord.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if worker_agent_id is not None:
                statement = statement.where(RunnerDispatchRecord.worker_agent_id == worker_agent_id)
            record = await session.scalar(statement)
            if record is None:
                return None
            record.status = "leased"
            record.lease_until = now + timedelta(seconds=lease_seconds)
            return dict(record.task_payload)

    async def get_dispatch(self, run_id: UUID) -> RunnerDispatchRecord | None:
        async with self._database.transaction() as session:
            return await session.get(RunnerDispatchRecord, run_id)

    async def get_active_dispatch_for_task(
        self, task_id: UUID, *, worker_agent_id: UUID
    ) -> ActiveWorkerDispatch | None:
        """Return the newest dispatch that still owns this Worker's execution of the Task.

        Terminal dispatches are ignored: a finished run must not stop the Worker from starting a
        new attempt.
        """

        async with self._database.transaction() as session:
            record = await session.scalar(
                select(RunnerDispatchRecord)
                .where(
                    RunnerDispatchRecord.task_id == task_id,
                    RunnerDispatchRecord.worker_agent_id == worker_agent_id,
                    RunnerDispatchRecord.status.in_(sorted(ACTIVE_DISPATCH_STATUSES)),
                )
                .order_by(RunnerDispatchRecord.created_at.desc())
                .limit(1)
            )
            if record is None:
                return None
            return ActiveWorkerDispatch(
                run_id=record.run_id,
                task_id=record.task_id,
                worker_agent_id=record.worker_agent_id,
                attempt=record.attempt,
                status=record.status,
                task_payload=dict(record.task_payload),
            )

    async def list_events_for_project(
        self, project_id: UUID
    ) -> tuple[dict[str, object], ...]:
        """Chronological runner events for a project, joined to their dispatch."""

        async with self._database.transaction() as session:
            rows = (
                await session.execute(
                    select(RunnerEventRecord, RunnerDispatchRecord)
                    .join(
                        RunnerDispatchRecord,
                        RunnerEventRecord.run_id == RunnerDispatchRecord.run_id,
                    )
                    .where(RunnerDispatchRecord.project_id == project_id)
                    .order_by(RunnerEventRecord.occurred_at, RunnerEventRecord.sequence)
                )
            ).all()
        return tuple(
            {
                "event_id": event.event_id,
                "run_id": event.run_id,
                "sequence": event.sequence,
                "event_type": event.event_type,
                "occurred_at": event.occurred_at,
                "task_id": dispatch.task_id,
                "repository_id": dispatch.repository_id,
            }
            for event, dispatch in rows
        )

    async def record_event(
        self,
        event: dict[str, object],
        *,
        expected_worker_agent_id: UUID | None = None,
        projection_allowed: bool = True,
    ) -> bool:
        """Record one Runner event, optionally only for one worker's own runs.

        ``expected_worker_agent_id`` is the *authenticated* worker, never a
        field of the event: the wire schema carries no worker id, and one it
        carried would be the caller's claim rather than a fact. Ownership is
        the dispatch row's, joined here by ``runId``. ``None`` — the managed
        Runner's global credential — leases and reports for every worker, so it
        skips the check rather than failing it.
        """


        event_id = UUID(str(event["eventId"]))
        run_id = UUID(str(event["runId"]))
        try:
            async with self._database.transaction() as session:
                dispatch = await session.get(RunnerDispatchRecord, run_id)
                if dispatch is None:
                    raise RunnerGatewayConflict("runner event references an unknown run")
                if (
                    expected_worker_agent_id is not None
                    and dispatch.worker_agent_id != expected_worker_agent_id
                ):
                    raise RunnerGatewayForbidden("runner event belongs to another worker")
                self._verify_binding(dispatch, event)
                session.add(
                    RunnerEventRecord(
                        event_id=event_id,
                        run_id=run_id,
                        sequence=int(event["sequence"]),
                        event_type=str(event["eventType"]),
                        payload=event,
                        occurred_at=datetime.fromisoformat(str(event["occurredAt"])),
                        recorded_at=datetime.now(UTC),
                        projection_status=("accepted" if projection_allowed else "rejected"),
                        rejection_reason=(
                            None if projection_allowed else "stale_assignment_generation"
                        ),
                    )
                )
                event_type = str(event["eventType"])
                if event_type == "runner.accepted" and projection_allowed:
                    dispatch.status = "accepted"
                    dispatch.lease_until = None
                elif event_type in _TERMINAL_EVENT_TYPES:
                    dispatch.status = event_type.removeprefix("runner.")
                    dispatch.lease_until = None
                    dispatch.completed_at = datetime.now(UTC)
                    reservation = await session.scalar(
                        select(WorkerExecutionReservationRecord)
                        .where(
                            WorkerExecutionReservationRecord.run_id == run_id,
                            WorkerExecutionReservationRecord.status.in_(
                                ACTIVE_EXECUTION_STATES
                            ),
                        )
                        .with_for_update()
                    )
                    if reservation is not None and projection_allowed:
                        terminal = {
                            "completed": WorkerExecutionStatus.SUCCEEDED.value,
                            "failed": WorkerExecutionStatus.FAILED.value,
                            "interrupted": WorkerExecutionStatus.FAILED.value,
                            "input_required": WorkerExecutionStatus.FAILED.value,
                        }[event_type.removeprefix("runner.")]
                        reservation.status = terminal
                        reservation.lease_owner = None
                        reservation.lease_expires_at = None
                        reservation.version += 1
                        reservation.updated_at = datetime.now(UTC)
                        reservation.completed_at = datetime.now(UTC)
            return True
        except IntegrityError:
            return False
        except (KeyError, TypeError, ValueError) as error:
            if isinstance(error, RunnerGatewayConflict):
                raise
            raise RunnerGatewayConflict("invalid runner event") from error

    @staticmethod
    def _verify_binding(dispatch: RunnerDispatchRecord, event: dict[str, object]) -> None:
        expected = {
            "organizationId": dispatch.organization_id,
            "projectId": dispatch.project_id,
            "taskId": dispatch.task_id,
            "runId": dispatch.run_id,
            "attempt": dispatch.attempt,
        }
        if dispatch.assignment_attempt_id is not None:
            expected["assignmentAttemptId"] = dispatch.assignment_attempt_id
            expected["assignmentGeneration"] = dispatch.assignment_generation
        if dispatch.execution_id is not None:
            expected["executionId"] = dispatch.execution_id
            expected["executionVersion"] = dispatch.execution_version
        for field, value in expected.items():
            actual = event.get(field)
            if str(actual) != str(value):
                raise RunnerGatewayConflict(f"runner event {field} binding mismatch")

    async def projection_allowed(self, run_id: UUID) -> bool:
        async with self._database.transaction() as session:
            event = await session.scalar(
                select(RunnerEventRecord)
                .where(RunnerEventRecord.run_id == run_id)
                .order_by(RunnerEventRecord.sequence.desc())
                .limit(1)
            )
            return event is None or event.projection_status == "accepted"

    async def ensure_recovery_for_run(
        self, run_id: UUID, event: dict[str, object]
    ) -> None:
        async with self._database.transaction() as session:
            reservation = await session.scalar(
                select(WorkerExecutionReservationRecord).where(
                    WorkerExecutionReservationRecord.run_id == run_id
                )
            )
            if reservation is None:
                return
            existing = await session.scalar(
                select(WorkerRecoveryOperationRecord.id).where(
                    WorkerRecoveryOperationRecord.execution_id == reservation.id
                )
            )
            if existing is not None:
                return
            now = datetime.now(UTC)
            session.add(
                WorkerRecoveryOperationRecord(
                    id=uuid4(), execution_id=reservation.id,
                    task_id=reservation.task_id,
                    assignment_attempt_id=reservation.assignment_attempt_id,
                    assignment_generation=reservation.assignment_generation,
                    failed_worker_id=reservation.worker_agent_id,
                    state=WorkerRecoveryState.PENDING.value,
                    reason=str(event["eventType"]).removeprefix("runner."),
                    native_session_id=(
                        str(event.get("nativeSessionId"))
                        if event.get("nativeSessionId") else None
                    ),
                    attempts=0, lease_owner=None, lease_expires_at=None,
                    decision=None, error_code=None, created_at=now,
                    updated_at=now, finished_at=None,
                )
            )
