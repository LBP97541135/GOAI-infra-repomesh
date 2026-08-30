"""Create unified Recovery Cases, decisions, and operations.

Revision ID: 20260828_0043
Revises: 20260828_0042
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260828_0043"
down_revision: str | None = "20260828_0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS recovery_management")
    op.add_column(
        "conflict_cases",
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        schema="delivery",
    )
    op.execute(
        "UPDATE delivery.conflict_cases AS c "
        "SET organization_id = s.organization_id "
        "FROM delivery.change_sets AS s WHERE s.id = c.change_set_id"
    )
    op.alter_column(
        "conflict_cases", "organization_id", nullable=False, schema="delivery"
    )
    op.create_index(
        "ix_delivery_conflict_cases_organization_id",
        "conflict_cases",
        ["organization_id"],
        schema="delivery",
    )
    op.create_table(
        "recovery_cases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source_type", sa.String(40), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=True),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("change_set_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("evidence_version", sa.String(300), nullable=False),
        sa.Column("available_actions", postgresql.JSONB(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("source_type", "source_id", name="uq_recovery_cases_source"),
        schema="recovery_management",
    )
    for column in (
        "source_type", "source_id", "organization_id", "project_id", "repository_id",
        "task_id", "change_set_id", "status", "severity",
    ):
        op.create_index(
            f"ix_recovery_cases_{column}", "recovery_cases", [column],
            schema="recovery_management",
        )
    op.create_table(
        "recovery_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("case_version", sa.Integer(), nullable=False),
        sa.Column("evidence_version", sa.String(300), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("decided_by_human_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"], ["recovery_management.recovery_cases.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("case_id", "case_version", name="uq_recovery_decisions_case_version"),
        schema="recovery_management",
    )
    op.create_index(
        "ix_recovery_decisions_case_id", "recovery_decisions", ["case_id"],
        schema="recovery_management",
    )
    op.create_index(
        "ix_recovery_decisions_decided_by_human_id", "recovery_decisions",
        ["decided_by_human_id"], schema="recovery_management",
    )
    op.create_table(
        "recovery_operations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("decision_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["case_id"], ["recovery_management.recovery_cases.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["decision_id"], ["recovery_management.recovery_decisions.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("decision_id", name="uq_recovery_operations_decision"),
        schema="recovery_management",
    )
    for column in ("case_id", "state"):
        op.create_index(
            f"ix_recovery_operations_{column}", "recovery_operations", [column],
            schema="recovery_management",
        )


def downgrade() -> None:
    op.drop_table("recovery_operations", schema="recovery_management")
    op.drop_table("recovery_decisions", schema="recovery_management")
    op.drop_table("recovery_cases", schema="recovery_management")
    op.execute("DROP SCHEMA IF EXISTS recovery_management")
    op.drop_index(
        "ix_delivery_conflict_cases_organization_id",
        table_name="conflict_cases",
        schema="delivery",
    )
    op.drop_column("conflict_cases", "organization_id", schema="delivery")
