"""Create durable database branch validation runs.

Revision ID: 20260831_0050
Revises: 20260830_0049
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260831_0050"
down_revision: str | None = "20260830_0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "database_branch_validations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_sha", sa.String(40), nullable=False),
        sa.Column("source_database_ref", sa.String(200), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("provider_branch_ref", sa.String(200), nullable=True),
        sa.Column("engine_version", sa.String(120), nullable=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("failure_code", sa.String(80), nullable=True),
        sa.Column("cleanup_pending", sa.Boolean(), nullable=False),
        sa.Column("results", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_database_branch_validations"),
        sa.UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_database_branch_validations_org_idempotency",
        ),
        schema="review_validation",
    )
    for column in ("organization_id", "project_id", "repository_id", "status", "updated_at"):
        op.create_index(
            f"ix_database_branch_validations_{column}",
            "database_branch_validations",
            [column],
            schema="review_validation",
        )


def downgrade() -> None:
    columns = ("organization_id", "project_id", "repository_id", "status", "updated_at")
    for column in reversed(columns):
        op.drop_index(
            f"ix_database_branch_validations_{column}",
            table_name="database_branch_validations",
            schema="review_validation",
        )
    op.drop_table("database_branch_validations", schema="review_validation")
