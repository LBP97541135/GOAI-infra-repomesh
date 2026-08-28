"""Create observability.trace_sessions and observability.trace_events.

The trace projection stores CoPaw agent sessions (pushed to shared storage by
FileSync.push_loop) as a queryable, append-only event stream: one row per
session in ``trace_sessions`` and one row per normalized event in
``trace_events``. ``(session_id, seq)`` is unique so re-polling an object —
unchanged or changed — never duplicates rows; the ingester relies on
``INSERT ... ON CONFLICT DO NOTHING`` for idempotency.

Revision ID: 20260815_0033
Revises: 20260815_0032
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_0033"
down_revision: str | None = "20260815_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trace_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("agent_name", sa.Text(), nullable=False),
        sa.Column("runtime", sa.Text(), nullable=False, server_default="copaw"),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("object_mtime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("object_size", sa.BigInteger(), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("parsing_error", sa.Text(), nullable=True),
        sa.Column("event_count", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("source_key", name="uq_trace_sessions_source_key"),
        schema="observability",
    )
    op.create_index(
        "ix_trace_sessions_agent_first_seen",
        "trace_sessions",
        ["agent_name", "first_seen_at"],
        schema="observability",
    )
    op.create_table(
        "trace_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Uuid(),
            sa.ForeignKey(
                "observability.trace_sessions.id", ondelete="CASCADE"
            ),
            nullable=False,
            index=True,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="ok"),
        sa.Column(
            "payload",
            sa.JSON().with_variant(sa.dialects.postgresql.JSONB, "postgresql"),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "session_id", "seq", name="uq_trace_events_session_seq"
        ),
        schema="observability",
    )


def downgrade() -> None:
    op.drop_table("trace_events", schema="observability")
    op.drop_index(
        "ix_trace_sessions_agent_first_seen",
        table_name="trace_sessions",
        schema="observability",
    )
    op.drop_table("trace_sessions", schema="observability")
