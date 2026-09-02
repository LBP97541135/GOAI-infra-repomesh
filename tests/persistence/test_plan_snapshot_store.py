"""PlanSnapshotStore persistence tests (PR-3).

``get_latest_graph`` is the read side of the replan source of truth: it must
reconstruct the latest plan-layer graph from the newest immutable row (with
legacy backfill), and ``next_version`` mints the re-planned version.
"""

from __future__ import annotations

from dataclasses import asdict
from uuid import uuid4

import pytest
import pytest_asyncio

from repomesh.modules.repository_intelligence.contracts import (
    ContractSpec,
    GraphEdge,
    GraphNode,
    IntegratedPlan,
    PlanGraph,
    TaskNode,
    diff_plan_graphs,
    integration_method,
    normalize_plan,
    plan_to_graph,
)
from repomesh.modules.repository_intelligence.infrastructure.plan_snapshot_store import (
    PlanSnapshotAlreadyExists,
    PlanSnapshotStore,
    PlanSnapshotVersionConflict,
    plan_graph_from_snapshot,
)
from repomesh.persistence import Database
from repomesh.persistence.base import ALL_SCHEMAS


def _plan() -> IntegratedPlan:
    plan = IntegratedPlan(
        engineering_spec="Deliver cross-repo change",
        contracts=[
            ContractSpec(producer="A", consumer="B", interface="API", agreement="ok")
        ],
        task_dag=[
            TaskNode(repository="A", instruction="do A"),
            TaskNode(repository="B", instruction="do B", depends_on=("A",)),
        ],
        execution_batches=[["A"], ["B"]],
    )
    return normalize_plan(plan, plan_to_graph(plan))


@pytest_asyncio.fixture
async def database(tmp_path: object) -> Database:
    instance = Database(
        f"sqlite+aiosqlite:///{tmp_path.joinpath('repomesh-plan-snapshots.db')}",
        schema_translate_map={schema: None for schema in ALL_SCHEMAS},
    )
    await instance.create_all_for_tests()
    yield instance
    await instance.dispose()


@pytest.mark.asyncio
async def test_link_execution_plan_consumes_draft_and_rejects_second_link(
    database: Database,
) -> None:
    """Linking consumes the draft; a concurrent second link loses the
    conditional UPDATE and surfaces as PlanSnapshotAlreadyExists."""
    store = PlanSnapshotStore(database)
    project_id = uuid4()
    plan = _plan()
    snapshot = await store.save(
        project_id=project_id,
        plan_version=1,
        engineering_spec=plan.engineering_spec,
        contracts=[asdict(c) for c in plan.contracts],
        task_dag=[asdict(t) for t in plan.task_dag],
        execution_batches=[list(b) for b in plan.execution_batches],
        graph_edges=[e.model_dump(by_alias=True) for e in plan.graph.edges],
    )
    assert snapshot.execution_plan_id is None

    plan_id = uuid4()
    await store.link_execution_plan(snapshot.id, plan_id)

    consumed = await store.get_by_version(project_id, 1)
    assert consumed is not None
    assert consumed.execution_plan_id == plan_id
    assert await store.current_draft(project_id) is None

    # The second caller racing the same draft gets a conflict, not a silent
    # second consumption (the storage-level half of the concurrent
    # materialization guard).
    with pytest.raises(PlanSnapshotAlreadyExists):
        await store.link_execution_plan(snapshot.id, uuid4())


@pytest.mark.asyncio
async def test_get_latest_graph_none_for_fresh_project(database: Database) -> None:
    store = PlanSnapshotStore(database)

    graph = await store.get_latest_graph(uuid4())

    assert graph is None


@pytest.mark.asyncio
async def test_get_latest_graph_round_trips_saved_edges(database: Database) -> None:
    store = PlanSnapshotStore(database)
    project_id = uuid4()
    plan = _plan()

    await store.save(
        project_id=project_id,
        plan_version=1,
        engineering_spec=plan.engineering_spec,
        contracts=[asdict(c) for c in plan.contracts],
        task_dag=[asdict(t) for t in plan.task_dag],
        execution_batches=[list(b) for b in plan.execution_batches],
        graph_edges=[e.model_dump(by_alias=True) for e in plan.graph.edges],
        created_by_agent_id=uuid4(),
        requirement_text="requirement",
        integration_method=integration_method(plan.graph),
    )

    graph = await store.get_latest_graph(project_id)

    assert graph is not None
    assert graph.plan_version == 1
    # Edges survive the round-trip verbatim (from_ serialised as "from").
    assert [e.model_dump(by_alias=True) for e in graph.edges] == [
        e.model_dump(by_alias=True) for e in plan.graph.edges
    ]
    # Read graph ≡ projection columns.
    assert [list(b) for b in graph.execution_batches] == [["A"], ["B"]]
    assert {t.repository: t.depends_on for t in graph.task_dag} == {
        "A": [],
        "B": ["A"],
    }


@pytest.mark.asyncio
async def test_get_latest_graph_backfills_legacy_empty_edges(
    database: Database,
) -> None:
    """A row saved before graph_edges was populated reconstructs the same
    graph the materialise-time path would, with projections equal to the
    stored columns."""
    store = PlanSnapshotStore(database)
    project_id = uuid4()
    plan = _plan()

    await store.save(
        project_id=project_id,
        plan_version=1,
        engineering_spec=plan.engineering_spec,
        contracts=[asdict(c) for c in plan.contracts],
        task_dag=[asdict(t) for t in plan.task_dag],
        execution_batches=[list(b) for b in plan.execution_batches],
        graph_edges=[],  # simulate a legacy row
        created_by_agent_id=uuid4(),
    )

    graph = await store.get_latest_graph(project_id)

    assert graph is not None
    assert [list(b) for b in graph.execution_batches] == [["A"], ["B"]]
    assert [(c.producer, c.consumer, c.interface) for c in graph.contracts] == [
        ("A", "B", "API")
    ]
    assert {t.repository: t.depends_on for t in graph.task_dag} == {
        "A": [],
        "B": ["A"],
    }


@pytest.mark.asyncio
async def test_next_version_mints_monotonic_versions(database: Database) -> None:
    """next_version starts at 1 for a fresh project and advances per save —
    the store is the single source of truth for the re-planned version."""
    store = PlanSnapshotStore(database)
    project_id = uuid4()

    assert await store.next_version(project_id) == 1

    plan = _plan()
    await store.save(
        project_id=project_id,
        plan_version=1,
        engineering_spec=plan.engineering_spec,
        contracts=[asdict(c) for c in plan.contracts],
        task_dag=[asdict(t) for t in plan.task_dag],
        execution_batches=[list(b) for b in plan.execution_batches],
        graph_edges=[e.model_dump(by_alias=True) for e in plan.graph.edges],
        created_by_agent_id=uuid4(),
    )

    assert await store.next_version(project_id) == 2


@pytest.mark.asyncio
async def test_diff_between_two_saved_snapshots(database: Database) -> None:
    """Two immutable rows diff through the store read path exactly as the
    materialised graphs do — the chain the API diff endpoint depends on."""
    store = PlanSnapshotStore(database)
    project_id = uuid4()
    v1 = _plan()
    v1_graph = plan_to_graph(v1)
    # v2 = v1 + a new confirmed edge (A -> C) in the plan-layer graph; the
    # graph is the single source of truth, so normalise from the graph.
    v2_graph = PlanGraph(
        plan_version=1,  # placeholder; the snapshot row owns the version
        nodes=list(v1_graph.nodes)
        + [GraphNode(repository="C", instruction="do C")],
        edges=list(v1_graph.edges)
        + [
            GraphEdge(
                from_="A",
                to="C",
                status="confirmed",
                source="llm",
                interface="API2",
                agreement="ok",
            )
        ],
    )
    v2 = normalize_plan(
        IntegratedPlan(
            engineering_spec=v1.engineering_spec,
            contracts=[],
            task_dag=[],
            execution_batches=[],
        ),
        v2_graph,
    )

    for version, plan in ((1, v1), (2, v2)):
        await store.save(
            project_id=project_id,
            plan_version=version,
            engineering_spec=plan.engineering_spec,
            contracts=[asdict(c) for c in plan.contracts],
            task_dag=[asdict(t) for t in plan.task_dag],
            execution_batches=[list(b) for b in plan.execution_batches],
            graph_edges=[e.model_dump(by_alias=True) for e in plan.graph.edges],
            created_by_agent_id=uuid4(),
            requirement_text="requirement",
            integration_method=integration_method(plan.graph),
        )

    from_record = await store.get_by_version(project_id, 1)
    to_record = await store.get_by_version(project_id, 2)
    assert from_record is not None and to_record is not None

    diff = diff_plan_graphs(
        plan_graph_from_snapshot(from_record),
        plan_graph_from_snapshot(to_record),
    )

    assert diff is not None
    assert diff.from_version == 1
    assert diff.to_version == 2
    assert [(e.from_, e.to) for e in diff.added_edges] == [("A", "C")]
    assert diff.added_repos == ["C"]
    assert diff.affected_repos == ["C"]


@pytest.mark.asyncio
async def test_set_discovery_writes_block_and_bumps_version(
    database: Database,
) -> None:
    """A fresh draft starts at discovery_version 0; writing the block at the
    version the caller read replaces it and advances the version — the next
    step's trigger reads the bumped version."""
    store = PlanSnapshotStore(database)
    project_id = uuid4()
    plan = _plan()
    snapshot = await store.save(
        project_id=project_id,
        plan_version=1,
        engineering_spec=plan.engineering_spec,
        contracts=[asdict(c) for c in plan.contracts],
        task_dag=[asdict(t) for t in plan.task_dag],
        execution_batches=[list(b) for b in plan.execution_batches],
        graph_edges=[e.model_dump(by_alias=True) for e in plan.graph.edges],
        requirement_text="requirement",
    )
    assert snapshot.discovery_version == 0
    assert snapshot.discovery is None

    block = {"schema_version": 1, "analysis": {"ok": True}}
    await store.set_discovery(snapshot.id, block, expected_version=0)

    written = await store.get_by_version(project_id, 1)
    assert written is not None
    assert written.discovery == block
    assert written.discovery_version == 1


@pytest.mark.asyncio
async def test_set_discovery_rejects_stale_version(
    database: Database,
) -> None:
    """Two writers that both read version N cannot both land: the second
    conditional UPDATE matches no row and refuses as a conflict instead of
    silently overwriting the first writer's block (v0.4 §4 optimistic lock)."""
    store = PlanSnapshotStore(database)
    project_id = uuid4()
    plan = _plan()
    snapshot = await store.save(
        project_id=project_id,
        plan_version=1,
        engineering_spec=plan.engineering_spec,
        contracts=[asdict(c) for c in plan.contracts],
        task_dag=[asdict(t) for t in plan.task_dag],
        execution_batches=[list(b) for b in plan.execution_batches],
        graph_edges=[e.model_dump(by_alias=True) for e in plan.graph.edges],
        requirement_text="requirement",
    )

    await store.set_discovery(snapshot.id, {"schema_version": 1}, expected_version=0)

    # A second writer that never saw the first write retries at version 0; its
    # WHERE clause matches nothing and the write is refused, keeping the first
    # writer's block intact.
    with pytest.raises(PlanSnapshotVersionConflict):
        await store.set_discovery(
            snapshot.id, {"schema_version": 2}, expected_version=0
        )

    written = await store.get_by_version(project_id, 1)
    assert written is not None
    assert written.discovery == {"schema_version": 1}
    assert written.discovery_version == 1


@pytest.mark.asyncio
async def test_set_discovery_reload_then_retry_succeeds(
    database: Database,
) -> None:
    """The recovery path the 409 tells the caller to take: reload the draft,
    read the new version, and the retry lands."""
    store = PlanSnapshotStore(database)
    project_id = uuid4()
    plan = _plan()
    snapshot = await store.save(
        project_id=project_id,
        plan_version=1,
        engineering_spec=plan.engineering_spec,
        contracts=[asdict(c) for c in plan.contracts],
        task_dag=[asdict(t) for t in plan.task_dag],
        execution_batches=[list(b) for b in plan.execution_batches],
        graph_edges=[e.model_dump(by_alias=True) for e in plan.graph.edges],
        requirement_text="requirement",
    )

    await store.set_discovery(snapshot.id, {"schema_version": 1}, expected_version=0)
    with pytest.raises(PlanSnapshotVersionConflict):
        await store.set_discovery(
            snapshot.id, {"schema_version": 2}, expected_version=0
        )

    # The loser reloads, sees version 1, and its write goes through.
    fresh = await store.get_by_version(project_id, 1)
    assert fresh is not None
    await store.set_discovery(
        snapshot.id, {"schema_version": 2}, expected_version=fresh.discovery_version
    )

    written = await store.get_by_version(project_id, 1)
    assert written is not None
    assert written.discovery == {"schema_version": 2}
    assert written.discovery_version == 2

