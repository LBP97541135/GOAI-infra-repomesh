"""Let one AgentTeams Team belong to a repository rather than to a row.

Contract v0.4 §8.7.2 (defect A-8). ``repository_agent_teams`` was born with a
table-wide ``UNIQUE (agentteams_team_name)``, which read: no two topology rows
may point at the same AgentTeams Team. That was true only because the name was
minted from the row's own id — one row, one Team, trivially unique — and it was
true only for as long as no two projects shared a repository.

They do now, and the assumption underneath was wrong the whole time. A
repository's leader and workers are directory singletons, and an AgentTeams
Team holds its members *exclusively*, so a repository can only ever have one
Team. Three issues over the same repository must therefore point at one row's
worth of Team, and the old constraint made storing that impossible.

What is kept is the half that survives: within a single project the two
repositories are two different Teams. A project whose repositories collapsed
onto one Team would route both repositories' traffic through one room and give
nobody a reason to notice, so it is still worth a constraint.

**No row is rewritten here.** The stale per-row names stay exactly as they are.
The reconcile adopts the repository's real Team on the next projection and
writes the adopted name back itself
(``integrations/agentteams/project_topology.py``), which means convergence is
something the running system demonstrates rather than something this file
asserts. A migration that renamed rows would be guessing at controller state
from inside a transaction that cannot see it.

Revision ID: 20260812_0024
Revises: 20260812_0023
Create Date: 2026-08-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260812_0024"
down_revision: str | None = "20260812_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "repository_agent_teams"
_SCHEMA = "project"
_NAME = "uq_project_agentteams_team_name"


def upgrade() -> None:
    op.drop_constraint(_NAME, _TABLE, schema=_SCHEMA, type_="unique")
    op.create_unique_constraint(
        _NAME,
        _TABLE,
        ["project_id", "agentteams_team_name"],
        schema=_SCHEMA,
    )


def downgrade() -> None:
    """Reversible only while no repository is actually shared.

    Going back re-imposes "one row per Team name" on a table that may by then
    hold several rows legitimately naming one Team, and the constraint will
    refuse to build. That is the honest behaviour: the downgrade is failing
    because the data has moved past the schema, not because the migration is
    broken, and silently deduplicating names would destroy the rooms those rows
    point at.
    """

    op.drop_constraint(_NAME, _TABLE, schema=_SCHEMA, type_="unique")
    op.create_unique_constraint(
        _NAME,
        _TABLE,
        ["agentteams_team_name"],
        schema=_SCHEMA,
    )
