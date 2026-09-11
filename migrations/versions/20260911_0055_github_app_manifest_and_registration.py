"""GitHub App manifest states and registration.

Revision ID: 20260911_0055
Revises: 20260902_0054
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260911_0055"
down_revision: str | None = "20260902_0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "github_app_manifest_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column("requested_name", sa.String(64), nullable=False),
        sa.Column("requested_owner", sa.String(64), nullable=True),
        sa.Column("requested_origin", sa.String(256), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_github_app_manifest_states"),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["identity_access.local_human_accounts.id"],
            name="fk_github_app_manifest_states_requested_by_local_human_accounts",
            ondelete="SET NULL",
        ),
        schema="platform",
    )
    op.create_index(
        "ix_github_app_manifest_states_state_hash",
        "github_app_manifest_states",
        ["state_hash"],
        unique=True,
        schema="platform",
    )
    op.create_table(
        "github_app_registrations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("app_id", sa.BigInteger(), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("owner_login", sa.String(64), nullable=False),
        sa.Column("owner_type", sa.String(32), nullable=False),
        sa.Column("requested_owner_login", sa.String(64), nullable=True),
        sa.Column("installation_id", sa.BigInteger(), nullable=True),
        sa.Column("installation_account_login", sa.String(64), nullable=True),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_github_app_registrations"),
        sa.UniqueConstraint("app_id", name="uq_github_app_registrations_app_id"),
        schema="platform",
    )


def downgrade() -> None:
    op.drop_table("github_app_registrations", schema="platform")
    op.drop_index(
        "ix_github_app_manifest_states_state_hash",
        table_name="github_app_manifest_states",
        schema="platform",
    )
    op.drop_table("github_app_manifest_states", schema="platform")
