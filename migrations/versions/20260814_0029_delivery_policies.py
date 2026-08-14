"""Persist organization and repository delivery policies.

Content of main's ``20260812_0020_delivery_policies``, re-seated onto this
branch's chain by the 2026-08-14 functional merge (D-2). The table is
unchanged; only the revision identity moved so the merged chain stays linear.

Revision ID: 20260814_0029
Revises: 20260814_0028
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260814_0029"
down_revision: str | None = "20260814_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "delivery_policies",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("repository_scope", sa.String(36), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=True),
        sa.Column("auto_merge", sa.Boolean(), nullable=False),
        sa.Column("base_branch", sa.String(255), nullable=False),
        sa.Column("required_checks", postgresql.JSONB(), nullable=False),
        sa.Column("required_approvals", sa.Integer(), nullable=False),
        sa.Column("contract_gate", sa.Boolean(), nullable=False),
        sa.Column("add_label", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("organization_id", "repository_scope"),
        sa.UniqueConstraint(
            "organization_id", "repository_scope", name="uq_delivery_policy_scope"
        ),
        schema="delivery",
    )
    op.create_index(
        "ix_delivery_delivery_policies_repository_id",
        "delivery_policies",
        ["repository_id"],
        schema="delivery",
    )


def downgrade() -> None:
    op.drop_table("delivery_policies", schema="delivery")
