"""Workspace (organization) registry — contract v0.3 §2.1.

Before this table organization_id was a bare UUID column scattered across
modules with no name and no listing source; the console workspace switcher
therefore had nothing to show. Seed backfill for the demo organization is a
seed-script concern (idempotent), not a migration concern.

Revision ID: 20260811_0021
Revises: 20260811_0020
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260811_0021"
down_revision: str | None = "20260811_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="identity_access",
    )
    op.create_index(
        "ix_identity_access_organizations_name",
        "organizations",
        ["name"],
        unique=True,
        schema="identity_access",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_identity_access_organizations_name",
        table_name="organizations",
        schema="identity_access",
    )
    op.drop_table("organizations", schema="identity_access")
