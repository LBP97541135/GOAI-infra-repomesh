"""Journal append-only dynamic execution plan revisions.

Revision ID: 20260828_0041
Revises: 20260828_0040
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260828_0041"
down_revision: str | None = "20260828_0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "execution_plan_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("base_plan_version", sa.Integer(), nullable=False),
        sa.Column("result_plan_version", sa.Integer(), nullable=True),
        sa.Column("actor_agent_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("appended_items", postgresql.JSONB(), nullable=False),
        sa.Column("previous_batches", postgresql.JSONB(), nullable=False),
        sa.Column("new_batches", postgresql.JSONB(), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["plan_id"], ["task_orchestration.execution_plans.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("plan_id", "revision", name="uq_execution_plan_revisions_number"),
        sa.UniqueConstraint("idempotency_key", name="uq_execution_plan_revisions_idempotency"),
        schema="task_orchestration",
    )
    op.create_index(
        "ix_execution_plan_revisions_plan_id", "execution_plan_revisions", ["plan_id"],
        schema="task_orchestration",
    )
    op.create_index(
        "ix_execution_plan_revisions_actor_agent_id", "execution_plan_revisions",
        ["actor_agent_id"], schema="task_orchestration",
    )
    op.create_index(
        "ix_execution_plan_revisions_status", "execution_plan_revisions", ["status"],
        schema="task_orchestration",
    )


def downgrade() -> None:
    op.drop_table("execution_plan_revisions", schema="task_orchestration")
