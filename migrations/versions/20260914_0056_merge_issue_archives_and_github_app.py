"""Merge the issue-archives chain with the GitHub-app-manifest chain.

Two heads met when ``feat/console-ui-overhaul`` merged ``main``:

* ``20260904_0055`` (issue archives + purge) tops the console-ui-overhaul
  chain through ``20260902_0054``;
* ``20260911_0055`` (GitHub App manifest and registration) tops main's
  chain through ``20260902_0054``.

Both descend from the shared ``20260902_0054`` merge ancestor, so this empty
revision only joins the two ends — it creates nothing: every table and column
already arrived with its own revision. ``alembic upgrade head`` refuses to
pick between heads, and the operational-readiness check
``alembic_single_head`` stays blocked until they are one.

Revision ID: 20260914_0056
Revises: 20260904_0055, 20260911_0055
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "20260914_0056"
down_revision: str | Sequence[str] | None = (
    "20260904_0055",
    "20260911_0055",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
