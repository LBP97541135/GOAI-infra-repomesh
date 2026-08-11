from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import JSON, DateTime, Integer, String, UniqueConstraint, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.persistence import Database
from repomesh.persistence.base import Base

from .contracts import ActiveWorkerDispatch

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


class RunnerGatewayConflict(ValueError):
    pass


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

    async def record_event(self, event: dict[str, object]) -> bool:
        event_id = UUID(str(event["eventId"]))
        run_id = UUID(str(event["runId"]))
        try:
            async with self._database.transaction() as session:
                dispatch = await session.get(RunnerDispatchRecord, run_id)
                if dispatch is None:
                    raise RunnerGatewayConflict("runner event references an unknown run")
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
                    )
                )
                event_type = str(event["eventType"])
                if event_type == "runner.accepted":
                    dispatch.status = "accepted"
                    dispatch.lease_until = None
                elif event_type in _TERMINAL_EVENT_TYPES:
                    dispatch.status = event_type.removeprefix("runner.")
                    dispatch.lease_until = None
                    dispatch.completed_at = datetime.now(UTC)
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
        for field, value in expected.items():
            actual = event.get(field)
            if str(actual) != str(value):
                raise RunnerGatewayConflict(f"runner event {field} binding mismatch")
