"""Create encrypted platform credentials.

Revision ID: 20260826_0036
Revises: 20260816_0035
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260826_0036"
down_revision: str | None = "20260816_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "platform_credentials",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("is_encrypted", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["identity_access.local_human_accounts.id"],
            name="fk_platform_credentials_updated_by_local_human_accounts",
            ondelete="SET NULL",
        ),
        schema="platform",
    )


def downgrade() -> None:
    op.drop_table("platform_credentials", schema="platform")
