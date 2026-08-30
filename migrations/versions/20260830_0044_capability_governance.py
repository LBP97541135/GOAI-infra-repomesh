"""Create capability_management schema: skill versions, evaluations, snapshots, MCP policies.

Revision ID: 20260830_0044
Revises: 20260828_0043
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260830_0044"
down_revision: str | None = "20260828_0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "capability_management"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    op.create_table(
        "skill_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("skill_id", sa.String(100), nullable=False, index=True),
        sa.Column("version", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, index=True),
        sa.Column("local_path", sa.String(500), nullable=False),
        sa.Column("content_hash", sa.String(80), nullable=False),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("skill_id", "version", name="uq_skill_versions_id_version"),
        schema=SCHEMA,
    )
    op.create_table(
        "skill_evaluations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("scenario", sa.String(500), nullable=False),
        sa.Column("negative_case", sa.String(500), nullable=False),
        sa.Column("outcome", sa.String(10), nullable=False),
        sa.Column("evidence", sa.String(2000), nullable=False),
        sa.Column("evaluated_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "skill_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True, index=True),
        sa.Column("versions", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "organization_id", "versions", name="uq_skill_snapshots_org_versions"
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "mcp_server_policies",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("max_retries", sa.Integer(), nullable=False),
        sa.Column("retryable_only_reads", sa.Boolean(), nullable=False),
        sa.Column("degraded_block_writes", sa.Boolean(), nullable=False),
        sa.Column("required_task_features", postgresql.JSONB(), nullable=False),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("mcp_server_policies", schema=SCHEMA)
    op.drop_table("skill_snapshots", schema=SCHEMA)
    op.drop_table("skill_evaluations", schema=SCHEMA)
    op.drop_table("skill_versions", schema=SCHEMA)
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}")
