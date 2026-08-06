"""Create durable Runner task and event gateway.

Revision ID: 20260806_0007
Revises: 20260804_0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260806_0007"
down_revision: str | None = "20260804_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runner_dispatches",
        sa.Column("run_id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("worker_agent_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("task_payload", postgresql.JSONB(), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_runner_dispatches_idempotency"),
        schema="agent_runtime",
    )
    for column in (
        "organization_id", "project_id", "repository_id", "task_id",
        "worker_agent_id", "status",
    ):
        op.create_index(
            f"ix_runner_dispatches_{column}", "runner_dispatches", [column],
            schema="agent_runtime",
        )
    op.create_table(
        "runner_events",
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "sequence", name="uq_runner_events_sequence"),
        schema="agent_runtime",
    )
    op.create_index("ix_runner_events_run_id", "runner_events", ["run_id"], schema="agent_runtime")
    op.create_index(
        "ix_runner_events_event_type",
        "runner_events",
        ["event_type"],
        schema="agent_runtime",
    )


def downgrade() -> None:
    op.drop_table("runner_events", schema="agent_runtime")
    op.drop_table("runner_dispatches", schema="agent_runtime")
