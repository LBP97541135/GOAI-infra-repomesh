"""Index repository_agent_teams by AgentTeams Team name.

The surviving half of main's ``20260812_0019_reuse_repository_agentteams_teams``,
re-seated onto this branch's chain by the 2026-08-14 functional merge
(docs/development/main-functional-merge-analysis-20260814.md, D-1/D-2).

Both lines dropped the table-wide ``UNIQUE (agentteams_team_name)`` for the
same reason — a repository's Team is shared by every project that touches the
repository. This branch's ``20260812_0024`` already replaced it with the
composite ``UNIQUE (project_id, agentteams_team_name)``, which admits every
reuse main wanted while keeping the within-project guard, so the drop half of
main's migration is not repeated here. What main added on top — a plain lookup
index on the shared name — is kept.

A database that already ran main's original ``20260812_0019`` is off this
chain and needs manual reconciliation; see the merge analysis §4.

Revision ID: 20260814_0028
Revises: 20260812_0027
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260814_0028"
down_revision: str | None = "20260812_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_project_repository_agent_teams_agentteams_team_name",
        "repository_agent_teams",
        ["agentteams_team_name"],
        schema="project",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_project_repository_agent_teams_agentteams_team_name",
        table_name="repository_agent_teams",
        schema="project",
    )
