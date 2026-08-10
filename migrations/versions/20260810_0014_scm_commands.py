"""Add the durable SCM command journal.

Revision ID: 20260810_0014
Revises: 20260810_0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260810_0014"
down_revision: str | None = "20260810_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scm_commands",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("change_set_id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(2000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["change_set_id"], ["delivery.change_sets.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("idempotency_key", name="uq_scm_commands_idempotency"),
        schema="delivery",
    )
    for column in ("change_set_id", "repository_id", "kind", "status"):
        op.create_index(
            f"ix_scm_commands_{column}",
            "scm_commands",
            [column],
            schema="delivery",
        )


def downgrade() -> None:
    op.drop_table("scm_commands", schema="delivery")
