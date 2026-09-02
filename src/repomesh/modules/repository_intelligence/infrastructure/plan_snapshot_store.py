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

from sqlalchemy import desc, select, update
from sqlalchemy.exc import IntegrityError

from repomesh.modules.repository_intelligence.contracts import (
    ContractSpec,
    GraphEdge,
    GraphNode,
    IntegratedPlan,
    PlanGraph,
    TaskNode,
    plan_to_graph,
)
from repomesh.modules.repository_intelligence.infrastructure.models import (
    PlanSnapshotRecord,
)
from repomesh.persistence import Database
from repomesh.shared.domain import DomainError

_logger = logging.getLogger(__name__)


def plan_graph_from_snapshot(record: PlanSnapshotRecord) -> PlanGraph:
    """Reconstruct the plan-layer graph from an immutable snapshot row.

    The row is the single source of truth: ``plan_version`` + ``task_dag``
    (nodes) + ``graph_edges`` (edges). Legacy rows saved with an empty
    ``graph_edges`` are backfilled through the exact materialise-time path
    (:func:`plan_to_graph` over the row's task DAG, contracts and execution
    batches — all confirmed llm/tm edges), so *read graph ≡ projection
    columns* holds for every row, old and new.
    """

    nodes = [
        GraphNode(
            repository=entry["repository"],
            instruction=entry.get("instruction") or None,
            tests=list(entry.get("tests") or []),
        )
        for entry in record.task_dag
    ]

    raw_edges: list[dict] = record.graph_edges or []
    if raw_edges:
        edges = [GraphEdge.model_validate(entry) for entry in raw_edges]
    else:
        # Legacy backfill — identical to materialise-time plan_to_graph so a
        # legacy row reconstructs the same graph a fresh materialise would.
        legacy_plan = IntegratedPlan(
            engineering_spec=record.engineering_spec,
            contracts=[
                ContractSpec(
                    producer=entry["producer"],
                    consumer=entry["consumer"],
                    interface=entry.get("interface", ""),
                    agreement=entry.get("agreement", ""),
                )
                for entry in (record.contracts or [])
            ],
            task_dag=[
                TaskNode(
                    repository=entry["repository"],
                    instruction=entry.get("instruction", ""),
                    depends_on=tuple(entry.get("depends_on") or ()),
                    parallelizable_with=tuple(
                        entry.get("parallelizable_with") or ()
                    ),
                    tests=tuple(entry.get("tests") or ()),
                )
                for entry in record.task_dag
            ],
            execution_batches=[list(b) for b in (record.execution_batches or [])],
        )
        graph = plan_to_graph(legacy_plan)
        return graph.model_copy(update={"plan_version": record.plan_version})

    return PlanGraph(plan_version=record.plan_version, nodes=nodes, edges=edges)


class PlanSnapshotAlreadyExists(DomainError):
    """A snapshot with the same (project_id, plan_version) already exists."""


class PlanSnapshotVersionConflict(DomainError):
    """The discovery block moved between the caller's read and write.

    Optimistic-lock refusal (v0.4 §4): the caller read the draft at version N,
    edited the block, and by write time the row's ``discovery_version`` is no
    longer N — another writer landed in between. Re-writing would silently
    overwrite that writer's work, so the write is refused and the caller
    reloads and retries.
    """


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
        document_filename: str | None = None,
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
            document_filename=document_filename,
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

    async def get_latest_graph(self, project_id: UUID) -> PlanGraph | None:
        """Reconstruct the latest plan-layer graph for a project.

        Returns ``None`` when no snapshot exists yet (e.g. a fresh project
        before its first materialise). Legacy rows are backfilled at read
        time, so the returned graph always satisfies
        *read graph ≡ projection columns*.
        """
        record = await self.get_latest(project_id)
        if record is None:
            return None
        return plan_graph_from_snapshot(record)

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

        The update is conditional on the draft still being unconsumed. Two
        concurrent materializations with different idempotency keys can both
        read the same draft (each misses the other's receipt); the loser of
        this WHERE clause is the one that must not create a second execution
        plan, so a zero rowcount raises instead of silently succeeding.
        ``change_orchestration`` turns that into a recorded, replayable
        failure (RoundNotRecorded) and the winner's receipt answers the
        retry.
        """
        statement = (
            update(PlanSnapshotRecord)
            .where(
                PlanSnapshotRecord.id == snapshot_id,
                PlanSnapshotRecord.execution_plan_id.is_(None),
            )
            .values(execution_plan_id=execution_plan_id)
        )
        async with self._database.transaction() as session:
            result = await session.execute(statement)
        if not result.rowcount:
            raise PlanSnapshotAlreadyExists(
                f"snapshot {snapshot_id} was already consumed by another "
                "materialization; this round is already on record under a "
                "different idempotency key"
            )

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

    async def set_discovery(
        self, snapshot_id: UUID, discovery: dict | None, *, expected_version: int
    ) -> None:
        """Replace the discovery block of one draft snapshot (optimistic).

        Whole-block writes only. The caller reads the block, edits it, and
        hands back the result — read-modify-write in the service so that
        "re-running a step voids its downstream steps" is one statement rather
        than one per step that can land partially.

        ``expected_version`` is the ``discovery_version`` the caller read.
        The UPDATE is conditional on it: two writers that both read version N
        cannot both land — the second one's WHERE clause matches nothing and
        the write is refused as :class:`PlanSnapshotVersionConflict` instead
        of silently overwriting the first writer's block.
        """

        statement = (
            update(PlanSnapshotRecord)
            .where(
                PlanSnapshotRecord.id == snapshot_id,
                PlanSnapshotRecord.discovery_version == expected_version,
            )
            .values(
                discovery=discovery,
                discovery_version=expected_version + 1,
            )
        )
        async with self._database.transaction() as session:
            result = await session.execute(statement)
        if not result.rowcount:
            raise PlanSnapshotVersionConflict(
                f"snapshot {snapshot_id} discovery block changed while it was "
                f"being written (expected version {expected_version}); reload "
                "the draft and retry"
            )

    async def set_integration(
        self,
        snapshot_id: UUID,
        *,
        engineering_spec: str,
        contracts: list[dict],
        task_dag: list[dict],
        execution_batches: list[list[str]],
        graph_edges: list[dict] | None = None,
        integration_method: str | None = None,
    ) -> None:
        """Write the integration products back into the draft (v0.4 §4.3).

        Step 3 does not bump the version: the plan it produces belongs to the
        round the draft already represents. ``graph_edges`` carries the
        plan-layer graph so a console round's row satisfies the single-graph
        invariant (read graph ≡ projection columns) the same way a scripted
        ``save`` does.
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
            if graph_edges is not None:
                record.graph_edges = graph_edges
            if integration_method is not None:
                record.integration_method = integration_method
