"""Add explicit leases to durable SCM commands.

Revision ID: 20260828_0038
Revises: 20260827_0037
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260828_0038"
down_revision: str | None = "20260827_0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "scm_commands",
        sa.Column("lease_owner", sa.String(128), nullable=True),
        schema="delivery",
    )
    op.add_column(
        "scm_commands",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        schema="delivery",
    )
    op.create_index(
        "ix_scm_commands_lease_expires_at",
        "scm_commands",
        ["lease_expires_at"],
        schema="delivery",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_scm_commands_lease_expires_at",
        table_name="scm_commands",
        schema="delivery",
    )
    op.drop_column("scm_commands", "lease_expires_at", schema="delivery")
    op.drop_column("scm_commands", "lease_owner", schema="delivery")
