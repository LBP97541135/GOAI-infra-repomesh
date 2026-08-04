"""Create the Agent principal registry.

Revision ID: 20260803_0003
Revises: 20260802_0002
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260803_0003"
down_revision: str | None = "20260802_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_principals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(50), nullable=False),
        sa.Column(
            "leader_agent_id",
            sa.Uuid(),
            sa.ForeignKey("agent_directory.agent_principals.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("singleton_key", sa.String(200), nullable=True),
        sa.Column("repository_id", sa.Uuid(), nullable=True),
        sa.Column(
            "responsibility_paths", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("agentteams_resource_kind", sa.String(30), nullable=False),
        sa.Column("agentteams_resource_name", sa.String(253), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_fingerprint", sa.String(71), nullable=False),
        sa.UniqueConstraint(
            "agentteams_resource_kind",
            "agentteams_resource_name",
            name="uq_agent_principals_agentteams_resource",
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_agent_principals_idempotency_key"),
        sa.UniqueConstraint("singleton_key", name="uq_agent_principals_singleton_key"),
        schema="agent_directory",
    )
    for column in (
        "organization_id",
        "role",
        "leader_agent_id",
        "repository_id",
        "status",
    ):
        op.create_index(
            f"ix_agent_principals_{column}",
            "agent_principals",
            [column],
            schema="agent_directory",
        )
    op.create_index(
        "ix_agent_principals_repository_role",
        "agent_principals",
        ["repository_id", "role"],
        schema="agent_directory",
    )


def downgrade() -> None:
    op.drop_table("agent_principals", schema="agent_directory")
