"""Create observability.alert_rules and observability.alert_events.

Revision ID: 20260814_0029
Revises: 20260814_0028
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260814_0029"
down_revision: str | None = "20260814_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alert_rules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column(
            "metric",
            sa.String(32),
            nullable=False,
            comment=(
                "success_rate | error_count | latency_p95_ms | "
                "estimated_cost_usd | calls"
            ),
        ),
        sa.Column("operator", sa.String(4), nullable=False, comment="lt | gt"),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("window_minutes", sa.Integer(), nullable=False, server_default="1440"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema="observability",
    )
    op.create_table(
        "alert_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "rule_id",
            sa.Uuid(),
            sa.ForeignKey(
                "observability.alert_rules.id", ondelete="CASCADE"
            ),
            nullable=False,
            index=True,
        ),
        sa.Column("status", sa.String(16), nullable=False, comment="firing | resolved"),
        sa.Column("message", sa.String(512), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("window_minutes", sa.Integer(), nullable=False),
        sa.Column(
            "triggered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        schema="observability",
    )
    op.create_index(
        "ix_alert_events_status", "alert_events", ["status"], schema="observability"
    )


def downgrade() -> None:
    op.drop_index(
        "ix_alert_events_status", table_name="alert_events", schema="observability"
    )
    op.drop_table("alert_events", schema="observability")
    op.drop_table("alert_rules", schema="observability")
