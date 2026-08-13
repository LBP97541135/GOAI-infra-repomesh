"""Persist repository onboarding jobs.

Revision ID: 20260813_0021
Revises: 20260812_0020
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260813_0021"
down_revision: str | None = "20260812_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    op.create_table(
        "repository_onboarding_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("org_url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("phase", sa.String(30), nullable=False),
        sa.Column("scan_workers", sa.Integer(), nullable=False),
        sa.Column("default_worker_count", sa.Integer(), nullable=False),
        sa.Column("requires_auth", sa.Boolean(), nullable=False),
        sa.Column("results", postgresql.JSONB(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_repository_onboarding_jobs_organization_id",
        "repository_onboarding_jobs",
        ["organization_id"],
        schema="platform",
    )
    op.create_index(
        "ix_platform_repository_onboarding_jobs_status",
        "repository_onboarding_jobs",
        ["status"],
        schema="platform",
    )

def downgrade() -> None:
    op.drop_table("repository_onboarding_jobs", schema="platform")
