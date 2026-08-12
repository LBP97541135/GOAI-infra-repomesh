"""PostgreSQL-backed store for plan snapshots.

Each snapshot captures the complete DAG structure (nodes, edges, batches,
contracts) at a specific planning moment.

Mutability, stated accurately (contract v0.4 §2.5 / Q2)::

    A consumed snapshot (``execution_plan_id`` is not NULL) is immutable.
    The current draft (``execution_plan_id`` IS NULL) is mutable until it is
    consumed, and a project has at most one of them.

This file used to claim snapshots were write-once. That was already untrue
when it was written: :meth:`link_execution_plan` has always edited a persisted
row after materialize. v0.4 makes the draft's mutability load-bearing — the
discovery chain writes four steps and an approval into one draft row without
bumping the version — so the description is corrected here rather than left as
a second, contradicting account of the same fact.

Why the draft must not gain a version per step: ``GET /issues/{id}/
repositories/{repo}/plan`` reads ``snapshots[0]``, the highest version. A
discovery step that took a version of its own would make the newest snapshot
one with empty ``execution_batches``, and the DAG panel would render an empty
graph for a plan that exists.
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
    """PostgreSQL-backed store for plan snapshots (see the module docstring)."""

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
        discovery: dict | None = None,
    ) -> PlanSnapshotRecord:
        """Insert a new snapshot.

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
            discovery=discovery,
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
        async with self._database.transaction() as session:
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
        async with self._database.transaction() as session:
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
        async with self._database.transaction() as session:
            stmt = (
                select(PlanSnapshotRecord)
                .where(PlanSnapshotRecord.project_id == project_id)
                .order_by(desc(PlanSnapshotRecord.plan_version))
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def list_project_ids(self) -> tuple[UUID, ...]:
        """All project ids that ever produced a plan snapshot."""
        async with self._database.transaction() as session:
            result = await session.execute(
                select(PlanSnapshotRecord.project_id).distinct()
            )
            return tuple(result.scalars().all())

    async def next_version(self, project_id: UUID) -> int:
        """Get the next available plan_version for a project (starts at 1)."""
        latest = await self.get_latest(project_id)
        return (latest.plan_version + 1) if latest else 1

    async def link_execution_plan(
        self, snapshot_id: UUID, execution_plan_id: UUID
    ) -> None:
        """Back-fill the execution_plan_id after materialize succeeds.

        This is also what consumes a draft: once the column is set the row is
        immutable (module docstring) and the next round starts a new draft at
        ``next_version()``.
        """
        async with self._database.transaction() as session:
            stmt = select(PlanSnapshotRecord).where(
                PlanSnapshotRecord.id == snapshot_id
            )
            result = await session.execute(stmt)
            record = result.scalar_one_or_none()
            if record is not None:
                record.execution_plan_id = execution_plan_id

    async def current_draft(self, project_id: UUID) -> PlanSnapshotRecord | None:
        """Contract v0.4 §2.3: the highest unconsumed version, or None.

        "Unconsumed" is ``execution_plan_id IS NULL``. There is at most one per
        project in practice, and taking the highest version rather than
        asserting uniqueness means a project that somehow grew two drafts
        answers with the newer one instead of failing the whole panel.
        """

        async with self._database.transaction() as session:
            stmt = (
                select(PlanSnapshotRecord)
                .where(
                    PlanSnapshotRecord.project_id == project_id,
                    PlanSnapshotRecord.execution_plan_id.is_(None),
                )
                .order_by(desc(PlanSnapshotRecord.plan_version))
                .limit(1)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def set_discovery(self, snapshot_id: UUID, discovery: dict | None) -> None:
        """Replace the discovery block of one draft snapshot.

        Whole-block writes only. The caller reads the block, edits it, and
        hands back the result — read-modify-write in the service so that
        "re-running a step voids its downstream steps" is one statement rather
        than one per step that can land partially.
        """

        async with self._database.transaction() as session:
            result = await session.execute(
                select(PlanSnapshotRecord).where(PlanSnapshotRecord.id == snapshot_id)
            )
            record = result.scalar_one_or_none()
            if record is not None:
                record.discovery = discovery

    async def set_integration(
        self,
        snapshot_id: UUID,
        *,
        engineering_spec: str,
        contracts: list[dict],
        task_dag: list[dict],
        execution_batches: list[list[str]],
        integration_method: str | None = None,
    ) -> None:
        """Write the integration products back into the draft (v0.4 §4.3).

        Step 3 does not bump the version: the plan it produces belongs to the
        round the draft already represents.
        """

        async with self._database.transaction() as session:
            result = await session.execute(
                select(PlanSnapshotRecord).where(PlanSnapshotRecord.id == snapshot_id)
            )
            record = result.scalar_one_or_none()
            if record is None:
                return
            record.engineering_spec = engineering_spec
            record.contracts = contracts
            record.task_dag = task_dag
            record.execution_batches = execution_batches
            if integration_method is not None:
                record.integration_method = integration_method
