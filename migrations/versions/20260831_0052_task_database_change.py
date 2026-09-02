"""Persist Repository Manager database requirements on Tasks.

Revision ID: 20260831_0052
Revises: 20260902_0052
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260831_0052"
down_revision: str | None = "20260902_0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("database_change", postgresql.JSONB(), nullable=True),
        schema="task_orchestration",
    )


def downgrade() -> None:
    op.drop_column("tasks", "database_change", schema="task_orchestration")
