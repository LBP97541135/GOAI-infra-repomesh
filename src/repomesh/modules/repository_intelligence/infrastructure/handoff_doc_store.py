"""PostgreSQL-backed store for repository handoff documents.

Documents are upserted by ``id`` (a decision rewrites the row's status and
decision fields); listing filters by project/version/repository/status; and
``supersede_for_repos`` bulk-marks every open document of the affected
repositories as SUPERSEDED when a replan regenerates them.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update

from repomesh.modules.repository_intelligence.application.handoff_docs import (
    HandoffDoc,
    HandoffDocStatus,
)
from repomesh.modules.repository_intelligence.infrastructure.models import (
    HandoffDocRecord,
)
from repomesh.persistence import Database

_logger = logging.getLogger(__name__)


def _record_to_doc(record: HandoffDocRecord) -> HandoffDoc:
    return HandoffDoc(
        id=record.id,
        project_id=record.project_id,
        plan_version=record.plan_version,
        repository=record.repository,
        status=HandoffDocStatus(record.status),
        content=record.content,
        created_at=record.created_at,
        created_by_agent_id=record.created_by_agent_id,
        decided_by_agent_id=record.decided_by_agent_id,
        decision_reason=record.decision_reason,
        superseded_by_version=record.superseded_by_version,
    )


class PostgresHandoffDocStore:
    """PostgreSQL-backed store implementing the :class:`HandoffDocStore` port."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def save(self, doc: HandoffDoc) -> HandoffDoc:
        """Upsert a document by id (a decision rewrites an existing row).

        ``session.merge`` keeps the upsert portable across SQLite (tests) and
        PostgreSQL (production) without dialect-specific ``ON CONFLICT``.
        """
        now = datetime.now(UTC)
        record = HandoffDocRecord(
            id=doc.id,
            project_id=doc.project_id,
            plan_version=doc.plan_version,
            repository=doc.repository,
            status=doc.status.value,
            content=doc.content,
            created_at=doc.created_at,
            updated_at=now,
            created_by_agent_id=doc.created_by_agent_id,
            decided_by_agent_id=doc.decided_by_agent_id,
            decision_reason=doc.decision_reason,
            superseded_by_version=doc.superseded_by_version,
        )
        async with self._database.transaction() as session:
            await session.merge(record)
        _logger.debug(
            "Saved handoff doc %s (%s @ v%d)",
            doc.id, doc.repository, doc.plan_version,
        )
        return doc

    async def get(self, doc_id: UUID) -> HandoffDoc | None:
        async with self._database.transaction() as session:
            stmt = select(HandoffDocRecord).where(HandoffDocRecord.id == doc_id)
            result = await session.execute(stmt)
            record = result.scalar_one_or_none()
            return _record_to_doc(record) if record is not None else None

    async def list_docs(
        self,
        *,
        project_id: UUID,
        plan_version: int | None = None,
        repository: str | None = None,
        status: HandoffDocStatus | None = None,
    ) -> list[HandoffDoc]:
        async with self._database.transaction() as session:
            stmt = select(HandoffDocRecord).where(
                HandoffDocRecord.project_id == project_id
            )
            if plan_version is not None:
                stmt = stmt.where(HandoffDocRecord.plan_version == plan_version)
            if repository is not None:
                stmt = stmt.where(HandoffDocRecord.repository == repository)
            if status is not None:
                stmt = stmt.where(HandoffDocRecord.status == status.value)
            stmt = stmt.order_by(
                HandoffDocRecord.plan_version.desc(), HandoffDocRecord.repository
            )
            result = await session.execute(stmt)
            return [_record_to_doc(record) for record in result.scalars().all()]

    async def supersede_for_repos(
        self,
        *,
        project_id: UUID,
        repositories: Sequence[str],
        superseded_by_version: int,
    ) -> int:
        """Mark every open doc of *repositories* as SUPERSEDED (bulk update)."""
        async with self._database.transaction() as session:
            stmt = (
                update(HandoffDocRecord)
                .where(
                    HandoffDocRecord.project_id == project_id,
                    HandoffDocRecord.repository.in_(list(repositories)),
                    HandoffDocRecord.status != HandoffDocStatus.SUPERSEDED.value,
                    HandoffDocRecord.plan_version != superseded_by_version,
                )
                .values(
                    status=HandoffDocStatus.SUPERSEDED.value,
                    superseded_by_version=superseded_by_version,
                    updated_at=datetime.now(UTC),
                )
            )
            result = await session.execute(stmt)
            return result.rowcount or 0
