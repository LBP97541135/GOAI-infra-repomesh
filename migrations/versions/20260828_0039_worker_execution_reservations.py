"""Reserve Worker executions before resource preparation.

Revision ID: 20260828_0039
Revises: 20260828_0038
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260828_0039"
down_revision: str | None = "20260828_0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTIVE = "status IN ('preparing', 'running')"


def upgrade() -> None:
    op.create_table(
        "worker_execution_reservations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("worker_agent_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("task_payload", postgresql.JSONB(), nullable=True),
        sa.Column("error_detail", sa.String(2000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        schema="agent_runtime",
    )
    for column in (
        "organization_id",
        "project_id",
        "repository_id",
        "task_id",
        "worker_agent_id",
        "status",
    ):
        op.create_index(
            f"ix_worker_execution_reservations_{column}",
            "worker_execution_reservations",
            [column],
            schema="agent_runtime",
        )
    op.create_index(
        "uq_worker_execution_reservations_active_task",
        "worker_execution_reservations",
        ["task_id"],
        unique=True,
        postgresql_where=sa.text(_ACTIVE),
        schema="agent_runtime",
    )
    op.create_index(
        "uq_worker_execution_reservations_active_worker",
        "worker_execution_reservations",
        ["worker_agent_id"],
        unique=True,
        postgresql_where=sa.text(_ACTIVE),
        schema="agent_runtime",
    )


def downgrade() -> None:
    op.drop_table("worker_execution_reservations", schema="agent_runtime")
