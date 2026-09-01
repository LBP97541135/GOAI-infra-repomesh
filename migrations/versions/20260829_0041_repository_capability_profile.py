"""Add the team capability profile column to the repository catalog.

A repository team's agents assemble their capability bundle from role presets
alone: every Repository Leader gets the same six skills, every Worker the same
four, wherever the team sits. The cross-repo test team breaks that assumption —
its leader is a Repository Leader over the test-asset repository and needs
``cross-repo-test``; its Workers need ``integration-run``; business-repository
teams must not receive either. The distinguishing fact is a property of the
repository ("this repository's team works under the cross-repo-test profile"),
so the catalog row carries it, next to the other operator-owned facts
(``test_commands``/``test_paths``) rather than under scanner-rewritten
``metadata``.

Nullable with no default: NULL *is* the default profile, so existing rows need
no backfill and every reader can treat "no profile" and "default profile" as
one state.

Timing note for whoever operates this column: set it before onboarding the
repository's team. Capability bundles are resolved at dispatch time and follow
a later change, but AgentTeams skill lists are compared when a worker resource
is ensured, so a profile changed afterwards reaches only resources that do not
exist yet.

Revision ID: 20260829_0041
Revises: 20260828_0039
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260829_0041"
down_revision: str | None = "20260828_0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "repositories",
        sa.Column("capability_profile", sa.String(length=64), nullable=True),
        schema="repository_intelligence",
    )


def downgrade() -> None:
    """Drop the column.

    Loses which repositories run under specialised team profiles; capability
    assembly falls back to role presets for every team, which is the state
    before this revision existed.
    """

    op.drop_column("repositories", "capability_profile", schema="repository_intelligence")
