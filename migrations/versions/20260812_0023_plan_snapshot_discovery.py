"""Give the discovery chain somewhere honest to live.

Contract v0.4 §2 / Q1. Between "create an issue" and "materialise a plan" the
pipeline produces four intermediate results — the sufficiency analysis and its
follow-up questions, the scored candidates, the three-tier classification, and
the leader's approval — and until now the server kept none of them: every step
endpoint was stateless and the driving script carried each response into the
next request. A browser cannot do that (refresh loses it, a second tab forks
it), so the chain has to land server-side.

Zero new *entities* still holds — no table, no Project row, no Discovery
aggregate. Zero new *columns* was never available: none of the existing columns
can hold this without carrying two meanings, and a column with two meanings is
the failure this codebase has already paid for once.

One JSONB column rather than four: the four steps are stages of one chain, read
together and voided together (§4.4).

Revision ID: 20260812_0023
Revises: 20260811_0022
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260812_0023"
down_revision: str | None = "20260811_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable with no server default: NULL is "the chain never ran", which is
    # a different fact from an empty object, and the read model projects the
    # two differently (§3.1 — an unrun step is null, never a hollow object).
    op.add_column(
        "plan_snapshots",
        sa.Column("discovery", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema="repository_intelligence",
    )


def downgrade() -> None:
    op.drop_column("plan_snapshots", "discovery", schema="repository_intelligence")
