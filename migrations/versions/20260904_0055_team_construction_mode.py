"""Let a repository team record who builds its code, and where (hosted-native M7).

0047 gave a team somewhere to say who decomposes its tasks. This is the column
that says who *builds* them: ``hosted_native`` — the team's copaw workers, in
their own controller-managed containers, which is the product's default — or
``local_cli`` — Bridges an operator runs outside the cluster, the line that was
the only one until now. Everything the two lines used to configure separately
(whether the controller containerizes a member, which controller runtime it is
asked for, whether adoption may raise the team into ``leader`` decomposition)
is derived from this one value in code (``project.contracts.derive_runtime``),
so the two runtime defaults that used to disagree — the onboarding request's
and the settings' — are gone rather than reconciled (spec D-17).

**Nothing is backfilled, and the default is the product default.** Every row
that exists at this revision means ``hosted_native``: no installation staffed a
team through a mode it could name, so there is nothing per row to compute. An
installation whose teams *are* served by Bridges flips those rows once, by
hand, after this revision::

    UPDATE project.repository_agent_teams SET construction_mode = 'local_cli'
     WHERE ...;

and sets ``REPOMESH_CONSTRUCTION_MODE_DEFAULT=local_cli`` so the teams it
creates from then on say so without the UPDATE. This file does not guess which
rows those are, for the same reason 0047 did not guess which teams were
adopted.

Revision ID: 20260904_0055
Revises: 20260902_0054
Create Date: 2026-09-04

Numbered 0055 because 0053 and 0054 were taken by the database test-team
handoff and the merge revision above it; the hosted-native spec's earlier
numbering (0053 for this column) was corrected on 2026-09-04.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260904_0055"
down_revision: str | None = "20260902_0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: ``hosted_native`` spelled once. A literal rather than an import of
#: ``ConstructionMode`` for the reason 0047 gives: a migration describes the
#: schema as it was at this revision, and an enum that changes later must not
#: silently change what this file did.
_HOSTED_NATIVE = "hosted_native"


def upgrade() -> None:
    op.add_column(
        "repository_agent_teams",
        sa.Column(
            "construction_mode",
            # String(20) rather than a PostgreSQL enum, matching
            # ``decomposition_mode`` and ``runtime_status`` beside it: a third
            # mode should be a code change, not a migration that must run
            # before any row can hold the value.
            sa.String(length=20),
            nullable=False,
            server_default=_HOSTED_NATIVE,
        ),
        schema="project",
    )
    # Asked once per assignment on the delivery path (which of the two
    # construction paths a task takes), and the name is the one the metadata
    # naming convention produces for ``index=True`` on the model, so
    # autogenerate against a database at this revision sees no drift.
    op.create_index(
        "ix_repository_agent_teams_construction_mode",
        "repository_agent_teams",
        ["construction_mode"],
        schema="project",
    )


def downgrade() -> None:
    """Drop the column.

    THIS LOSES WHICH TEAMS WERE STAFFED FOR BRIDGES. After a downgrade/upgrade
    round-trip every team reads ``hosted_native`` again, and a deployment that
    had flipped rows to ``local_cli`` has to flip them again: unlike 0047's
    column, nothing observes this fact and restores it — it is an operator's
    choice, not a controller's.

    What that means for work in flight: a task delivered under ``local_cli``
    went out as a runner dispatch a Bridge is leasing; after the round-trip
    the same team would deliver its next task as a hosted-native round to a
    worker that has no container. Reversible for the round-trip test and for
    rolling back a deploy on a database whose teams are all hosted-native,
    which is every database this revision is first applied to.
    """

    op.drop_index(
        "ix_repository_agent_teams_construction_mode",
        table_name="repository_agent_teams",
        schema="project",
    )
    op.drop_column("repository_agent_teams", "construction_mode", schema="project")
