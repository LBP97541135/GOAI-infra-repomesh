"""Index ChangeSet repository delivery candidates.

Revision ID: 20260809_0012
Revises: 20260809_0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260809_0012"
down_revision: str | None = "20260809_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "change_set_repositories",
        sa.Column(
            "change_set_id",
            sa.Uuid(),
            sa.ForeignKey("delivery.change_sets.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("repository_id", sa.Uuid(), primary_key=True),
        sa.Column("head_sha", sa.String(40), nullable=False),
        sa.Column("pull_request_number", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(40), nullable=False),
        sa.UniqueConstraint(
            "change_set_id",
            "repository_id",
            name="uq_change_set_repositories_candidate",
        ),
        schema="delivery",
    )
    op.create_index(
        "ix_change_set_repositories_lookup",
        "change_set_repositories",
        ["repository_id", "head_sha"],
        schema="delivery",
    )
    op.execute(
        """
        INSERT INTO delivery.change_set_repositories
            (change_set_id, repository_id, head_sha, pull_request_number, status)
        SELECT
            cs.id,
            (candidate->>'repository_id')::uuid,
            candidate->>'commit_sha',
            NULLIF(candidate->>'pull_request_number', '')::integer,
            candidate->>'status'
        FROM delivery.change_sets AS cs
        CROSS JOIN LATERAL jsonb_array_elements(cs.payload->'repositories') AS candidate
        """
    )
    op.create_index(
        "ix_change_set_repositories_status",
        "change_set_repositories",
        ["status"],
        schema="delivery",
    )


def downgrade() -> None:
    op.drop_table("change_set_repositories", schema="delivery")
