from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Index, String, Text, UniqueConstraint, and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.modules.collaboration.contracts import (
    CollaborationDeliveryStatus,
    CollaborationMessageKind,
    RoomTimelineCursor,
    RoomTimelineEntryView,
)
from repomesh.modules.collaboration.domain import CollaborationConflict, CollaborationMessage
from repomesh.persistence import Database
from repomesh.persistence.base import Base
from repomesh.persistence.models import AuditEventRecord
from repomesh.shared.events import EventEnvelope


class CollaborationMessageRecord(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_collaboration_messages_idempotency"),
        {"schema": "collaboration"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    repository_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    task_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    sender_agent_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    recipient_agent_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    subject: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    room_id: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), index=True)
    event_id: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    request_fingerprint: Mapped[str] = mapped_column(String(71))


class ProcessedMatrixEventRecord(Base):
    __tablename__ = "processed_matrix_events"
    __table_args__ = {"schema": "collaboration"}

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    sender_agent_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)


class RoomTimelineMessageRecord(Base):
    """What a room said, as the homeserver said it.

    A table of its own rather than a second use of ``processed_matrix_events``:
    that one is a consumption cursor — "this event has been acted on" — and
    this one is a transcript. Sharing a table would make deleting a stale
    cursor entry delete a message, and would make the two consumers'
    idempotency the same fact when they are not: an event can be recorded here
    and deliberately not acted on there (D-7), and both statements are true at
    once.

    The primary key is the Matrix event id, which is what makes replaying a
    sync batch free. ``ix_room_timeline_messages_room_id`` covers the read the
    console actually makes — one room, in ``(occurred_at, event_id)`` order —
    so paging never sorts the table.
    """

    __tablename__ = "room_timeline_messages"
    __table_args__ = (
        Index(
            "ix_room_timeline_messages_room_id",
            "room_id",
            "occurred_at",
            "event_id",
        ),
        {"schema": "collaboration"},
    )

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    room_id: Mapped[str] = mapped_column(Text)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    repository_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    sender_matrix_user_id: Mapped[str] = mapped_column(Text)
    #: Null when no principal owns this Matrix user (D-4): stored unresolved
    #: rather than resolved to a plausible neighbour.
    sender_agent_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    body: Mapped[str] = mapped_column(Text)
    #: Matrix ``origin_server_ts``. The room's own clock, not ours: the console
    #: shows messages in the order the room saw them.
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class InMemoryCollaborationMessageStore:
    def __init__(self) -> None:
        self.messages: dict[object, CollaborationMessage] = {}
        self.idempotency: dict[str, tuple[object, str]] = {}

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> tuple[CollaborationMessage, str] | None:
        binding = self.idempotency.get(idempotency_key)
        if binding is None:
            return None
        message_id, fingerprint = binding
        return self.messages[message_id], fingerprint

    async def add(
        self,
        message: CollaborationMessage,
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> None:
        self.messages[message.id] = message
        self.idempotency[idempotency_key] = (message.id, request_fingerprint)

    async def update(self, message: CollaborationMessage) -> None:
        self.messages[message.id] = message

    async def last_assignment_at(self, project_id: UUID) -> dict[UUID, datetime]:
        latest: dict[UUID, datetime] = {}
        for message in self.messages.values():
            if message.project_id != project_id or message.task_id is None:
                continue
            if message.kind is not CollaborationMessageKind.TASK_ASSIGNMENT:
                continue
            current = latest.get(message.task_id)
            if current is None or message.created_at > current:
                latest[message.task_id] = message.created_at
        return latest

    async def list_failed(
        self, limit: int = 100
    ) -> tuple[tuple[CollaborationMessage, str], ...]:
        failed = []
        for key, (message_id, _) in self.idempotency.items():
            message = self.messages[message_id]
            if message.status is CollaborationDeliveryStatus.FAILED:
                failed.append((message, key))
        return tuple(failed[:limit])


class InMemoryProcessedMatrixEventStore:
    def __init__(self) -> None:
        self.events: dict[str, tuple[UUID, UUID, UUID]] = {}

    async def contains(self, event_id: str) -> bool:
        return event_id in self.events

    async def add(
        self, event_id: str, *, project_id: UUID, task_id: UUID, sender_agent_id: UUID
    ) -> None:
        self.events.setdefault(event_id, (project_id, task_id, sender_agent_id))


def _timeline_order(entry: RoomTimelineEntryView) -> tuple[datetime, str]:
    """The one sort key. Ties on the room's clock break on the event id.

    Written once and used by both adapters so "stable order" cannot mean two
    different things depending on where the rows came from.
    """

    return (entry.occurred_at, entry.event_id)


class InMemoryRoomTimelineStore:
    def __init__(self) -> None:
        self.entries: dict[str, RoomTimelineEntryView] = {}

    async def get(self, event_id: str) -> RoomTimelineEntryView | None:
        return self.entries.get(event_id)

    async def add(self, entry: RoomTimelineEntryView) -> RoomTimelineEntryView:
        return self.entries.setdefault(entry.event_id, entry)

    async def list_room(
        self,
        room_id: str,
        *,
        after: RoomTimelineCursor | None = None,
        limit: int = 100,
    ) -> tuple[RoomTimelineEntryView, ...]:
        rows = sorted(
            (entry for entry in self.entries.values() if entry.room_id == room_id),
            key=_timeline_order,
        )
        if after is not None:
            cursor = (after.occurred_at, after.event_id)
            rows = [entry for entry in rows if _timeline_order(entry) > cursor]
        return tuple(rows[:limit])


class InMemoryCollaborationAuditLedger:
    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    async def record(self, event: EventEnvelope) -> None:
        self.events.append(event)


class PostgresCollaborationMessageStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> tuple[CollaborationMessage, str] | None:
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(CollaborationMessageRecord).where(
                    CollaborationMessageRecord.idempotency_key == idempotency_key
                )
            )
        if record is None:
            return None
        return self._to_domain(record), record.request_fingerprint

    async def add(
        self,
        message: CollaborationMessage,
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> None:
        try:
            async with self._database.transaction() as session:
                session.add(
                    CollaborationMessageRecord(
                        **self._values(message),
                        idempotency_key=idempotency_key,
                        request_fingerprint=request_fingerprint,
                    )
                )
        except IntegrityError as error:
            raise CollaborationConflict("collaboration message already exists") from error

    async def list_by_project(self, project_id: UUID) -> tuple[CollaborationMessage, ...]:
        async with self._database.transaction() as session:
            records = (
                await session.scalars(
                    select(CollaborationMessageRecord)
                    .where(CollaborationMessageRecord.project_id == project_id)
                    .order_by(CollaborationMessageRecord.created_at)
                )
            ).all()
        return tuple(self._to_domain(record) for record in records)

    async def last_assignment_at(self, project_id: UUID) -> dict[UUID, datetime]:
        """When each task of this project was last dispatched (contract v0.4 §8.7.4).

        Deliberately an aggregate rather than a filter over ``list_by_project``.
        That method loads every message of the project including its full
        ``body``, and the console asks this on every read of a round's tasks;
        ``max(created_at) group by task_id`` returns one small row per task and
        rides the ``project_id`` and ``kind`` indexes the table already has.

        ``created_at`` is set when the message is written, so this is when the
        dispatch was *sent*, not when the Worker read it — which is the honest
        reading and the only one available: the row has no ``delivered_at``.
        The console labels it accordingly.
        """

        async with self._database.transaction() as session:
            rows = (
                await session.execute(
                    select(
                        CollaborationMessageRecord.task_id,
                        func.max(CollaborationMessageRecord.created_at),
                    )
                    .where(
                        CollaborationMessageRecord.project_id == project_id,
                        CollaborationMessageRecord.kind
                        == CollaborationMessageKind.TASK_ASSIGNMENT.value,
                        CollaborationMessageRecord.task_id.is_not(None),
                    )
                    .group_by(CollaborationMessageRecord.task_id)
                )
            ).all()
        return {task_id: at for task_id, at in rows if task_id is not None and at is not None}

    async def list_by_room(self, room_id: str) -> tuple[CollaborationMessage, ...]:
        """Every message delivered to one Matrix room, oldest first."""

        async with self._database.transaction() as session:
            records = (
                await session.scalars(
                    select(CollaborationMessageRecord)
                    .where(CollaborationMessageRecord.room_id == room_id)
                    .order_by(CollaborationMessageRecord.created_at)
                )
            ).all()
        return tuple(self._to_domain(record) for record in records)

    async def update(self, message: CollaborationMessage) -> None:
        async with self._database.transaction() as session:
            record = await session.get(CollaborationMessageRecord, message.id)
            if record is None:
                raise CollaborationConflict("collaboration message does not exist")
            record.status = message.status.value
            record.event_id = message.event_id

    async def list_failed(
        self, limit: int = 100
    ) -> tuple[tuple[CollaborationMessage, str], ...]:
        async with self._database.transaction() as session:
            records = (
                await session.scalars(
                    select(CollaborationMessageRecord)
                    .where(
                        CollaborationMessageRecord.status
                        == CollaborationDeliveryStatus.FAILED.value
                    )
                    .limit(limit)
                )
            ).all()
        return tuple((self._to_domain(record), record.idempotency_key) for record in records)

    @staticmethod
    def _values(message: CollaborationMessage) -> dict[str, object]:
        return {
            "id": message.id,
            "organization_id": message.organization_id,
            "project_id": message.project_id,
            "repository_id": message.repository_id,
            "task_id": message.task_id,
            "sender_agent_id": message.sender_agent_id,
            "recipient_agent_id": message.recipient_agent_id,
            "kind": message.kind.value,
            "subject": message.subject,
            "body": message.body,
            "room_id": message.room_id,
            "status": message.status.value,
            "event_id": message.event_id,
            "correlation_id": message.correlation_id,
            "created_at": message.created_at,
        }

    @staticmethod
    def _to_domain(record: CollaborationMessageRecord) -> CollaborationMessage:
        return CollaborationMessage(
            id=record.id,
            organization_id=record.organization_id,
            project_id=record.project_id,
            repository_id=record.repository_id,
            task_id=record.task_id,
            sender_agent_id=record.sender_agent_id,
            recipient_agent_id=record.recipient_agent_id,
            kind=CollaborationMessageKind(record.kind),
            subject=record.subject,
            body=record.body,
            room_id=record.room_id,
            status=CollaborationDeliveryStatus(record.status),
            event_id=record.event_id,
            correlation_id=record.correlation_id,
            created_at=record.created_at,
        )


class PostgresRoomTimelineStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(self, event_id: str) -> RoomTimelineEntryView | None:
        async with self._database.transaction() as session:
            record = await session.get(RoomTimelineMessageRecord, event_id)
            return self._to_view(record) if record is not None else None

    async def add(self, entry: RoomTimelineEntryView) -> RoomTimelineEntryView:
        """Insert, and on a duplicate event id return what is already there.

        The conflict is not an error worth propagating: an event id arriving
        twice is a sync batch being replayed, which the poller does by design
        after any failure in the batch. Losing the race means somebody else
        recorded the identical event, so the caller is told about that row.
        """

        try:
            async with self._database.transaction() as session:
                session.add(
                    RoomTimelineMessageRecord(
                        event_id=entry.event_id,
                        room_id=entry.room_id,
                        project_id=entry.project_id,
                        repository_id=entry.repository_id,
                        sender_matrix_user_id=entry.sender_matrix_user_id,
                        sender_agent_id=entry.sender_agent_id,
                        body=entry.body,
                        occurred_at=entry.occurred_at,
                    )
                )
        except IntegrityError:
            stored = await self.get(entry.event_id)
            if stored is None:  # pragma: no cover - only a non-key conflict
                raise
            return stored
        return entry

    async def list_room(
        self,
        room_id: str,
        *,
        after: RoomTimelineCursor | None = None,
        limit: int = 100,
    ) -> tuple[RoomTimelineEntryView, ...]:
        statement = select(RoomTimelineMessageRecord).where(
            RoomTimelineMessageRecord.room_id == room_id
        )
        if after is not None:
            # Spelled out rather than as a row-value comparison: the same
            # predicate has to run on SQLite, where tuple comparison support
            # is version-dependent.
            statement = statement.where(
                or_(
                    RoomTimelineMessageRecord.occurred_at > after.occurred_at,
                    and_(
                        RoomTimelineMessageRecord.occurred_at == after.occurred_at,
                        RoomTimelineMessageRecord.event_id > after.event_id,
                    ),
                )
            )
        statement = statement.order_by(
            RoomTimelineMessageRecord.occurred_at,
            RoomTimelineMessageRecord.event_id,
        ).limit(limit)
        async with self._database.transaction() as session:
            records = (await session.scalars(statement)).all()
        return tuple(self._to_view(record) for record in records)

    @staticmethod
    def _to_view(record: RoomTimelineMessageRecord) -> RoomTimelineEntryView:
        return RoomTimelineEntryView(
            event_id=record.event_id,
            room_id=record.room_id,
            project_id=record.project_id,
            repository_id=record.repository_id,
            sender_matrix_user_id=record.sender_matrix_user_id,
            sender_agent_id=record.sender_agent_id,
            body=record.body,
            occurred_at=record.occurred_at,
        )


class PostgresCollaborationAuditLedger:
    """Append one envelope to ``platform.audit_events``.

    The platform ledger rather than a table of this module's own: the row is
    an audit fact about a refusal, which is what that table is for, and adding
    a private one would put half the audit trail somewhere no operator looks.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    async def record(self, event: EventEnvelope) -> None:
        async with self._database.transaction() as session:
            session.add(AuditEventRecord.from_envelope(event))


class PostgresProcessedMatrixEventStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def contains(self, event_id: str) -> bool:
        async with self._database.transaction() as session:
            return await session.get(ProcessedMatrixEventRecord, event_id) is not None

    async def add(
        self, event_id: str, *, project_id: UUID, task_id: UUID, sender_agent_id: UUID
    ) -> None:
        try:
            async with self._database.transaction() as session:
                session.add(
                    ProcessedMatrixEventRecord(
                        event_id=event_id,
                        project_id=project_id,
                        task_id=task_id,
                        sender_agent_id=sender_agent_id,
                    )
                )
        except IntegrityError:
            return
