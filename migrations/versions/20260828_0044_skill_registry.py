"""Create governed Skill Registry.

Revision ID: 20260828_0044
Revises: 20260828_0043
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260828_0044"
down_revision: str | None = "20260828_0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS capability_management")
    op.create_table(
        "skills",
        sa.Column("id", sa.String(120), primary_key=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("allowed_roles", postgresql.JSONB(), nullable=False),
        sa.Column("source_repository", sa.String(1000), nullable=False),
        sa.Column("source_path", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="capability_management",
    )
    op.create_table(
        "skill_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("skill_id", sa.String(120), nullable=False),
        sa.Column("version", sa.String(80), nullable=False),
        sa.Column("content_hash", sa.String(71), nullable=False),
        sa.Column("local_path", sa.String(1000), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["skill_id"], ["capability_management.skills.id"]),
        sa.UniqueConstraint("skill_id", "version", name="uq_skill_versions_semver"),
        schema="capability_management",
    )
    for column in ("skill_id", "state"):
        op.create_index(
            f"ix_skill_versions_{column}", "skill_versions", [column],
            schema="capability_management",
        )
    op.create_table(
        "skill_evaluations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.String(200), nullable=False),
        sa.Column("dataset_version", sa.String(100), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.Column("thresholds", postgresql.JSONB(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["version_id"], ["capability_management.skill_versions.id"]),
        schema="capability_management",
    )
    op.create_index(
        "ix_skill_evaluations_version_id", "skill_evaluations", ["version_id"],
        schema="capability_management",
    )
    op.create_table(
        "skill_releases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("skill_id", sa.String(120), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("traffic_percent", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["skill_id"], ["capability_management.skills.id"]),
        sa.ForeignKeyConstraint(["version_id"], ["capability_management.skill_versions.id"]),
        schema="capability_management",
    )
    for column in ("skill_id", "version_id", "channel", "status"):
        op.create_index(
            f"ix_skill_releases_{column}", "skill_releases", [column],
            schema="capability_management",
        )
    op.create_index(
        "uq_skill_releases_active_channel", "skill_releases", ["skill_id", "channel"],
        unique=True, postgresql_where=sa.text("status = 'active'"),
        schema="capability_management",
    )
    op.create_table(
        "skill_assignments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("skill_id", sa.String(120), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("release_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["version_id"], ["capability_management.skill_versions.id"]),
        sa.ForeignKeyConstraint(["release_id"], ["capability_management.skill_releases.id"]),
        sa.UniqueConstraint("task_id", "skill_id", name="uq_skill_assignments_task_skill"),
        schema="capability_management",
    )
    for column in ("task_id", "run_id", "skill_id"):
        op.create_index(
            f"ix_skill_assignments_{column}", "skill_assignments", [column],
            schema="capability_management",
        )


def downgrade() -> None:
    op.drop_table("skill_assignments", schema="capability_management")
    op.drop_table("skill_releases", schema="capability_management")
    op.drop_table("skill_evaluations", schema="capability_management")
    op.drop_table("skill_versions", schema="capability_management")
    op.drop_table("skills", schema="capability_management")
    op.execute("DROP SCHEMA IF EXISTS capability_management")
