"""Bind each checkpoint decision to one review request.

Revision ID: 20260811_0018
Revises: 20260811_0017
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260811_0018"
down_revision: str | None = "20260811_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "checkpoint_decisions",
        sa.Column("review_request_id", sa.Uuid(), nullable=True),
        schema="project",
    )
    op.create_index(
        "ix_project_checkpoint_decisions_review_request_id",
        "checkpoint_decisions",
        ["review_request_id"],
        schema="project",
    )
    op.execute(
        """
        UPDATE project.checkpoint_decisions AS decision
        SET review_request_id = review.id
        FROM project.human_review_requests AS review
        WHERE decision.review_request_id IS NULL
          AND review.project_id = decision.project_id
          AND review.checkpoint = decision.checkpoint
          AND review.repository_scope_key = COALESCE(decision.repository_id::text, '')
          AND review.evidence_version = decision.evidence_version
        """
    )
    op.create_unique_constraint(
        "uq_checkpoint_decision_review_request",
        "checkpoint_decisions",
        ["review_request_id"],
        schema="project",
    )
    op.alter_column(
        "checkpoint_decisions",
        "review_request_id",
        nullable=False,
        schema="project",
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_checkpoint_decision_review_request",
        "checkpoint_decisions",
        schema="project",
        type_="unique",
    )
    op.drop_index(
        "ix_project_checkpoint_decisions_review_request_id",
        table_name="checkpoint_decisions",
        schema="project",
    )
    op.drop_column("checkpoint_decisions", "review_request_id", schema="project")
