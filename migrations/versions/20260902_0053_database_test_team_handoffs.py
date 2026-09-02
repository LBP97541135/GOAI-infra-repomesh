"""Persist database test-team handoffs.

Revision ID: 20260902_0053
Revises: 20260902_0052, 20260831_0052
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260902_0053"
down_revision: tuple[str, str] = ("20260902_0052", "20260831_0052")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    op.create_table(
        "database_test_team_handoffs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("branch_validation_key", sa.String(200), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_database_test_team_handoffs"),
        sa.UniqueConstraint("branch_validation_key", name="uq_database_test_team_handoffs_key"),
        schema="task_orchestration",
    )

def downgrade() -> None:
    op.drop_table("database_test_team_handoffs", schema="task_orchestration")
