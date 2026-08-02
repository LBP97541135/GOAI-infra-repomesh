from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.persistence.base import Base

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


class ContextObjectRecord(Base):
    __tablename__ = "context_objects"
    __table_args__ = (
        Index("ix_context_objects_project_scope", "project_id", "scope"),
        {"schema": "context"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    project_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    object_type: Mapped[str] = mapped_column(String(50), index=True)
    scope: Mapped[str] = mapped_column(String(30), index=True)
    owner_subject: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(30), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ContextObjectVersionRecord(Base):
    __tablename__ = "context_object_versions"
    __table_args__ = (
        UniqueConstraint(
            "context_object_id", "version", name="uq_context_object_versions_object_version"
        ),
        Index("ix_context_object_versions_hash", "content_hash"),
        {"schema": "context"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    context_object_id: Mapped[UUID] = mapped_column(
        ForeignKey("context.context_objects.id", ondelete="RESTRICT"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    content_uri: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(71))
    mime_type: Mapped[str] = mapped_column(String(200))
    size_bytes: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    supersedes_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("context.context_object_versions.id", ondelete="RESTRICT"), nullable=True
    )


class ContextRelationRecord(Base):
    __tablename__ = "context_relations"
    __table_args__ = (
        UniqueConstraint(
            "source_version_id",
            "target_version_id",
            "relation_type",
            name="uq_context_relations_versions_type",
        ),
        {"schema": "context"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    source_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("context.context_object_versions.id", ondelete="RESTRICT"), index=True
    )
    target_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("context.context_object_versions.id", ondelete="RESTRICT"), index=True
    )
    relation_type: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ContextBundleRecord(Base):
    __tablename__ = "context_bundles"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_context_bundles_run_id"),
        Index("ix_context_bundles_project_agent", "project_id", "agent_id"),
        {"schema": "context"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    task_spec_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    agent_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    role: Mapped[str] = mapped_column(String(100))
    repository_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    base_sha: Mapped[str] = mapped_column(String(200))
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    allowed_tools: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, default=list)
    allowed_paths: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, default=list)
    denied_paths: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, default=list)
    network_policy: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, default=list)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    content_hash: Mapped[str] = mapped_column(String(71), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ContextBundleItemRecord(Base):
    __tablename__ = "context_bundle_items"
    __table_args__ = (
        UniqueConstraint("bundle_id", "mount_path", name="uq_context_bundle_items_mount_path"),
        {"schema": "context"},
    )

    bundle_id: Mapped[UUID] = mapped_column(
        ForeignKey("context.context_bundles.id", ondelete="CASCADE"), primary_key=True
    )
    version_id: Mapped[UUID] = mapped_column(
        ForeignKey("context.context_object_versions.id", ondelete="RESTRICT"), primary_key=True
    )
    context_object_id: Mapped[UUID] = mapped_column(
        ForeignKey("context.context_objects.id", ondelete="RESTRICT"), index=True
    )
    content_hash: Mapped[str] = mapped_column(String(71))
    mount_path: Mapped[str] = mapped_column(Text)
    required_read: Mapped[bool] = mapped_column(Boolean, default=False)
    position: Mapped[int] = mapped_column(Integer)


class ContextDeltaRecord(Base):
    __tablename__ = "context_deltas"
    __table_args__ = (
        UniqueConstraint("bundle_id", "sequence", name="uq_context_deltas_bundle_sequence"),
        {"schema": "context"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    bundle_id: Mapped[UUID] = mapped_column(
        ForeignKey("context.context_bundles.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(30))
    content_hash: Mapped[str] = mapped_column(String(71), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ContextDeltaItemRecord(Base):
    __tablename__ = "context_delta_items"
    __table_args__ = (
        UniqueConstraint("delta_id", "mount_path", name="uq_context_delta_items_mount_path"),
        {"schema": "context"},
    )

    delta_id: Mapped[UUID] = mapped_column(
        ForeignKey("context.context_deltas.id", ondelete="CASCADE"), primary_key=True
    )
    version_id: Mapped[UUID] = mapped_column(
        ForeignKey("context.context_object_versions.id", ondelete="RESTRICT"), primary_key=True
    )
    context_object_id: Mapped[UUID] = mapped_column(
        ForeignKey("context.context_objects.id", ondelete="RESTRICT"), index=True
    )
    content_hash: Mapped[str] = mapped_column(String(71))
    mount_path: Mapped[str] = mapped_column(Text)
    required_read: Mapped[bool] = mapped_column(Boolean, default=False)
    position: Mapped[int] = mapped_column(Integer)


class ContextAccessEventRecord(Base):
    __tablename__ = "context_access_events"
    __table_args__ = (
        Index("ix_context_access_events_run_accessed", "run_id", "accessed_at"),
        {"schema": "context"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    bundle_id: Mapped[UUID] = mapped_column(
        ForeignKey("context.context_bundles.id", ondelete="RESTRICT"), index=True
    )
    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    agent_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    # Keep attempted unknown version ids so denied/not-found reads remain auditable.
    version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    path: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(71))
    result: Mapped[str] = mapped_column(String(30), index=True)
    accessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    metadata_payload: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON_DOCUMENT, default=dict
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
