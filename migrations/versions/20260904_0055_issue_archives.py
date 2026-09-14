"""Issue archive tombstones (issue-grain archive, delivery_archives pattern).

Revision ID: 20260904_0055
Revises: 20260902_0054
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260904_0055"
down_revision: str | None = "20260902_0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS repository_intelligence")
    op.create_table(
        "issue_archives",
        sa.Column("issue_id", sa.Uuid(), primary_key=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=False),
        schema="repository_intelligence",
    )


def downgrade() -> None:
    op.drop_table("issue_archives", schema="repository_intelligence")
