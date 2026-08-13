"""Plan-snapshot graph consistency tests (PR-2).

Invariant under test: *read graph ≡ projection columns*. A snapshot row's
``graph_edges`` column must reconstruct a graph whose projections
(``execution_batches`` / ``contracts`` / ``task_dag``) equal the stored
columns — for new rows and for legacy rows backfilled at read time.
"""

from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from repomesh.modules.repository_intelligence.contracts import (  # noqa: E402
    ContractSpec,
    GraphEdge,
    IntegratedPlan,
    TaskNode,
    integration_method,
    normalize_plan,
    plan_to_graph,
)
from repomesh.modules.repository_intelligence.infrastructure.models import (  # noqa: E402
    PlanSnapshotRecord,
)
from repomesh.modules.repository_intelligence.infrastructure.plan_snapshot_store import (  # noqa: E402
    plan_graph_from_snapshot,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(**overrides) -> PlanSnapshotRecord:
    base = dict(
        id=uuid4(),
        project_id=uuid4(),
        plan_version=1,
        engineering_spec="spec",
        contracts=[],
        task_dag=[],
        execution_batches=[],
        graph_edges=[],
        integration_method=None,
    )
    base.update(overrides)
    return PlanSnapshotRecord(**base)


def _contract_dict(producer: str, consumer: str, interface: str) -> dict:
    return {"producer": producer, "consumer": consumer, "interface": interface,
            "agreement": "ok"}


def _task_dict(repo: str, depends_on: tuple[str, ...] = ()) -> dict:
    return {
        "repository": repo,
        "instruction": f"change {repo}",
        "depends_on": list(depends_on),
        "parallelizable_with": [],
        "tests": [],
    }


def _plan_with_dep_and_contract() -> IntegratedPlan:
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


def _record_from_plan(plan: IntegratedPlan, *, plan_version: int = 1) -> PlanSnapshotRecord:
    return _make_record(
        plan_version=plan_version,
        engineering_spec=plan.engineering_spec,
        contracts=[asdict(c) for c in plan.contracts],
        task_dag=[asdict(t) for t in plan.task_dag],
        execution_batches=[list(b) for b in plan.execution_batches],
        graph_edges=[e.model_dump(by_alias=True) for e in plan.graph.edges],
        integration_method=integration_method(plan.graph),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSnapshotGraphConsistency:
    def test_new_snapshot_graph_matches_projection_columns(self):
        plan = _plan_with_dep_and_contract()
        record = _record_from_plan(plan, plan_version=3)

        graph = plan_graph_from_snapshot(record)

        assert graph.plan_version == 3
        # Read graph ≡ projection columns.
        assert [list(b) for b in graph.execution_batches] == record.execution_batches
        assert [
            (c.producer, c.consumer, c.interface) for c in graph.contracts
        ] == [("A", "B", "API")]
        assert {t.repository: t.depends_on for t in graph.task_dag} == {
            "A": [],
            "B": ["A"],
        }
        # Edges survive the round-trip verbatim (from_ serialised as "from").
        assert [e.model_dump(by_alias=True) for e in graph.edges] == record.graph_edges

    def test_legacy_snapshot_backfill_matches_projection_columns(self):
        """A row saved before graph_edges was populated reconstructs a graph
        whose projections equal the stored columns."""
        plan = _plan_with_dep_and_contract()
        record = _record_from_plan(plan)
        record.graph_edges = []  # simulate a legacy row

        graph = plan_graph_from_snapshot(record)

        assert [list(b) for b in graph.execution_batches] == record.execution_batches
        assert [
            (c.producer, c.consumer, c.interface) for c in graph.contracts
        ] == [("A", "B", "API")]
        assert {t.repository: t.depends_on for t in graph.task_dag} == {
            "A": [],
            "B": ["A"],
        }

    def test_legacy_manual_batch_order_recovered_via_tm_edges(self):
        """Legacy rows whose ordering lived only in execution_batches must
        recover it as tm edges, keeping the projection equal to the column."""
        record = _make_record(
            engineering_spec="manual bridge",
            contracts=[],
            task_dag=[_task_dict("A"), _task_dict("B")],
            execution_batches=[["A"], ["B"]],
            graph_edges=[],
        )

        graph = plan_graph_from_snapshot(record)

        assert [e.source for e in graph.edges] == ["tm"]
        assert [list(b) for b in graph.execution_batches] == [["A"], ["B"]]
        assert {t.repository: t.depends_on for t in graph.task_dag} == {
            "A": [],
            "B": ["A"],
        }

    def test_legacy_backfill_single_contract_edge_not_duplicated(self):
        """A depends_on + contract pair in a legacy row yields one upgraded
        edge, not two — the contract projection must carry the interface."""
        record = _make_record(
            contracts=[_contract_dict("A", "B", "API")],
            task_dag=[_task_dict("A"), _task_dict("B", depends_on=("A",))],
            execution_batches=[["A"], ["B"]],
            graph_edges=[],
        )

        graph = plan_graph_from_snapshot(record)

        assert len(graph.edges) == 1
        edge = graph.edges[0]
        assert edge.from_ == "A" and edge.to == "B"
        assert edge.interface == "API"
        assert [c.interface for c in graph.contracts] == ["API"]

    def test_legacy_backfill_dependency_facts_win_over_batches(self):
        record = _make_record(
            contracts=[],
            task_dag=[
                _task_dict("A", depends_on=("B",)),
                _task_dict("B"),
            ],
            # contradictory manual order: A first, but B must finish first
            execution_batches=[["A"], ["B"]],
            graph_edges=[],
        )

        graph = plan_graph_from_snapshot(record)

        assert [list(b) for b in graph.execution_batches] == [["B"], ["A"]]
        assert {t.repository: t.depends_on for t in graph.task_dag} == {
            "A": ["B"],
            "B": [],
        }


class TestIntegrationMethodClassification:
    def test_graph_assisted_when_scan_edges_present(self):
        graph = plan_to_graph(
            IntegratedPlan(
                engineering_spec="s",
                contracts=[],
                task_dag=[
                    TaskNode(repository="A", instruction=""),
                    TaskNode(repository="B", instruction=""),
                ],
                execution_batches=[["A", "B"]],
            )
        )
        graph.edges = [
            GraphEdge(from_="A", to="B", status="confirmed", source="scan")
        ]
        assert integration_method(graph) == "graph_assisted"

    def test_llm_only_without_scan_edges(self):
        graph = plan_to_graph(
            IntegratedPlan(
                engineering_spec="s",
                contracts=[],
                task_dag=[
                    TaskNode(repository="A", instruction=""),
                    TaskNode(repository="B", instruction="", depends_on=("A",)),
                ],
                execution_batches=[["A"], ["B"]],
            )
        )
        assert {e.source for e in graph.edges} == {"llm"}
        assert integration_method(graph) == "llm_only"

    def test_llm_only_for_empty_graph(self):
        graph = plan_to_graph(
            IntegratedPlan(
                engineering_spec="s",
                contracts=[],
                task_dag=[TaskNode(repository="A", instruction="")],
                execution_batches=[["A"]],
            )
        )
        assert integration_method(graph) == "llm_only"
