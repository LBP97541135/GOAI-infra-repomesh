"""Attempt and event stores for hosted-native rounds (spec §5.3.1, revision 0056).

Two implementations of ``HostedNativeAttemptStore``: the Postgres one over the
two ``agent_runtime`` tables the 0056 migration creates, and an in-memory one
for the round's and observer's unit tests. Both enforce the same two
invariants the schema does — one open attempt per task (partial unique index
over ``OPEN_PHASES_SQL``) and one event per ``(attempt_id, kind, marker)``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import JSON, DateTime, Index, Integer, String, UniqueConstraint, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.persistence import Database
from repomesh.persistence.base import Base

from .contracts import (
    OPEN_PHASES_SQL,
    TERMINAL_PHASES,
    AttemptPhase,
    EventKind,
    HostedNativeAttempt,
    HostedNativeAttemptStore,
    HostedNativeConflict,
    HostedNativeEvent,
    ReviewVerdict,
    SubmitStatus,
)

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")

OPEN_PHASE_VALUES: tuple[str, ...] = tuple(
    phase.value for phase in AttemptPhase if phase not in TERMINAL_PHASES
)
"""The phases ``OPEN_PHASES_SQL`` names, as the queries spell them. The unit
tests pin that the two agree, so the index and the reads cannot drift apart."""


class HostedNativeAttemptRecord(Base):
    """``agent_runtime.hosted_native_attempts``: one row per copaw-native task
    directory (D-8). The partial unique index is D-6's "one open attempt per
    task"; a terminal row (``verified`` / ``failed`` / ``blocked`` / ``fenced``)
    leaves the index and frees the task for its next generation (D-9)."""

    __tablename__ = "hosted_native_attempts"
    __table_args__ = (
        Index(
            "uq_hosted_native_attempts_open_task",
            "task_id",
            unique=True,
            postgresql_where=text(OPEN_PHASES_SQL),
            sqlite_where=text(OPEN_PHASES_SQL),
        ),
        {"schema": "agent_runtime"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    worker_agent_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    leader_agent_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    team_name: Mapped[str] = mapped_column(String(200), nullable=False)
    room_id: Mapped[str] = mapped_column(String(255), index=True)
    assignment_attempt_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    execution_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    phase: Mapped[str] = mapped_column(String(30), index=True)
    package_dir: Mapped[str] = mapped_column(String(500), nullable=False)
    base_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    review_dir: Mapped[str | None] = mapped_column(String(500))
    budget_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    review_budget_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submit_status: Mapped[str | None] = mapped_column(String(30))
    review_verdict: Mapped[str | None] = mapped_column(String(20))
    verification_run_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    fenced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fence_reason: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HostedNativeEventRecord(Base):
    """``agent_runtime.hosted_native_events``: the observer's inbox, one row per
    observation, keyed by ``(attempt_id, kind, marker)`` the way
    ``delivery.scm_observations`` is keyed by its external fact."""

    __tablename__ = "hosted_native_events"
    __table_args__ = (
        UniqueConstraint(
            "attempt_id", "kind", "marker", name="uq_hosted_native_events_observation"
        ),
        {"schema": "agent_runtime"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    attempt_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    marker: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON_DOCUMENT, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PostgresHostedNativeAttemptStore(HostedNativeAttemptStore):
    """The production store. Both refusals come from the schema: the partial
    unique index turns a second open attempt into ``IntegrityError`` and the
    events constraint turns a repeated observation into one, so the store has
    no read-then-write window of its own."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def add(self, attempt: HostedNativeAttempt) -> None:
        try:
            async with self._database.transaction() as session:
                session.add(_attempt_record(attempt))
        except IntegrityError as error:
            if await self.get(attempt.id) is not None:
                raise HostedNativeConflict(f"attempt {attempt.id} already exists") from error
            raise HostedNativeConflict(
                f"task {attempt.task_id} already has an open attempt"
            ) from error

    async def get(self, attempt_id: UUID) -> HostedNativeAttempt | None:
        async with self._database.transaction() as session:
            record = await session.get(HostedNativeAttemptRecord, attempt_id)
            return _attempt_domain(record) if record is not None else None

    async def get_open_for_task(self, task_id: UUID) -> HostedNativeAttempt | None:
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(HostedNativeAttemptRecord)
                .where(
                    HostedNativeAttemptRecord.task_id == task_id,
                    HostedNativeAttemptRecord.phase.in_(OPEN_PHASE_VALUES),
                )
                .limit(1)
            )
            return _attempt_domain(record) if record is not None else None

    async def list_open(self) -> tuple[HostedNativeAttempt, ...]:
        async with self._database.transaction() as session:
            records = await session.scalars(
                select(HostedNativeAttemptRecord)
                .where(HostedNativeAttemptRecord.phase.in_(OPEN_PHASE_VALUES))
                .order_by(HostedNativeAttemptRecord.notified_at, HostedNativeAttemptRecord.id)
            )
            return tuple(_attempt_domain(record) for record in records)

    async def list_for_task(self, task_id: UUID) -> tuple[HostedNativeAttempt, ...]:
        async with self._database.transaction() as session:
            records = await session.scalars(
                select(HostedNativeAttemptRecord)
                .where(HostedNativeAttemptRecord.task_id == task_id)
                .order_by(
                    HostedNativeAttemptRecord.generation,
                    HostedNativeAttemptRecord.created_at,
                    HostedNativeAttemptRecord.id,
                )
            )
            return tuple(_attempt_domain(record) for record in records)

    async def save(self, attempt: HostedNativeAttempt) -> None:
        try:
            async with self._database.transaction() as session:
                record = await session.get(HostedNativeAttemptRecord, attempt.id)
                if record is None:
                    raise HostedNativeConflict(f"attempt {attempt.id} does not exist")
                _apply_attempt(record, attempt)
        except IntegrityError as error:
            raise HostedNativeConflict(
                f"task {attempt.task_id} already has an open attempt"
            ) from error

    async def record_event(self, event: HostedNativeEvent) -> bool:
        try:
            async with self._database.transaction() as session:
                session.add(
                    HostedNativeEventRecord(
                        id=event.id,
                        attempt_id=event.attempt_id,
                        kind=event.kind.value,
                        marker=event.marker,
                        payload=dict(event.payload),
                        observed_at=event.observed_at,
                        applied_at=event.applied_at,
                    )
                )
        except IntegrityError:
            return False
        return True

    async def find_event(
        self, attempt_id: UUID, kind: EventKind, marker: str
    ) -> HostedNativeEvent | None:
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(HostedNativeEventRecord).where(
                    HostedNativeEventRecord.attempt_id == attempt_id,
                    HostedNativeEventRecord.kind == kind.value,
                    HostedNativeEventRecord.marker == marker,
                )
            )
            return _event_domain(record) if record is not None else None

    async def mark_applied(self, event_id: UUID, *, applied_at: datetime) -> None:
        async with self._database.transaction() as session:
            record = await session.get(HostedNativeEventRecord, event_id)
            if record is None:
                return
            record.applied_at = applied_at

    async def list_events(self, attempt_id: UUID) -> tuple[HostedNativeEvent, ...]:
        async with self._database.transaction() as session:
            records = await session.scalars(
                select(HostedNativeEventRecord)
                .where(HostedNativeEventRecord.attempt_id == attempt_id)
                .order_by(HostedNativeEventRecord.observed_at, HostedNativeEventRecord.id)
            )
            return tuple(_event_domain(record) for record in records)


def _attempt_record(attempt: HostedNativeAttempt) -> HostedNativeAttemptRecord:
    record = HostedNativeAttemptRecord(id=attempt.id)
    _apply_attempt(record, attempt)
    return record


def _apply_attempt(record: HostedNativeAttemptRecord, attempt: HostedNativeAttempt) -> None:
    """Copy every column of *attempt* onto *record* (the full-row update ``save`` promises)."""

    record.task_id = attempt.task_id
    record.worker_agent_id = attempt.worker_agent_id
    record.leader_agent_id = attempt.leader_agent_id
    record.team_name = attempt.team_name
    record.room_id = attempt.room_id
    record.assignment_attempt_id = attempt.assignment_attempt_id
    record.generation = attempt.generation
    record.execution_id = attempt.execution_id
    record.phase = attempt.phase.value
    record.package_dir = attempt.package_dir
    record.base_sha = attempt.base_sha
    record.review_dir = attempt.review_dir
    record.budget_until = attempt.budget_until
    record.review_budget_until = attempt.review_budget_until
    record.notified_at = attempt.notified_at
    record.acknowledged_at = attempt.acknowledged_at
    record.submitted_at = attempt.submitted_at
    record.submit_status = attempt.submit_status.value if attempt.submit_status else None
    record.review_verdict = attempt.review_verdict.value if attempt.review_verdict else None
    record.verification_run_id = attempt.verification_run_id
    record.fenced_at = attempt.fenced_at
    record.fence_reason = attempt.fence_reason
    record.created_at = attempt.created_at
    record.updated_at = attempt.updated_at


def _attempt_domain(record: HostedNativeAttemptRecord) -> HostedNativeAttempt:
    return HostedNativeAttempt(
        id=record.id,
        task_id=record.task_id,
        worker_agent_id=record.worker_agent_id,
        leader_agent_id=record.leader_agent_id,
        team_name=record.team_name,
        room_id=record.room_id,
        assignment_attempt_id=record.assignment_attempt_id,
        generation=record.generation,
        execution_id=record.execution_id,
        phase=AttemptPhase(record.phase),
        package_dir=record.package_dir,
        base_sha=record.base_sha,
        budget_until=_aware(record.budget_until),
        notified_at=_aware(record.notified_at),
        created_at=_aware(record.created_at),
        updated_at=_aware(record.updated_at),
        review_dir=record.review_dir,
        review_budget_until=_optional_aware(record.review_budget_until),
        acknowledged_at=_optional_aware(record.acknowledged_at),
        submitted_at=_optional_aware(record.submitted_at),
        submit_status=SubmitStatus(record.submit_status) if record.submit_status else None,
        review_verdict=ReviewVerdict(record.review_verdict) if record.review_verdict else None,
        verification_run_id=record.verification_run_id,
        fenced_at=_optional_aware(record.fenced_at),
        fence_reason=record.fence_reason,
    )


def _event_domain(record: HostedNativeEventRecord) -> HostedNativeEvent:
    return HostedNativeEvent(
        id=record.id,
        attempt_id=record.attempt_id,
        kind=EventKind(record.kind),
        marker=record.marker,
        payload=dict(record.payload or {}),
        observed_at=_aware(record.observed_at),
        applied_at=_optional_aware(record.applied_at),
    )


def _aware(value: datetime) -> datetime:
    """SQLite hands back naive datetimes for ``DateTime(timezone=True)``; every
    value this store writes is UTC, so naive means UTC on the way out."""

    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _optional_aware(value: datetime | None) -> datetime | None:
    return _aware(value) if value is not None else None


class InMemoryHostedNativeAttemptStore(HostedNativeAttemptStore):
    """Dictionary-backed twin of the Postgres store, with the same refusals."""

    def __init__(self) -> None:
        self.attempts: dict[UUID, HostedNativeAttempt] = {}
        self.events: dict[UUID, HostedNativeEvent] = {}
        self._event_keys: dict[tuple[UUID, EventKind, str], UUID] = {}
        self._sequence = 0

    async def add(self, attempt: HostedNativeAttempt) -> None:
        if attempt.id in self.attempts:
            raise HostedNativeConflict(f"attempt {attempt.id} already exists")
        if attempt.is_open and await self.get_open_for_task(attempt.task_id) is not None:
            raise HostedNativeConflict(f"task {attempt.task_id} already has an open attempt")
        self.attempts[attempt.id] = attempt

    async def get(self, attempt_id: UUID) -> HostedNativeAttempt | None:
        return self.attempts.get(attempt_id)

    async def get_open_for_task(self, task_id: UUID) -> HostedNativeAttempt | None:
        for attempt in self.attempts.values():
            if attempt.task_id == task_id and attempt.is_open:
                return attempt
        return None

    async def list_open(self) -> tuple[HostedNativeAttempt, ...]:
        return tuple(
            sorted(
                (attempt for attempt in self.attempts.values() if attempt.is_open),
                key=lambda item: (item.notified_at, str(item.id)),
            )
        )

    async def list_for_task(self, task_id: UUID) -> tuple[HostedNativeAttempt, ...]:
        return tuple(
            sorted(
                (attempt for attempt in self.attempts.values() if attempt.task_id == task_id),
                key=lambda item: (item.generation, item.created_at, str(item.id)),
            )
        )

    async def save(self, attempt: HostedNativeAttempt) -> None:
        if attempt.id not in self.attempts:
            raise HostedNativeConflict(f"attempt {attempt.id} does not exist")
        if attempt.is_open:
            other = await self.get_open_for_task(attempt.task_id)
            if other is not None and other.id != attempt.id:
                raise HostedNativeConflict(
                    f"task {attempt.task_id} already has an open attempt"
                )
        self.attempts[attempt.id] = attempt

    async def record_event(self, event: HostedNativeEvent) -> bool:
        key = (event.attempt_id, event.kind, event.marker)
        if key in self._event_keys or event.id in self.events:
            return False
        self._sequence += 1
        self.events[event.id] = event
        self._event_keys[key] = event.id
        return True

    async def find_event(
        self, attempt_id: UUID, kind: EventKind, marker: str
    ) -> HostedNativeEvent | None:
        event_id = self._event_keys.get((attempt_id, kind, marker))
        return self.events.get(event_id) if event_id is not None else None

    async def mark_applied(self, event_id: UUID, *, applied_at: datetime) -> None:
        event = self.events.get(event_id)
        if event is None:
            return
        self.events[event_id] = HostedNativeEvent(
            id=event.id,
            attempt_id=event.attempt_id,
            kind=event.kind,
            marker=event.marker,
            payload=event.payload,
            observed_at=event.observed_at,
            applied_at=applied_at,
        )

    async def list_events(self, attempt_id: UUID) -> tuple[HostedNativeEvent, ...]:
        ordered = sorted(
            (event for event in self.events.values() if event.attempt_id == attempt_id),
            key=lambda item: (item.observed_at, list(self.events).index(item.id)),
        )
        return tuple(ordered)
