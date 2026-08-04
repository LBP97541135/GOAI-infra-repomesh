"""Create project Agent topology storage.

Revision ID: 20260803_0004
Revises: 20260803_0003
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260803_0004"
down_revision: str | None = "20260803_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_topologies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("organization_leader_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_fingerprint", sa.String(71), nullable=False),
        sa.UniqueConstraint("project_id", name="uq_project_agent_topologies_project"),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_project_agent_topologies_idempotency"
        ),
        schema="project",
    )
    op.create_index(
        "ix_project_agent_topologies_organization_id",
        "agent_topologies",
        ["organization_id"],
        schema="project",
    )
    op.create_index(
        "ix_project_agent_topologies_project_id",
        "agent_topologies",
        ["project_id"],
        schema="project",
    )
    op.create_index(
        "ix_project_agent_topologies_organization_leader_id",
        "agent_topologies",
        ["organization_leader_id"],
        schema="project",
    )
    op.create_table(
        "repository_agent_teams",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "topology_id",
            sa.Uuid(),
            sa.ForeignKey("project.agent_topologies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("leader_agent_id", sa.Uuid(), nullable=False),
        sa.Column(
            "worker_agent_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("agentteams_team_name", sa.String(253), nullable=False),
        sa.Column("runtime_status", sa.String(30), nullable=False),
        sa.Column("room_id", sa.Text(), nullable=True),
        sa.Column("leader_room_id", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "project_id", "repository_id", name="uq_project_repository_agent_team"
        ),
        sa.UniqueConstraint(
            "agentteams_team_name", name="uq_project_agentteams_team_name"
        ),
        schema="project",
    )
    for column in (
        "topology_id",
        "project_id",
        "repository_id",
        "leader_agent_id",
        "runtime_status",
    ):
        op.create_index(
            f"ix_project_repository_agent_teams_{column}",
            "repository_agent_teams",
            [column],
            schema="project",
        )


def downgrade() -> None:
    op.drop_table("repository_agent_teams", schema="project")
    op.drop_table("agent_topologies", schema="project")
