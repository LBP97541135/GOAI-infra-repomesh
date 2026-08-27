"""Persist two-stage platform bootstrap operations.

Revision ID: 20260827_0037
Revises: 20260826_0036
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260827_0037"
down_revision: str | None = "20260826_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTIVE_PREDICATE = "state IN ('pending', 'running', 'waiting_for_user', 'retryable_failure')"


def upgrade() -> None:
    op.create_table(
        "bootstrap_operations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("phase", sa.String(64), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="0", nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_detail", sa.String(2000), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["identity_access.local_human_accounts.id"],
            name="fk_bootstrap_operations_requested_by_local_human_accounts",
            ondelete="SET NULL",
        ),
        schema="platform",
    )
    op.create_index(
        "ix_bootstrap_operations_state",
        "bootstrap_operations",
        ["state"],
        schema="platform",
    )
    op.create_index(
        "uq_bootstrap_operations_active_kind",
        "bootstrap_operations",
        ["kind"],
        unique=True,
        postgresql_where=sa.text(_ACTIVE_PREDICATE),
        schema="platform",
    )


def downgrade() -> None:
    op.drop_index(
        "uq_bootstrap_operations_active_kind",
        table_name="bootstrap_operations",
        schema="platform",
    )
    op.drop_index(
        "ix_bootstrap_operations_state",
        table_name="bootstrap_operations",
        schema="platform",
    )
    op.drop_table("bootstrap_operations", schema="platform")
