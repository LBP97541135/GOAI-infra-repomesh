from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.persistence.base import PLATFORM_SCHEMA, Base
from repomesh.shared.events import EventEnvelope

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


class EventRecordMixin:
    event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(200), index=True)
    schema_version: Mapped[int] = mapped_column(Integer)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    actor_type: Mapped[str] = mapped_column(String(30))
    actor_id: Mapped[str] = mapped_column(String(200))
    organization_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    project_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    workstream_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    task_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    run_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    correlation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    causation_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    aggregate_type: Mapped[str] = mapped_column(String(100))
    aggregate_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    aggregate_version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)

    @classmethod
    def envelope_values(cls, event: EventEnvelope) -> dict[str, Any]:
        return {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "schema_version": event.schema_version,
            "occurred_at": event.occurred_at,
            "actor_type": event.actor_type,
            "actor_id": event.actor_id,
            "organization_id": event.organization_id,
            "project_id": event.project_id,
            "workstream_id": event.workstream_id,
            "task_id": event.task_id,
            "run_id": event.run_id,
            "correlation_id": event.correlation_id,
            "causation_id": event.causation_id,
            "aggregate_type": event.aggregate_type,
            "aggregate_id": event.aggregate_id,
            "aggregate_version": event.aggregate_version,
            "payload": event.payload,
        }


class StateEventRecord(EventRecordMixin, Base):
    __tablename__ = "state_events"
    __table_args__ = {"schema": PLATFORM_SCHEMA}

    @classmethod
    def from_envelope(cls, event: EventEnvelope) -> "StateEventRecord":
        return cls(**cls.envelope_values(event))


class AuditEventRecord(EventRecordMixin, Base):
    __tablename__ = "audit_events"
    __table_args__ = {"schema": PLATFORM_SCHEMA}

    @classmethod
    def from_envelope(cls, event: EventEnvelope) -> "AuditEventRecord":
        return cls(**cls.envelope_values(event))


class OutboxEventRecord(EventRecordMixin, Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        Index("ix_outbox_events_pending", "published_at", "recorded_at"),
        {"schema": PLATFORM_SCHEMA},
    )

    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)

    @classmethod
    def from_envelope(cls, event: EventEnvelope) -> "OutboxEventRecord":
        return cls(**cls.envelope_values(event))


class TraceLinkRecord(Base):
    __tablename__ = "trace_links"
    __table_args__ = {"schema": PLATFORM_SCHEMA}

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    span_id: Mapped[str | None] = mapped_column(String(32))
    entity_type: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("scope", "idempotency_key", name="uq_idempotency_scope_key"),
        {"schema": PLATFORM_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    scope: Mapped[str] = mapped_column(String(200))
    idempotency_key: Mapped[str] = mapped_column(String(500))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
