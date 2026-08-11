"""Add project operational lifecycle status.

Revision ID: 20260811_0017
Revises: 20260811_0016
Create Date: 2026-08-11
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
        "agent_topologies",
        sa.Column(
            "operational_status",
            sa.String(30),
            nullable=False,
            server_default="active",
        ),
        schema="project",
    )


def downgrade() -> None:
    op.drop_column("agent_topologies", "operational_status", schema="project")
