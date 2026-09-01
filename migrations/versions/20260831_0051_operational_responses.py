"""Create idempotent operational alert responses.

Revision ID: 20260831_0051
Revises: 20260831_0050
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_0051"
down_revision: str | None = "20260831_0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operational_responses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("alert_event_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("notification_status", sa.String(24), nullable=False),
        sa.Column("action_status", sa.String(24), nullable=False),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["alert_event_id"],
            ["observability.alert_events.id"],
            name="fk_operational_responses_alert_event_id_alert_events",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_operational_responses"),
        sa.UniqueConstraint(
            "alert_event_id", name="uq_operational_responses_alert_event"
        ),
        schema="observability",
    )
    op.create_index(
        "ix_operational_responses_alert_event_id",
        "operational_responses",
        ["alert_event_id"],
        schema="observability",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_operational_responses_alert_event_id",
        table_name="operational_responses",
        schema="observability",
    )
    op.drop_table("operational_responses", schema="observability")
