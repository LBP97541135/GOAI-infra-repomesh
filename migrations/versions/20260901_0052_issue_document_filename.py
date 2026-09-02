"""Add document_filename to plan snapshots.

Revision ID: 20260901_0052
Revises: f46421a0cc5f
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260901_0052"
down_revision: str | None = "f46421a0cc5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "plan_snapshots",
        sa.Column("document_filename", sa.String(255), nullable=True),
        schema="repository_intelligence",
    )


def downgrade() -> None:
    op.drop_column(
        "plan_snapshots", "document_filename", schema="repository_intelligence"
    )
