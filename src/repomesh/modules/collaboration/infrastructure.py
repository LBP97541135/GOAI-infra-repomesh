from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, String, Text, UniqueConstraint, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.modules.collaboration.contracts import (
    CollaborationDeliveryStatus,
    CollaborationMessageKind,
)
from repomesh.modules.collaboration.domain import CollaborationConflict, CollaborationMessage
from repomesh.persistence import Database
from repomesh.persistence.base import Base


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
