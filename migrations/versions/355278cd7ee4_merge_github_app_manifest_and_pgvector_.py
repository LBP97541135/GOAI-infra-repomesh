"""merge github-app manifest and pgvector embeddings

Revision ID: 355278cd7ee4
Revises: 20260911_0055, 20260914_0056
Create Date: 2026-09-14 16:47:17.359741
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '355278cd7ee4'
down_revision: str | None = ('20260911_0055', '20260914_0056')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
