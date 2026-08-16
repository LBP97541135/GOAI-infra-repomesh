"""Widen platform.idempotency_records.idempotency_key.

Composite idempotency keys are derived by suffixing a base key with role and
sequence segments (``:leader``, ``:worker:NN``, ``:team``, ``:event:N``). On
redispatch the fully-qualified key overflowed ``varchar(200)`` and surfaced as
a bare ``StringDataRightTruncation`` 500 (M-10). Widen the column so the
composite key always fits.

Revision ID: 20260816_0035
Revises: 20260815_0034
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260816_0035"
down_revision: str | None = "20260815_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "idempotency_records",
        "idempotency_key",
        existing_type=sa.String(200),
        type_=sa.String(500),
        existing_nullable=False,
        schema="platform",
    )


def downgrade() -> None:
    op.alter_column(
        "idempotency_records",
        "idempotency_key",
        existing_type=sa.String(500),
        type_=sa.String(200),
        existing_nullable=False,
        schema="platform",
    )
