"""Add task execution modes and durable Worker preflight assessments.

Revision ID: 20260807_0008
Revises: 20260806_0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_0008"
down_revision: str | None = "20260806_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "execution_mode",
            sa.String(30),
            nullable=False,
            server_default="coordination",
        ),
        schema="task_orchestration",
    )
    op.execute(
        """
        UPDATE task_orchestration.tasks AS task
        SET execution_mode = 'governed_worker'
        FROM agent_directory.agent_principals AS agent
        WHERE task.assignee_agent_id = agent.id AND agent.role = 'worker'
        """
    )
    op.alter_column(
        "tasks",
        "execution_mode",
        server_default=None,
        schema="task_orchestration",
    )
    op.create_index(
        "ix_tasks_execution_mode",
        "tasks",
        ["execution_mode"],
        schema="task_orchestration",
    )
    op.create_table(
        "worker_preflights",
        sa.Column("task_id", sa.Uuid(), primary_key=True),
        sa.Column("worker_agent_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(30), nullable=False),
        sa.Column("spec_understood", sa.Boolean(), nullable=False),
        sa.Column("scope_sufficient", sa.Boolean(), nullable=False),
        sa.Column("tests_defined", sa.Boolean(), nullable=False),
        sa.Column("dependencies_ready", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
        schema="agent_runtime",
    )
    op.create_index(
        "ix_worker_preflights_worker_agent_id",
        "worker_preflights",
        ["worker_agent_id"],
        schema="agent_runtime",
    )
    op.create_index(
        "ix_worker_preflights_decision",
        "worker_preflights",
        ["decision"],
        schema="agent_runtime",
    )


def downgrade() -> None:
    op.drop_table("worker_preflights", schema="agent_runtime")
    op.drop_index(
        "ix_tasks_execution_mode",
        table_name="tasks",
        schema="task_orchestration",
    )
    op.drop_column("tasks", "execution_mode", schema="task_orchestration")
