"""Create observability.llm_usage for planning-phase LLM usage records.

Revision ID: 20260815_0031
Revises: 20260814_0030
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_0031"
down_revision: str | None = "20260814_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The foundation migration already creates every business schema; the
    # guarded statement keeps a partially-migrated database from failing here.
    op.execute("CREATE SCHEMA IF NOT EXISTS observability")
    op.create_table(
        "llm_usage",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False, server_default="chat"),
        sa.Column("issue_id", sa.Uuid(), nullable=True),
        sa.Column("discovery_step", sa.Integer(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("finish_reason", sa.String(32), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="ok"),
        schema="observability",
    )
    op.create_index(
        "ix_llm_usage_created_at", "llm_usage", ["created_at"], schema="observability"
    )
    op.create_index(
        "ix_llm_usage_issue_id", "llm_usage", ["issue_id"], schema="observability"
    )
    op.create_index("ix_llm_usage_model", "llm_usage", ["model"], schema="observability")


def downgrade() -> None:
    op.drop_index(
        "ix_llm_usage_model", table_name="llm_usage", schema="observability"
    )
    op.drop_index(
        "ix_llm_usage_issue_id", table_name="llm_usage", schema="observability"
    )
    op.drop_index(
        "ix_llm_usage_created_at", table_name="llm_usage", schema="observability"
    )
    op.drop_table("llm_usage", schema="observability")
