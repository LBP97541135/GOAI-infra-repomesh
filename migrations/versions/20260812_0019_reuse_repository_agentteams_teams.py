"""Allow long-lived repository AgentTeams Teams to be reused by projects.

Revision ID: 20260812_0019
Revises: 20260811_0018
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260812_0019"
down_revision: str | None = "20260811_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_project_agentteams_team_name",
        "repository_agent_teams",
        schema="project",
        type_="unique",
    )
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
    op.create_unique_constraint(
        "uq_project_agentteams_team_name",
        "repository_agent_teams",
        ["agentteams_team_name"],
        schema="project",
    )
