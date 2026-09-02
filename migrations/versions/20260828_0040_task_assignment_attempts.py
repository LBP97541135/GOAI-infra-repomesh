"""Persist Task assignment generations for Worker recovery.

Revision ID: 20260828_0040
Revises: 20260828_0039
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260828_0040"
down_revision: str | None = "20260828_0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "task_assignment_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("worker_agent_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("reason", sa.String(40), nullable=False),
        sa.Column("assigned_by", sa.String(20), nullable=False),
        sa.Column("assigned_by_id", sa.Uuid(), nullable=True),
        sa.Column("previous_attempt_id", sa.Uuid(), nullable=True),
        sa.Column("execution_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["task_id"], ["task_orchestration.tasks.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["previous_attempt_id"],
            ["task_orchestration.task_assignment_attempts.id"],
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "task_id", "generation", name="uq_task_assignment_attempts_generation"
        ),
        schema="task_orchestration",
    )
    for column in (
        "organization_id",
        "project_id",
        "repository_id",
        "task_id",
        "worker_agent_id",
        "state",
    ):
        op.create_index(
            f"ix_task_assignment_attempts_{column}",
            "task_assignment_attempts",
            [column],
            schema="task_orchestration",
        )
    op.create_index(
        "uq_task_assignment_attempts_active_task",
        "task_assignment_attempts",
        ["task_id"],
        unique=True,
        postgresql_where=sa.text("state = 'active'"),
        schema="task_orchestration",
    )
    for table in ("worker_execution_reservations", "runner_dispatches"):
        op.add_column(
            table, sa.Column("assignment_attempt_id", sa.Uuid(), nullable=True),
            schema="agent_runtime",
        )
        op.add_column(
            table, sa.Column("assignment_generation", sa.Integer(), nullable=True),
            schema="agent_runtime",
        )
    op.add_column(
        "runner_dispatches",
        sa.Column("execution_id", sa.Uuid(), nullable=True),
        schema="agent_runtime",
    )
    op.add_column(
        "runner_dispatches",
        sa.Column("execution_version", sa.Integer(), nullable=True),
        schema="agent_runtime",
    )
    op.add_column(
        "runner_events",
        sa.Column("projection_status", sa.String(20), server_default="accepted", nullable=False),
        schema="agent_runtime",
    )
    op.create_table(
        "worker_recovery_operations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("execution_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("assignment_attempt_id", sa.Uuid(), nullable=True),
        sa.Column("assignment_generation", sa.Integer(), nullable=True),
        sa.Column("failed_worker_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("reason", sa.String(40), nullable=False),
        sa.Column("native_session_id", sa.String(512), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision", sa.String(30), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        schema="agent_runtime",
    )
    op.create_index(
        "ix_worker_recovery_operations_state",
        "worker_recovery_operations",
        ["state", "created_at"],
        schema="agent_runtime",
    )
    op.add_column(
        "runner_events",
        sa.Column("rejection_reason", sa.String(200), nullable=True),
        schema="agent_runtime",
    )


def downgrade() -> None:
    op.drop_table("worker_recovery_operations", schema="agent_runtime")
    op.drop_column("runner_events", "rejection_reason", schema="agent_runtime")
    op.drop_column("runner_events", "projection_status", schema="agent_runtime")
    op.drop_column("runner_dispatches", "execution_version", schema="agent_runtime")
    op.drop_column("runner_dispatches", "execution_id", schema="agent_runtime")
    for table in ("runner_dispatches", "worker_execution_reservations"):
        op.drop_column(table, "assignment_generation", schema="agent_runtime")
        op.drop_column(table, "assignment_attempt_id", schema="agent_runtime")
    op.drop_table("task_assignment_attempts", schema="task_orchestration")
