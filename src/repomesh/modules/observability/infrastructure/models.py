"""SQLAlchemy models for the observability schema.

Append-only LLM usage rows (planning-phase inference, and anything else that
runs through the DeepSeek adapter). This is the durable projection of what the
OTel GenAI spans already carry ephemerally: rows survive collector downtime and
are what the aggregation endpoints read.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.persistence.base import Base


class LLMUsageRecord(Base):
    __tablename__ = "llm_usage"
    __table_args__ = (
        Index("ix_llm_usage_created_at", "created_at"),
        Index("ix_llm_usage_issue_id", "issue_id"),
        Index("ix_llm_usage_model", "model"),
        {"schema": "observability"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Ambient attribution; null when the call had no discovery context.
    issue_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    discovery_step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    finish_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: "ok" | "error" — a failed call is a call, and hiding it would make
    #: reliability problems invisible to the observability page.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")


class AlertRuleRecord(Base):
    """A threshold rule evaluated over a trailing llm_usage window."""

    __tablename__ = "alert_rules"
    __table_args__ = {"schema": "observability"}

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    #: success_rate | error_count | latency_p95_ms | estimated_cost_usd | calls
    metric: Mapped[str] = mapped_column(String(32), nullable=False)
    #: "lt" | "gt"
    operator: Mapped[str] = mapped_column(String(4), nullable=False)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    window_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=1440)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AlertEventRecord(Base):
    """One firing/resolved transition of an alert rule."""

    __tablename__ = "alert_events"
    __table_args__ = (
        Index("ix_alert_events_status", "status"),
        {"schema": "observability"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    rule_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("observability.alert_rules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: "firing" | "resolved"
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(String(512), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    window_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class TraceSessionRecord(Base):
    """One CoPaw session object discovered in shared storage.

    ``source_key`` is the storage object key (``agents/{name}/.copaw/...``),
    the deduplication identity for the ingester: an object with unchanged
    mtime/size is skipped; a changed object is re-parsed and re-projected
    without duplicating rows (events are unique per ``(session_id, seq)``).
    """

    __tablename__ = "trace_sessions"
    __table_args__ = (
        Index("ix_trace_sessions_agent_first_seen", "agent_name", "first_seen_at"),
        UniqueConstraint("source_key", name="uq_trace_sessions_source_key"),
        {"schema": "observability"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    agent_name: Mapped[str] = mapped_column(Text, nullable=False)
    #: "copaw" — the runtime that produced the session.
    runtime: Mapped[str] = mapped_column(Text, nullable=False, default="copaw")
    source_key: Mapped[str] = mapped_column(Text, nullable=False)
    object_mtime: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    object_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    parsed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Set when the whole object failed to parse; kept so the poller does not
    #: hot-loop over a permanently broken object.
    parsing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class TraceEventRecord(Base):
    """One normalized event from a CoPaw session.

    Types: ``chat`` (LLM text message), ``tool``/``skill``/``mcp``/``rag``
    (tool calls, classified by tool name), ``task`` (RepoMesh task assignment
    embedded in a user message). A tool event merges its ``tool_use`` block
    with the matching ``tool_result`` block when both are present.
    """

    __tablename__ = "trace_events"
    __table_args__ = (
        Index("ix_trace_events_session_id", "session_id"),
        UniqueConstraint(
            "session_id", "seq", name="uq_trace_events_session_seq"
        ),
        {"schema": "observability"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("observability.trace_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: 1-based position in the message file; idempotency key with session_id.
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    #: "user" | "assistant" | "tool" (for tool/skill/mcp/rag/task events).
    role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: "ok" | "error" | "skipped" (tool result missing/empty).
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class LogEntryRecord(Base):
    """One structured log line captured from the RepoMesh planning process.

    The ``log_recorder`` pipeline attaches a standard-library ``logging.Handler``
    to the root logger, normalises each ``LogRecord`` on the emitting thread and
    drains the queue into this table on the event loop. ``source`` is the
    logger name (``repomesh.modules.…``), ``issue_id`` arrives via
    ``extra={"issue_id": …}`` and stays null for ambient logs.
    """

    __tablename__ = "log_entries"
    __table_args__ = (
        Index("ix_log_entries_ts", "ts"),
        Index("ix_log_entries_level", "level"),
        Index("ix_log_entries_source", "source"),
        Index("ix_log_entries_issue_id", "issue_id"),
        {"schema": "observability"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: DEBUG | INFO | WARNING | ERROR | CRITICAL (``LogRecord.levelname``).
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Logger name (``record.name``), e.g. ``repomesh.modules.…``.
    source: Mapped[str] = mapped_column(Text, nullable=False)
    issue_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    #: ``traceback.format_exception`` text when the record carried exc_info.
    exc_info: Mapped[str | None] = mapped_column(Text, nullable=True)
