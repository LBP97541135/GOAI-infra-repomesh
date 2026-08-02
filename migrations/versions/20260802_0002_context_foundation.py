"""Create the versioned context foundation.

Revision ID: 20260802_0002
Revises: 20260802_0001
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260802_0002"
down_revision: str | None = "20260802_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "context_objects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("object_type", sa.String(50), nullable=False),
        sa.Column("scope", sa.String(30), nullable=False),
        sa.Column("owner_subject", sa.String(200), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="context",
    )
    for column in ("organization_id", "project_id", "object_type", "scope", "status"):
        op.create_index(
            f"ix_context_objects_{column}",
            "context_objects",
            [column],
            schema="context",
        )
    op.create_index(
        "ix_context_objects_project_scope",
        "context_objects",
        ["project_id", "scope"],
        schema="context",
    )

    op.create_table(
        "context_object_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "context_object_id",
            sa.Uuid(),
            sa.ForeignKey("context.context_objects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_uri", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(71), nullable=False),
        sa.Column("mime_type", sa.String(200), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "supersedes_version_id",
            sa.Uuid(),
            sa.ForeignKey("context.context_object_versions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "context_object_id",
            "version",
            name="uq_context_object_versions_object_version",
        ),
        schema="context",
    )
    op.create_index(
        "ix_context_object_versions_context_object_id",
        "context_object_versions",
        ["context_object_id"],
        schema="context",
    )
    op.create_index(
        "ix_context_object_versions_hash",
        "context_object_versions",
        ["content_hash"],
        schema="context",
    )

    op.create_table(
        "context_relations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "source_version_id",
            sa.Uuid(),
            sa.ForeignKey("context.context_object_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "target_version_id",
            sa.Uuid(),
            sa.ForeignKey("context.context_object_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("relation_type", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "source_version_id",
            "target_version_id",
            "relation_type",
            name="uq_context_relations_versions_type",
        ),
        schema="context",
    )
    op.create_index(
        "ix_context_relations_source_version_id",
        "context_relations",
        ["source_version_id"],
        schema="context",
    )
    op.create_index(
        "ix_context_relations_target_version_id",
        "context_relations",
        ["target_version_id"],
        schema="context",
    )

    op.create_table(
        "context_bundles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("task_spec_version_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(100), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("base_sha", sa.String(200), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column(
            "allowed_tools", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "allowed_paths", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "denied_paths", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "network_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(71), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", name="uq_context_bundles_run_id"),
        sa.UniqueConstraint("content_hash", name="uq_context_bundles_content_hash"),
        schema="context",
    )
    for column in (
        "project_id",
        "run_id",
        "task_spec_version_id",
        "agent_id",
        "repository_id",
        "expires_at",
    ):
        op.create_index(
            f"ix_context_bundles_{column}",
            "context_bundles",
            [column],
            schema="context",
        )
    op.create_index(
        "ix_context_bundles_project_agent",
        "context_bundles",
        ["project_id", "agent_id"],
        schema="context",
    )

    op.create_table(
        "context_bundle_items",
        sa.Column(
            "bundle_id",
            sa.Uuid(),
            sa.ForeignKey("context.context_bundles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "version_id",
            sa.Uuid(),
            sa.ForeignKey("context.context_object_versions.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "context_object_id",
            sa.Uuid(),
            sa.ForeignKey("context.context_objects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(71), nullable=False),
        sa.Column("mount_path", sa.Text(), nullable=False),
        sa.Column("required_read", sa.Boolean(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "bundle_id", "mount_path", name="uq_context_bundle_items_mount_path"
        ),
        schema="context",
    )
    op.create_index(
        "ix_context_bundle_items_context_object_id",
        "context_bundle_items",
        ["context_object_id"],
        schema="context",
    )

    op.create_table(
        "context_deltas",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "bundle_id",
            sa.Uuid(),
            sa.ForeignKey("context.context_bundles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("content_hash", sa.String(71), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "bundle_id", "sequence", name="uq_context_deltas_bundle_sequence"
        ),
        sa.UniqueConstraint("content_hash", name="uq_context_deltas_content_hash"),
        schema="context",
    )
    op.create_index(
        "ix_context_deltas_bundle_id",
        "context_deltas",
        ["bundle_id"],
        schema="context",
    )

    op.create_table(
        "context_delta_items",
        sa.Column(
            "delta_id",
            sa.Uuid(),
            sa.ForeignKey("context.context_deltas.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "version_id",
            sa.Uuid(),
            sa.ForeignKey("context.context_object_versions.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "context_object_id",
            sa.Uuid(),
            sa.ForeignKey("context.context_objects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(71), nullable=False),
        sa.Column("mount_path", sa.Text(), nullable=False),
        sa.Column("required_read", sa.Boolean(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "delta_id", "mount_path", name="uq_context_delta_items_mount_path"
        ),
        schema="context",
    )
    op.create_index(
        "ix_context_delta_items_context_object_id",
        "context_delta_items",
        ["context_object_id"],
        schema="context",
    )

    op.create_table(
        "context_access_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column(
            "bundle_id",
            sa.Uuid(),
            sa.ForeignKey("context.context_bundles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(71), nullable=False),
        sa.Column("result", sa.String(30), nullable=False),
        sa.Column("accessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema="context",
    )
    for column in ("project_id", "bundle_id", "run_id", "agent_id", "version_id", "result"):
        op.create_index(
            f"ix_context_access_events_{column}",
            "context_access_events",
            [column],
            schema="context",
        )
    op.create_index(
        "ix_context_access_events_run_accessed",
        "context_access_events",
        ["run_id", "accessed_at"],
        schema="context",
    )


def downgrade() -> None:
    for table in (
        "context_access_events",
        "context_delta_items",
        "context_deltas",
        "context_bundle_items",
        "context_bundles",
        "context_relations",
        "context_object_versions",
        "context_objects",
    ):
        op.drop_table(table, schema="context")
