"""Persist append-only, replayable SCM observations.

Revision ID: 20260809_0011
Revises: 20260809_0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260809_0011"
down_revision: str | None = "20260809_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scm_observations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("external_id", sa.String(200), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("change_set_id", sa.Uuid(), nullable=True),
        sa.Column("repository_id", sa.Uuid(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(2000), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "provider",
            "source",
            "external_id",
            name="uq_scm_observations_external_fact",
        ),
        schema="delivery",
    )
    for column in (
        "event_type",
        "status",
        "change_set_id",
        "repository_id",
    ):
        op.create_index(
            f"ix_scm_observations_{column}",
            "scm_observations",
            [column],
            schema="delivery",
        )


def downgrade() -> None:
    op.drop_table("scm_observations", schema="delivery")
