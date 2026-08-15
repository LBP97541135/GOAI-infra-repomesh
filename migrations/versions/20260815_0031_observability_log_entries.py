"""Create observability.log_entries.

Revision ID: 20260815_0031
Revises: 20260815_0030
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_0031"
down_revision: str | None = "20260815_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "log_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("issue_id", sa.Uuid(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("exc_info", sa.Text(), nullable=True),
        schema="observability",
    )
    op.create_index("ix_log_entries_ts", "log_entries", ["ts"], schema="observability")
    op.create_index("ix_log_entries_level", "log_entries", ["level"], schema="observability")
    op.create_index("ix_log_entries_source", "log_entries", ["source"], schema="observability")
    op.create_index("ix_log_entries_issue_id", "log_entries", ["issue_id"], schema="observability")


def downgrade() -> None:
    op.drop_index("ix_log_entries_issue_id", table_name="log_entries", schema="observability")
    op.drop_index("ix_log_entries_source", table_name="log_entries", schema="observability")
    op.drop_index("ix_log_entries_level", table_name="log_entries", schema="observability")
    op.drop_index("ix_log_entries_ts", table_name="log_entries", schema="observability")
    op.drop_table("log_entries", schema="observability")
