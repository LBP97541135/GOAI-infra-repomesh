"""PostgreSQL-backed store for immutable plan snapshots.

Each snapshot captures the complete DAG structure (nodes, edges, batches,
contracts) at a specific planning moment. Snapshots are write-once: a
replan produces a new snapshot with ``plan_version + 1``.
"""

from __future__ import annotations

import logging
from uuid import UUID, uuid4

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError

from repomesh.modules.repository_intelligence.infrastructure.models import (
    PlanSnapshotRecord,
)
from repomesh.persistence import Database
from repomesh.shared.domain import DomainError

_logger = logging.getLogger(__name__)


class PlanSnapshotAlreadyExists(DomainError):
    """A snapshot with the same (project_id, plan_version) already exists."""


class PlanSnapshotStore:
    """PostgreSQL-backed store for immutable plan snapshots."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def save(
        self,
        *,
        project_id: UUID,
        plan_version: int,
        engineering_spec: str,
        contracts: list[dict],
        task_dag: list[dict],
        execution_batches: list[list[str]],
        graph_edges: list[dict],
        created_by_agent_id: UUID | None = None,
        execution_plan_id: UUID | None = None,
        requirement_text: str | None = None,
        integration_method: str | None = None,
    ) -> PlanSnapshotRecord:
        """Insert a new immutable snapshot.

        Raises PlanSnapshotAlreadyExists on (project_id, plan_version) conflict.
        """
        record = PlanSnapshotRecord(
            id=uuid4(),
            project_id=project_id,
            plan_version=plan_version,
            created_by_agent_id=created_by_agent_id,
            engineering_spec=engineering_spec,
            contracts=contracts,
            task_dag=task_dag,
            execution_batches=execution_batches,
            graph_edges=graph_edges,
            execution_plan_id=execution_plan_id,
            requirement_text=requirement_text,
            integration_method=integration_method,
        )
        try:
            async with self._database.transaction() as session:
                session.add(record)
        except IntegrityError as exc:
            raise PlanSnapshotAlreadyExists(
                f"Snapshot already exists for project {project_id} "
                f"version {plan_version}"
            ) from exc

        _logger.info(
            "Saved plan snapshot %s for project %s version %d",
            record.id, project_id, plan_version,
        )
        return record

    async def get_latest(
        self, project_id: UUID
    ) -> PlanSnapshotRecord | None:
        """Get the highest-version snapshot for a project."""
        async with self._database.session() as session:
            stmt = (
                select(PlanSnapshotRecord)
                .where(PlanSnapshotRecord.project_id == project_id)
                .order_by(desc(PlanSnapshotRecord.plan_version))
                .limit(1)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_by_version(
        self, project_id: UUID, plan_version: int
    ) -> PlanSnapshotRecord | None:
        """Get a specific version."""
        async with self._database.session() as session:
            stmt = select(PlanSnapshotRecord).where(
                PlanSnapshotRecord.project_id == project_id,
                PlanSnapshotRecord.plan_version == plan_version,
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_all(
        self, project_id: UUID
    ) -> list[PlanSnapshotRecord]:
        """List all snapshots for a project, ordered by version descending."""
        async with self._database.session() as session:
            stmt = (
                select(PlanSnapshotRecord)
                .where(PlanSnapshotRecord.project_id == project_id)
                .order_by(desc(PlanSnapshotRecord.plan_version))
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def next_version(self, project_id: UUID) -> int:
        """Get the next available plan_version for a project (starts at 1)."""
        latest = await self.get_latest(project_id)
        return (latest.plan_version + 1) if latest else 1

    async def link_execution_plan(
        self, snapshot_id: UUID, execution_plan_id: UUID
    ) -> None:
        """Back-fill the execution_plan_id after materialize succeeds."""
        async with self._database.transaction() as session:
            stmt = select(PlanSnapshotRecord).where(
                PlanSnapshotRecord.id == snapshot_id
            )
            result = await session.execute(stmt)
            record = result.scalar_one_or_none()
            if record is not None:
                record.execution_plan_id = execution_plan_id
