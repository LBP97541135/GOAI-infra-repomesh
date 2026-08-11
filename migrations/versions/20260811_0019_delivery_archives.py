"""Archive inactive deliveries without deleting their data.

Revision ID: 20260811_0019
Revises: 20260811_0018
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260811_0019"
down_revision: str | None = "20260811_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS delivery")
    op.create_table(
        "delivery_archives",
        sa.Column("delivery_id", sa.Uuid(), primary_key=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=False),
        schema="delivery",
    )


def downgrade() -> None:
    op.drop_table("delivery_archives", schema="delivery")
