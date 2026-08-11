"""Persist when a collaboration message was created.

The delivery read-model events timeline (contract v0.1 §4.1) dates its
matrix entries with this column; existing rows are backfilled with now().

Revision ID: 20260811_0017
Revises: 20260811_0016
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260811_0017"
down_revision: str | None = "20260811_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="collaboration",
    )
    op.alter_column(
        "messages", "created_at", server_default=None, schema="collaboration"
    )


def downgrade() -> None:
    op.drop_column("messages", "created_at", schema="collaboration")
