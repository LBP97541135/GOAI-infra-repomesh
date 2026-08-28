"""Let a repository team record who decomposes its tasks (adjudication D-2).

0038 gave a parked batch somewhere to live. This is the column that decides
whether a batch parks at all: ``AdvanceExecutionPlan._assign_batch`` asks
``TeamDecompositionModeReader`` per batch item, and until now the composition
root answered ``server`` for every team from a placeholder, because no team had
anywhere to say otherwise. This is that somewhere.

Only the formal adoption pass writes ``leader``. ``ReconcileProjectAgentTopology``
already asks the controller for the leader's worker document -- that is how a
repository's Team is adopted rather than re-created (A-8) -- and the same
document carries ``containerManaged``. A leader the controller does not
containerize is an external Repository Leader with a Bridge serving it, and a
team whose leader plans for itself is exactly what ``leader`` means. So the fact
costs no new round-trip and comes from the same source PR 5.5A's preflight
confirms a binding against. There is deliberately no script, no console action
and no admin route that sets this column: D-2 says the mode is a consequence of
adoption, not a switch somebody flips.

**Nothing is backfilled, and nothing needs to be.** No installation had an
adopted external Repository Leader before this revision, so every existing row
means ``server`` and the column default says so. The first reconcile after
deploy is what promotes the teams that have really been adopted -- an UPDATE
here would be this file guessing at which ones those are.

The promotion is one-way in the domain
(``RepositoryTeam.with_adopted_leader``): a reconcile that runs during a
controller outage observes no external leader and must not thereby decompose
work a leader is in the middle of planning. That is a code invariant rather than
a database one -- a CHECK constraint cannot see the previous value -- so it is
stated here only so that whoever reads this column next knows the write side is
monotonic by intent.

Revision ID: 20260828_0037
Revises: 20260828_0038
Create Date: 2026-08-28

The revision number is *lower* than its parent, and that is not a mistake: the
chain order is merge order, and 0038 (PR 7, B track) landed before this one
(PR 5.5B, A track). The numbers were reserved per work order in the wave-0
baseline ledger, and renumbering to make them sort would rewrite a revision
already applied to running databases. Alembic orders by ``down_revision``, and
so should any reader.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260828_0037"
down_revision: str | None = "20260828_0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: ``server`` spelled once. Deliberately a literal rather than an import of
#: ``TeamDecompositionMode``: a migration describes the schema as it was at this
#: revision, and an enum that gains a member later must not silently change what
#: this file did.
_SERVER = "server"


def upgrade() -> None:
    op.add_column(
        "repository_agent_teams",
        sa.Column(
            "decomposition_mode",
            # String(20) rather than a PostgreSQL enum, matching
            # ``runtime_status`` on this table and ``phase`` on
            # ``leader_assignments``: a third mode should be a code change, not
            # a migration that must run before any row can hold the value.
            sa.String(length=20),
            nullable=False,
            server_default=_SERVER,
        ),
        schema="project",
    )
    # The reader is asked once per batch item on the dispatch path, and the
    # name is the one the metadata naming convention produces for
    # ``index=True`` on the model, so autogenerate against a database at this
    # revision sees no drift.
    op.create_index(
        "ix_repository_agent_teams_decomposition_mode",
        "repository_agent_teams",
        ["decomposition_mode"],
        schema="project",
    )


def downgrade() -> None:
    """Drop the column.

    THIS LOSES WHICH TEAMS WERE ADOPTED. A team promoted to ``leader`` comes
    back as ``server`` after a downgrade/upgrade round-trip, and the next
    reconcile is what restores it -- correctly, since the controller is the
    source of the fact and still holds it. What does not survive the gap is any
    batch parked in the meantime: with the mode gone, assignment decomposes
    server-side again and a leader's parked assignment in ``leader_assignments``
    is left with nothing that will ever read it.

    So this is reversible for the round-trip test and for rolling back a bad
    deploy on a database with no parked assignments, which is the same bargain
    0038 struck one revision down.
    """

    op.drop_index(
        "ix_repository_agent_teams_decomposition_mode",
        table_name="repository_agent_teams",
        schema="project",
    )
    op.drop_column("repository_agent_teams", "decomposition_mode", schema="project")
