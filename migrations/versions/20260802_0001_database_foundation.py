"""Create module schemas and database foundation.

Revision ID: 20260802_0001
Revises:
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from repomesh.persistence.base import ALL_SCHEMAS

revision: str = "20260802_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def event_columns() -> list[sa.Column]:
    return [
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        sa.Column("event_type", sa.String(200), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor_type", sa.String(30), nullable=False),
        sa.Column("actor_id", sa.String(200), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("workstream_id", sa.Uuid(), nullable=True),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("causation_id", sa.Uuid(), nullable=True),
        sa.Column("aggregate_type", sa.String(100), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    ]


def create_event_indexes(table: str) -> None:
    for column in (
        "event_type",
        "occurred_at",
        "organization_id",
        "project_id",
        "task_id",
        "run_id",
        "correlation_id",
        "aggregate_id",
    ):
        op.create_index(f"ix_{table}_{column}", table, [column], schema="platform")


def upgrade() -> None:
    for schema in ALL_SCHEMAS:
        op.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))

    op.create_table(
        "repositories",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("topics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("languages", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("profiled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.UniqueConstraint("url", name="uq_repositories_url"),
        schema="repository_intelligence",
    )
    op.create_index(
        "ix_repositories_name", "repositories", ["name"], schema="repository_intelligence"
    )

    for table in ("state_events", "audit_events"):
        op.create_table(table, *event_columns(), schema="platform")
        create_event_indexes(table)

    op.create_table(
        "outbox_events",
        *event_columns(),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        schema="platform",
    )
    create_event_indexes("outbox_events")
    op.create_index(
        "ix_outbox_events_pending",
        "outbox_events",
        ["published_at", "recorded_at"],
        schema="platform",
    )

    op.create_table(
        "trace_links",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("trace_id", sa.String(64), nullable=False),
        sa.Column("span_id", sa.String(32), nullable=True),
        sa.Column("entity_type", sa.String(100), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema="platform",
    )
    op.create_index("ix_trace_links_trace_id", "trace_links", ["trace_id"], schema="platform")
    op.create_index("ix_trace_links_entity_id", "trace_links", ["entity_id"], schema="platform")

    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("scope", sa.String(200), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("scope", "idempotency_key", name="uq_idempotency_scope_key"),
        schema="platform",
    )
    op.create_index(
        "ix_idempotency_records_expires_at",
        "idempotency_records",
        ["expires_at"],
        schema="platform",
    )


def downgrade() -> None:
    op.drop_table("idempotency_records", schema="platform")
    op.drop_table("trace_links", schema="platform")
    op.drop_table("outbox_events", schema="platform")
    op.drop_table("audit_events", schema="platform")
    op.drop_table("state_events", schema="platform")
    op.drop_table("repositories", schema="repository_intelligence")
    for schema in reversed(ALL_SCHEMAS):
        op.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}"'))
