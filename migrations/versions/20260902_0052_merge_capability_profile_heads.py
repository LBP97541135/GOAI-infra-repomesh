"""Merge the repository-capability-profile chain with main's two heads.

Three heads met when ``feat/module-test-team-v1`` merged ``main``:

* ``20260829_0041`` (repository capability profile) was written against the
  chain as it stood before main renumbered the duplicated 20260828 revisions,
  so its parent id ``20260828_0039`` now names a different migration and the
  revision hangs off the side of the chain instead of its end;
* ``20260831_0051`` (operational responses), the end of main's linear chain;
* ``41c1e6609ea6``, main's own merge of the decision-chain and room-timeline
  branches, which nothing after it revised.

``alembic upgrade head`` refuses to pick between heads, so this empty
revision joins all three. It creates nothing: every table and column already
arrived with its own revision.

Revision ID: 20260902_0052
Revises: 20260829_0041, 20260831_0051, 41c1e6609ea6
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "20260902_0052"
down_revision: str | Sequence[str] | None = (
    "20260829_0041",
    "20260831_0051",
    "41c1e6609ea6",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
