"""Persist idempotent SCM webhook deliveries.

Revision ID: 20260807_0010
Revises: 20260807_0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_0010"
down_revision: str | None = "20260807_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scm_webhook_events",
        sa.Column("delivery_id", sa.String(100), primary_key=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        schema="delivery",
    )
    op.create_index(
        "ix_scm_webhook_events_status",
        "scm_webhook_events",
        ["status"],
        schema="delivery",
    )


def downgrade() -> None:
    op.drop_table("scm_webhook_events", schema="delivery")
