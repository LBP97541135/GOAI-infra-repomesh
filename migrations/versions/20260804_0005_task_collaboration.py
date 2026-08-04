"""Create task orchestration and collaboration storage.

Revision ID: 20260804_0005
Revises: 20260803_0004
Create Date: 2026-08-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260804_0005"
down_revision: str | None = "20260803_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("parent_task_id", sa.Uuid(), nullable=True),
        sa.Column("assigned_by_agent_id", sa.Uuid(), nullable=False),
        sa.Column("assignee_agent_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column(
            "acceptance", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_fingerprint", sa.String(71), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_tasks_idempotency_key"),
        schema="task_orchestration",
    )
    for column in (
        "organization_id",
        "project_id",
        "repository_id",
        "parent_task_id",
        "assigned_by_agent_id",
        "assignee_agent_id",
        "status",
    ):
        op.create_index(
            f"ix_tasks_{column}",
            "tasks",
            [column],
            schema="task_orchestration",
        )

    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=True),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("sender_agent_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_agent_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("room_id", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("event_id", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_fingerprint", sa.String(71), nullable=False),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_collaboration_messages_idempotency"
        ),
        schema="collaboration",
    )
    for column in (
        "organization_id",
        "project_id",
        "repository_id",
        "task_id",
        "sender_agent_id",
        "recipient_agent_id",
        "kind",
        "status",
        "correlation_id",
    ):
        op.create_index(
            f"ix_collaboration_messages_{column}",
            "messages",
            [column],
            schema="collaboration",
        )
    op.create_table(
        "processed_matrix_events",
        sa.Column("event_id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("sender_agent_id", sa.Uuid(), nullable=False),
        schema="collaboration",
    )
    for column in ("project_id", "task_id", "sender_agent_id"):
        op.create_index(
            f"ix_collaboration_processed_matrix_events_{column}",
            "processed_matrix_events",
            [column],
            schema="collaboration",
        )


def downgrade() -> None:
    op.drop_table("processed_matrix_events", schema="collaboration")
    op.drop_table("messages", schema="collaboration")
    op.drop_table("tasks", schema="task_orchestration")
