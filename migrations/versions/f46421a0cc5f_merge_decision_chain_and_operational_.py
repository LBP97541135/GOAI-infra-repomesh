"""merge decision-chain and operational-responses heads

Revision ID: f46421a0cc5f
Revises: 41c1e6609ea6, 20260831_0051
Create Date: 2026-09-01 10:45:25.642657
"""
from collections.abc import Sequence

revision: str = 'f46421a0cc5f'
down_revision: str | None = ('41c1e6609ea6', '20260831_0051')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
