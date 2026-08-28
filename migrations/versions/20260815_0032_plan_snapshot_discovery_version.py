"""Version the discovery block so concurrent writes are refused, not silent.

Contract v0.4 §4: every discovery step reads the draft's ``discovery`` block,
edits it, and writes the whole block back (read-modify-write in the service).
Two writers — a step running in a worker thread and an approval from another
tab, or two uvicorn workers sharing the database — can both read the same
block; whichever writes last silently wins and the first writer's work is
lost without a trace. ``discovery_version`` turns that race into a refused
write: ``set_discovery`` updates ``WHERE discovery_version = <version the
writer read>``, and a zero rowcount means the block moved while the writer
was working — surfaced as a 409 the panel can show, not a silent overwrite.

Zero for rows written before this migration: "the chain never wrote" is
version 0, which is exactly what a fresh draft answers on its first write.

Revision ID: 20260815_0032
Revises: 20260815_0031
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_0032"
down_revision: str | None = "20260815_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "plan_snapshots",
        sa.Column(
            "discovery_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        schema="repository_intelligence",
    )


def downgrade() -> None:
    op.drop_column("plan_snapshots", "discovery_version", schema="repository_intelligence")
