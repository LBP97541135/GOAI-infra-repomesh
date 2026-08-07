"""Create batched execution plans for repository task delivery.

Revision ID: 20260807_0008
Revises: 20260806_0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260807_0008"
down_revision: str | None = "20260806_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "execution_plans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_agent_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("current_batch_index", sa.Integer(), nullable=False),
        sa.Column("batches", postgresql.JSONB(), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_execution_plans_idempotency_key"),
        schema="task_orchestration",
    )
    for column in ("organization_id", "project_id", "created_by_agent_id", "status"):
        op.create_index(
            f"ix_execution_plans_{column}", "execution_plans", [column],
            schema="task_orchestration",
        )
    op.create_table(
        "execution_plan_tasks",
        sa.Column("leader_task_id", sa.Uuid(), primary_key=True),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("batch_index", sa.Integer(), nullable=False),
        schema="task_orchestration",
    )
    op.create_index(
        "ix_execution_plan_tasks_plan_id",
        "execution_plan_tasks",
        ["plan_id"],
        schema="task_orchestration",
    )


def downgrade() -> None:
    op.drop_table("execution_plan_tasks", schema="task_orchestration")
    op.drop_table("execution_plans", schema="task_orchestration")
