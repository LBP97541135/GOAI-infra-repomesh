"""Declare why a task exists instead of inferring it from its title.

The read model identified rework tasks by comparing ``title`` against the
literal CIReworkTaskCreator writes, so rewording a display string would have
silently changed three derived contract fields (attempt number, the
``repairing`` display status and the repair timeline) with nothing going red.

The UPDATE below is the one and only place a task title is allowed to decide
semantics: it is a one-shot, auditable backfill for rows written before the
column existed, not a read-path judgement.

Revision ID: 20260811_0022
Revises: 20260811_0021
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260811_0022"
down_revision: str | None = "20260811_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REWORK_TASK_TITLE = "Repair failed delivery candidate"


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("origin", sa.String(30), nullable=False, server_default="planned"),
        schema="task_orchestration",
    )
    op.execute(
        sa.text(
            "UPDATE task_orchestration.tasks SET origin = 'rework' WHERE title = :title"
        ).bindparams(title=_REWORK_TASK_TITLE)
    )


def downgrade() -> None:
    op.drop_column("tasks", "origin", schema="task_orchestration")
