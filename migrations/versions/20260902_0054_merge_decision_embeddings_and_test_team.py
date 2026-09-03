"""Merge the decision-embeddings chain with the database-test-team chain.

Two heads met when ``feat/repo-scan-chain-merge-main`` merged ``main``:

* ``20260901_0053`` (decision embeddings, L3 semantic retrieval) tops the
  chain through ``f46421a0cc5f`` → ``20260901_0052``;
* ``20260902_0053`` (database test team handoffs) tops main's chain through
  ``20260902_0052`` → ``20260831_0052``.

Both descend from the shared ``41c1e6609ea6``/``20260831_0051`` ancestors, so
this empty revision only joins the two ends — it creates nothing: every table
and column already arrived with its own revision. ``alembic upgrade head``
refuses to pick between heads, and the operational-readiness check
``alembic_single_head`` stays blocked until they are one.

Revision ID: 20260902_0054
Revises: 20260901_0053, 20260902_0053
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "20260902_0054"
down_revision: str | Sequence[str] | None = (
    "20260901_0053",
    "20260902_0053",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
