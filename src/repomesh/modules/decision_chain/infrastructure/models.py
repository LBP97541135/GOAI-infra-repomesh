"""``decision_chain_nodes`` row model (contract v0.1 §4.1).

The table is a read-side projection: chain fields + ``payload_summary`` +
``evidence_refs`` pointers. Full event payloads stay in ``platform.audit_events``
and the producer modules — this schema never double-writes them (red line 2).

One addition over the §4.1 column list: ``event_type``. The §6.1 trace output
must carry it on every node, and a projection row that needs a join back into
``platform.audit_events`` to answer that would couple every trace to the write
side's retention policy — so the projector stamps it onto the sheet itself.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    DateTime,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.persistence.base import Base

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


class DecisionNodeRecord(Base):
    __tablename__ = "decision_chain_nodes"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_decision_chain_nodes_event_id"),
        UniqueConstraint(
            "project_id",
            "step",
            "version",
            name="uq_decision_chain_nodes_project_step_version",
        ),
        {"schema": "decision_chain"},
    )

    decision_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    step: Mapped[str] = mapped_column(String(30))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30))
    actor: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    upstream_ref: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    evidence_refs: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    payload_summary: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    affected_repository_ids: Mapped[list[str]] = mapped_column(JSON_DOCUMENT)
    business_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    source: Mapped[str] = mapped_column(String(20))
    event_type: Mapped[str] = mapped_column(String(200))


class DecisionEmbeddingRecord(Base):
    """L3 ``decision_embeddings``: one vector per decision sheet.

    The vector is stored as a JSON document (JSONB on Postgres, JSON on the
    SQLite test twin) rather than a pgvector column. The chain is small —
    the same "JSON column + in-memory computation is fast enough" judgment
    the AGE decision already made — and the portable type keeps one schema
    for both databases. pgvector stays the documented upgrade path if the
    corpus ever reaches the scale where SQL-side ANN matters.
    """

    __tablename__ = "decision_embeddings"
    __table_args__ = ({"schema": "decision_chain"},)

    decision_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    embedding: Mapped[list[float]] = mapped_column(JSON_DOCUMENT)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
