"""Create immutable specification storage.

Revision ID: 20260804_0006
Revises: 20260804_0005
Create Date: 2026-08-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260804_0006"
down_revision: str | None = "20260804_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "specifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=True),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("owner_agent_id", sa.Uuid(), nullable=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_fingerprint", sa.String(71), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_specifications_idempotency"
        ),
        schema="specification",
    )
    for column in (
        "organization_id",
        "project_id",
        "repository_id",
        "task_id",
        "kind",
        "status",
        "owner_agent_id",
        "current_version_id",
    ):
        op.create_index(
            f"ix_specifications_{column}",
            "specifications",
            [column],
            schema="specification",
        )
    op.create_table(
        "specification_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("specification_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "content", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("content_hash", sa.String(71), nullable=False),
        sa.Column("created_by_agent_id", sa.Uuid(), nullable=False),
        sa.Column("supersedes_version_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "specification_id",
            "version",
            name="uq_specification_versions_number",
        ),
        schema="specification",
    )
    for column in (
        "specification_id",
        "created_by_agent_id",
        "supersedes_version_id",
    ):
        op.create_index(
            f"ix_specification_versions_{column}",
            "specification_versions",
            [column],
            schema="specification",
        )


def downgrade() -> None:
    op.drop_table("specification_versions", schema="specification")
    op.drop_table("specifications", schema="specification")
