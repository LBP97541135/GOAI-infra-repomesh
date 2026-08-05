"""Add RepoMesh-owned coding run control records.

Revision ID: 20260805_0007
Revises: 20260804_0006
Create Date: 2026-08-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260805_0007"
down_revision: str | None = "20260804_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("path", sa.String(2000), nullable=False, unique=True),
        sa.Column("base_sha", sa.String(200), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("bound_run_id", sa.Uuid(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="agent_runtime",
    )
    op.create_table(
        "coding_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("worker_agent_id", sa.Uuid(), nullable=False),
        sa.Column("adapter_id", sa.String(100), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("context_bundle_id", sa.Uuid(), nullable=False),
        sa.Column("context_bundle_hash", sa.String(71), nullable=False),
        sa.Column("coding_package_hash", sa.String(71), nullable=False),
        sa.Column("base_sha", sa.String(200), nullable=False),
        sa.Column("instruction", sa.String(10000), nullable=False),
        sa.Column("acceptance", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("required_tests", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("allowed_tools", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("allowed_paths", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("denied_paths", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("network_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("native_session_id", sa.String(1000), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="agent_runtime",
    )
    op.create_table(
        "session_bindings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("adapter_id", sa.String(100), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("context_bundle_hash", sa.String(71), nullable=False),
        sa.Column("coding_package_hash", sa.String(71), nullable=False),
        sa.Column("native_session_id", sa.String(1000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="agent_runtime",
    )
    for table, columns in {
        "workspaces": (
            "organization_id",
            "project_id",
            "repository_id",
            "task_id",
            "status",
            "bound_run_id",
        ),
        "coding_runs": (
            "organization_id",
            "project_id",
            "repository_id",
            "task_id",
            "worker_agent_id",
            "adapter_id",
            "workspace_id",
            "context_bundle_id",
            "status",
        ),
        "session_bindings": ("run_id", "task_id", "adapter_id", "workspace_id"),
    }.items():
        for column in columns:
            op.create_index(
                f"ix_{table}_{column}", table, [column], schema="agent_runtime"
            )


def downgrade() -> None:
    op.drop_table("session_bindings", schema="agent_runtime")
    op.drop_table("coding_runs", schema="agent_runtime")
    op.drop_table("workspaces", schema="agent_runtime")
