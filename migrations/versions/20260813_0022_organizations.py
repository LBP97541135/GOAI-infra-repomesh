"""Persist first-class organizations and bind onboarding jobs to them.

Revision ID: 20260813_0022
Revises: 20260813_0021
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260813_0022"
down_revision: str | None = "20260813_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(120), nullable=False),
        sa.Column("scm_provider", sa.String(20), nullable=False),
        sa.Column("scm_organization_url", sa.Text(), nullable=True),
        sa.Column("default_model", sa.String(100), nullable=True),
        sa.Column("default_worker_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_organizations"),
        sa.UniqueConstraint("slug", name="uq_organizations_slug"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_organizations_slug",
        "organizations",
        ["slug"],
        schema="platform",
    )
    op.create_foreign_key(
        "fk_repository_onboarding_jobs_organization_id_organizations",
        "repository_onboarding_jobs",
        "organizations",
        ["organization_id"],
        ["id"],
        source_schema="platform",
        referent_schema="platform",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_repository_onboarding_jobs_organization_id_organizations",
        "repository_onboarding_jobs",
        schema="platform",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_platform_organizations_slug",
        table_name="organizations",
        schema="platform",
    )
    op.drop_table("organizations", schema="platform")
