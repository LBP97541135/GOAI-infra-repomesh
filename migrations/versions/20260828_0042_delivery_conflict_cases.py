"""Persist delivery base-drift and content-conflict cases.

Revision ID: 20260828_0042
Revises: 20260828_0041
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260828_0042"
down_revision: str | None = "20260828_0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conflict_cases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("change_set_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_head_sha", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("expected_base_sha", sa.String(64), nullable=False),
        sa.Column("observed_base_sha", sa.String(64), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("repair_task_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["change_set_id"], ["delivery.change_sets.id"], ondelete="CASCADE"
        ),
        schema="delivery",
    )
    for column in (
        "change_set_id", "project_id", "repository_id", "candidate_head_sha", "kind", "status"
    ):
        op.create_index(
            f"ix_delivery_conflict_cases_{column}", "conflict_cases", [column],
            schema="delivery",
        )
    op.create_index(
        "uq_delivery_conflict_cases_active_repository", "conflict_cases",
        ["change_set_id", "repository_id"], unique=True,
        postgresql_where=sa.text("status = 'open'"), schema="delivery",
    )


def downgrade() -> None:
    op.drop_table("conflict_cases", schema="delivery")
