"""Hold a project's supervision policy before its topology exists.

Setting the policy and using it are two acts separated in time: an admin
decides it on the console, and materialization reads it back minutes later
while creating the topology. Nothing could carry a decision across that gap, so
every project materialized so far took the defaults — automatic, zero
checkpoints — and the review desk stayed empty because nobody was ever asked.

The three policy columns are deliberately the same names and the same types as
``project.agent_topologies``: materialization moves them across whole, without
reshaping, which is one fewer place to get the shape wrong.

Revision ID: 20260814_0030
Revises: 20260812_0027
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260814_0030"
down_revision: str | None = "20260812_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "topology_policy_drafts",
        # The requirement's id, which is the project id (delivery read-model
        # contract §0). One requirement holds one supervision intent, so this
        # is the whole key: writing again overwrites.
        sa.Column("project_id", sa.Uuid(), primary_key=True),
        sa.Column("execution_mode", sa.String(30), nullable=False, server_default="auto"),
        sa.Column(
            "required_checkpoints",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "human_grants",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        # The local account that set it. Kept because "who decided this project
        # would run unattended" is the question an audit asks first.
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema="project",
    )


def downgrade() -> None:
    op.drop_table("topology_policy_drafts", schema="project")
