"""Create plan_snapshots table for DAG observability.

Revision ID: 20260808_0009
Revises: 20260807_0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260808_0009"
down_revision: str | None = "20260807_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "plan_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("created_by_agent_id", sa.Uuid(), nullable=True),
        sa.Column("engineering_spec", sa.Text(), nullable=False),
        sa.Column("contracts", postgresql.JSONB(), nullable=False),
        sa.Column("task_dag", postgresql.JSONB(), nullable=False),
        sa.Column("execution_batches", postgresql.JSONB(), nullable=False),
        sa.Column(
            "graph_edges", postgresql.JSONB(), nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("execution_plan_id", sa.Uuid(), nullable=True),
        sa.Column("requirement_text", sa.Text(), nullable=True),
        sa.Column("integration_method", sa.String(20), nullable=True),
        sa.UniqueConstraint(
            "project_id", "plan_version",
            name="uq_plan_snapshots_project_version",
        ),
        schema="repository_intelligence",
    )
    op.create_index(
        "ix_plan_snapshots_project_id",
        "plan_snapshots",
        ["project_id"],
        schema="repository_intelligence",
    )
    op.create_index(
        "ix_plan_snapshots_project_version",
        "plan_snapshots",
        ["project_id", sa.text("plan_version DESC")],
        schema="repository_intelligence",
    )


def downgrade() -> None:
    op.drop_table("plan_snapshots", schema="repository_intelligence")
