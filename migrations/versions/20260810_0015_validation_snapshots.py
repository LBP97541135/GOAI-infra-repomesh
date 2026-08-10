"""Create immutable validation snapshots.

Revision ID: 20260810_0015
Revises: 20260810_0014
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260810_0015"
down_revision: str | None = "20260810_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS review_validation")
    op.create_table(
        "validation_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("specification_version_id", sa.Uuid(), nullable=True),
        sa.Column("environment_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        schema="review_validation",
    )
    for column in (
        "organization_id",
        "project_id",
        "environment_hash",
        "status",
        "expires_at",
    ):
        op.create_index(
            f"ix_validation_snapshots_{column}",
            "validation_snapshots",
            [column],
            schema="review_validation",
        )


def downgrade() -> None:
    op.drop_table("validation_snapshots", schema="review_validation")
