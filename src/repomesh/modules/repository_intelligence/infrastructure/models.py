from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.persistence.base import Base

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


class RepositoryRecord(Base):
    __tablename__ = "repositories"
    __table_args__ = {"schema": "repository_intelligence"}

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    url: Mapped[str] = mapped_column(Text, unique=True)
    description: Mapped[str] = mapped_column(Text)
    topics: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, default=list)
    languages: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, default=list)
    test_commands: Mapped[list[str]] = mapped_column(
        JSON_DOCUMENT, nullable=False, server_default="[]", default=list
    )
    """Defect A-19: how this repository verifies itself, source of truth.

    A column rather than another key under ``metadata`` because it is read on
    the materialize path of every round and is meant to be edited by an
    operator; ``metadata`` carries the scanner's derived AutoCard, which is
    rewritten wholesale by the next scan.
    """
    test_paths: Mapped[list[str]] = mapped_column(
        JSON_DOCUMENT, nullable=False, server_default="[]", default=list
    )
    """Defect A-21: glob patterns the verification commands read from.

    Beside ``test_commands`` because they are one fact in two halves — a
    command without the path it reads is the trap that voided a whole run.
    """
    capability_profile: Mapped[str | None] = mapped_column(String(64), nullable=True)
    """Which team capability profile this repository's agents assemble under.

    Operator-owned and set before a team is onboarded: the AgentTeams skill
    lists are chosen when a worker resource is created, so a profile changed
    afterwards reaches new resources only. NULL means the default profile.
    """
    profiled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    metadata_payload: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON_DOCUMENT, default=dict
    )


class PlanSnapshotRecord(Base):
    __tablename__ = "plan_snapshots"
    __table_args__ = (
        UniqueConstraint("project_id", "plan_version", name="uq_plan_snapshots_project_version"),
        {"schema": "repository_intelligence"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    plan_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_by_agent_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    engineering_spec: Mapped[str] = mapped_column(Text)
    contracts: Mapped[list[dict[str, Any]]] = mapped_column(JSON_DOCUMENT, default=list)
    task_dag: Mapped[list[dict[str, Any]]] = mapped_column(JSON_DOCUMENT, default=list)
    execution_batches: Mapped[list[list[str]]] = mapped_column(JSON_DOCUMENT, default=list)
    graph_edges: Mapped[list[dict[str, Any]]] = mapped_column(JSON_DOCUMENT, default=list)
    execution_plan_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    requirement_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    integration_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    discovery: Mapped[dict[str, Any] | None] = mapped_column(
        JSON_DOCUMENT, nullable=True, default=None
    )
    """Contract v0.4 §2: the whole discovery chain for this round.

    One column rather than four because the four steps are four stages of one
    chain: they are read together and invalidated together (§4.4 — re-running
    an upstream step voids the downstream ones), and four columns would make
    that void a four-statement write that can land half-done.

    NULL means the chain was never started, which is not the same as an empty
    object; ``{}`` would be a chain that ran and produced nothing.
    """
    discovery_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", default=0
    )
    """Optimistic-lock version of the ``discovery`` block (v0.4 §4).

    Every step reads the block, edits it, and writes it back whole; a version
    guard makes two concurrent writers refuse instead of silently clobbering
    each other. ``set_discovery`` updates ``WHERE discovery_version = <the
    version the writer read>`` and bumps it; a zero rowcount means the block
    moved while the writer was working and the write is refused (409).
    """


class HandoffDocRecord(Base):
    """Repository handoff document (仓库对接文档) for human approval.

    One document per (repository, plan_version).  ``status`` moves through
    PENDING → APPROVED | REJECTED and is set to SUPERSEDED when a replan
    regenerates the document for a newer plan version.
    """

    __tablename__ = "handoff_docs"
    __table_args__ = {"schema": "repository_intelligence"}

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    plan_version: Mapped[int] = mapped_column(Integer)
    repository: Mapped[str] = mapped_column(String(200), index=True)
    status: Mapped[str] = mapped_column(String(20))
    content: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    created_by_agent_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    decided_by_agent_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    decision_reason: Mapped[str] = mapped_column(Text, default="")
    superseded_by_version: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
