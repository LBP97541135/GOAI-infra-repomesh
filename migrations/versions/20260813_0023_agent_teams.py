"""Persist composed AgentTeams Teams as first-class RepoMesh records.

Revision ID: 20260813_0023
Revises: 20260813_0022
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260813_0023"
down_revision: str | None = "20260813_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_teams",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(253), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("leader_agent_id", sa.Uuid(), nullable=False),
        sa.Column(
            "member_agent_ids",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("agentteams_team_name", sa.String(253), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_agent_teams"),
        sa.UniqueConstraint(
            "agentteams_team_name", name="uq_agent_teams_agentteams_team_name"
        ),
        schema="agent_directory",
    )
    op.create_index(
        "ix_agent_directory_agent_teams_organization_id",
        "agent_teams",
        ["organization_id"],
        schema="agent_directory",
    )
    op.create_index(
        "ix_agent_directory_agent_teams_leader_agent_id",
        "agent_teams",
        ["leader_agent_id"],
        schema="agent_directory",
    )
    op.create_index(
        "ix_agent_directory_agent_teams_repository_id",
        "agent_teams",
        ["repository_id"],
        schema="agent_directory",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_directory_agent_teams_repository_id",
        table_name="agent_teams",
        schema="agent_directory",
    )
    op.drop_index(
        "ix_agent_directory_agent_teams_leader_agent_id",
        table_name="agent_teams",
        schema="agent_directory",
    )
    op.drop_index(
        "ix_agent_directory_agent_teams_organization_id",
        table_name="agent_teams",
        schema="agent_directory",
    )
    op.drop_table("agent_teams", schema="agent_directory")
