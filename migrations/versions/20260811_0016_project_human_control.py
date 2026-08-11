"""Add project execution modes and human grants.

Revision ID: 20260811_0016
Revises: 20260810_0015
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260811_0016"
down_revision: str | None = "20260810_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "local_human_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("password_hash", sa.String(64), nullable=False),
        sa.Column("password_salt", sa.String(32), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("username", name="uq_local_human_accounts_username"),
        schema="identity_access",
    )
    op.create_index(
        "ix_identity_access_local_human_accounts_username",
        "local_human_accounts",
        ["username"],
        schema="identity_access",
    )
    op.create_index(
        "ix_identity_access_local_human_accounts_active",
        "local_human_accounts",
        ["active"],
        schema="identity_access",
    )
    op.create_table(
        "local_human_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("identity_access.local_human_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("token_hash", name="uq_local_human_sessions_token_hash"),
        schema="identity_access",
    )
    for column in ("account_id", "token_hash", "expires_at"):
        op.create_index(
            f"ix_identity_access_local_human_sessions_{column}",
            "local_human_sessions",
            [column],
            schema="identity_access",
        )
    op.add_column(
        "agent_topologies",
        sa.Column("execution_mode", sa.String(30), nullable=False, server_default="auto"),
        schema="project",
    )
    op.create_table(
        "human_review_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("checkpoint", sa.String(40), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=True),
        sa.Column("repository_scope_key", sa.String(36), nullable=False, server_default=""),
        sa.Column("evidence_version", sa.String(200), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("requested_by_agent_id", sa.Uuid(), nullable=True),
        sa.Column("resolved_by_human_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "project_id",
            "checkpoint",
            "repository_scope_key",
            "evidence_version",
            name="uq_project_human_review_evidence",
        ),
        schema="project",
    )
    for column in ("project_id", "checkpoint", "repository_id", "evidence_version", "status"):
        op.create_index(
            f"ix_project_human_review_requests_{column}",
            "human_review_requests",
            [column],
            schema="project",
        )
    op.create_table(
        "checkpoint_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("checkpoint", sa.String(40), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=True),
        sa.Column("human_principal_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(30), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence_version", sa.String(200), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        schema="project",
    )
    for column in (
        "project_id",
        "checkpoint",
        "repository_id",
        "human_principal_id",
        "evidence_version",
    ):
        op.create_index(
            f"ix_project_checkpoint_decisions_{column}",
            "checkpoint_decisions",
            [column],
            schema="project",
        )
    op.add_column(
        "agent_topologies",
        sa.Column(
            "required_checkpoints",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        schema="project",
    )
    op.add_column(
        "agent_topologies",
        sa.Column(
            "human_grants",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        schema="project",
    )


def downgrade() -> None:
    op.drop_table("human_review_requests", schema="project")
    op.drop_table("checkpoint_decisions", schema="project")
    op.drop_column("agent_topologies", "human_grants", schema="project")
    op.drop_column("agent_topologies", "required_checkpoints", schema="project")
    op.drop_column("agent_topologies", "execution_mode", schema="project")
    op.drop_table("local_human_sessions", schema="identity_access")
    op.drop_table("local_human_accounts", schema="identity_access")
