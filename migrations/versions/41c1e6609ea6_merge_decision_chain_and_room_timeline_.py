"""merge decision-chain and room-timeline migration chains

Revision ID: 41c1e6609ea6
Revises: 20260828_0033, 20260830_0049
Create Date: 2026-08-31 09:33:47.993609
"""
from collections.abc import Sequence

revision: str = '41c1e6609ea6'
down_revision: str | None = ('20260828_0033', '20260830_0049')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
