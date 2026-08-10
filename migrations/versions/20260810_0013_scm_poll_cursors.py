"""Persist GitHub polling cursors and retry state.

Revision ID: 20260810_0013
Revises: 20260809_0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260810_0013"
down_revision: str | None = "20260809_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scm_poll_cursors",
        sa.Column("change_set_id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_poll_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.String(2000), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["change_set_id"], ["delivery.change_sets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("change_set_id", "repository_id"),
        schema="delivery",
    )
    op.create_index(
        "ix_scm_poll_cursors_next_poll_at",
        "scm_poll_cursors",
        ["next_poll_at"],
        schema="delivery",
    )


def downgrade() -> None:
    op.drop_table("scm_poll_cursors", schema="delivery")
