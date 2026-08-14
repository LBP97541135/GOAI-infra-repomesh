"""Unit tests for the unified dependency graph contracts and projections (PR-1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from repomesh.modules.repository_intelligence.contracts import (
    ContractEdgeView,
    GraphEdge,
    GraphNode,
    PlanGraph,
    TaskDagNodeView,
    derive_edges,
    project_batches,
    project_contracts,
    project_task_dag,
)


def node(name: str) -> GraphNode:
    return GraphNode(repository=name)


def edge(
    from_: str,
    to: str,
    *,
    status: str = "confirmed",
    source: str = "scan",
    interface: str | None = None,
    agreement: str | None = None,
) -> GraphEdge:
    return GraphEdge(
        from_=from_,
        to=to,
        status=status,
        source=source,
        interface=interface,
        agreement=agreement,
    )


# ---------------------------------------------------------------------------
# derive_edges
# ---------------------------------------------------------------------------


def test_derive_edges_deduplicates_keeping_first() -> None:
    nodes = [node("a"), node("b")]
    edges = [edge("a", "b"), edge("a", "b", source="llm")]
    derived = derive_edges(nodes, edges)
    assert len(derived) == 1
    assert derived[0].source == "scan"  # first occurrence wins


def test_derive_edges_drops_self_loops() -> None:
    nodes = [node("a")]
    derived = derive_edges(nodes, [edge("a", "a")])
    assert derived == []


def test_derive_edges_rejects_dangling_edge() -> None:
    nodes = [node("a")]
    with pytest.raises(ValueError, match="dangling edge"):
        derive_edges(nodes, [edge("a", "b")])


# ---------------------------------------------------------------------------
# project_batches
# ---------------------------------------------------------------------------


def test_project_batches_linear_chain() -> None:
    nodes = [node("a"), node("b"), node("c")]
    edges = [edge("a", "b"), edge("b", "c")]
    assert project_batches(nodes, edges) == [["a"], ["b"], ["c"]]


def test_project_batches_ignores_candidate_edges() -> None:
    nodes = [node("a"), node("b"), node("c")]
    edges = [
        edge("a", "b", status="candidate"),
        edge("b", "c", status="candidate"),
    ]
    assert project_batches(nodes, edges) == [["a", "b", "c"]]


def test_project_batches_parallel_repos_share_batch() -> None:
    nodes = [node("a"), node("b"), node("c")]
    edges = [edge("a", "b"), edge("a", "c")]
    assert project_batches(nodes, edges) == [["a"], ["b", "c"]]


def test_project_batches_nodes_without_edges_go_first() -> None:
    # c has no edges at all — free, shares batch 1.
    nodes = [node("a"), node("b"), node("c")]
    edges = [edge("a", "b")]
    assert project_batches(nodes, edges) == [["a", "c"], ["b"]]


def test_project_batches_empty_and_single() -> None:
    assert project_batches([], []) == []
    nodes = [node("a"), node("b")]
    assert project_batches(nodes, [edge("a", "b")]) == [["a"], ["b"]]


def test_project_batches_cycle_collapses_to_single_batch() -> None:
    nodes = [node("a"), node("b")]
    edges = [edge("a", "b"), edge("b", "a")]
    assert project_batches(nodes, edges) == [["a", "b"]]


def test_project_batches_cycle_with_external_dependency() -> None:
    # c → a → b → a : cycle {a, b} only after c is placed.
    nodes = [node("a"), node("b"), node("c")]
    edges = [edge("a", "b"), edge("b", "a"), edge("c", "a")]
    assert project_batches(nodes, edges) == [["c"], ["a", "b"]]


# ---------------------------------------------------------------------------
# project_contracts
# ---------------------------------------------------------------------------


def test_project_contracts_only_confirmed_edges_with_interface() -> None:
    edges = [
        edge("a", "b", interface="getCode", agreement="v2 contract"),
        edge("b", "c", status="candidate", interface="getCode"),
        edge("a", "c"),  # no interface → not a contract edge
    ]
    assert project_contracts(edges) == [
        ContractEdgeView(
            producer="a", consumer="b", interface="getCode", agreement="v2 contract"
        )
    ]


def test_project_contracts_empty() -> None:
    assert project_contracts([]) == []


# ---------------------------------------------------------------------------
# project_task_dag
# ---------------------------------------------------------------------------


def test_project_task_dag_orders_and_lists_dependencies() -> None:
    nodes = [node("b"), node("a")]
    edges = [edge("a", "b"), edge("c", "b", status="candidate")]
    dag = project_task_dag(nodes, edges)
    assert [d.repository for d in dag] == ["a", "b"]
    assert dag[1].depends_on == ["a"]  # candidate edge excluded


def test_project_task_dag_carries_node_semantics() -> None:
    nodes = [GraphNode(repository="a", instruction="ship api", tests=["pytest"])]
    dag = project_task_dag(nodes, [])
    assert dag == [
        TaskDagNodeView(repository="a", instruction="ship api", depends_on=[], tests=["pytest"])
    ]


# ---------------------------------------------------------------------------
# PlanGraph materialisation
# ---------------------------------------------------------------------------


def test_plan_graph_materialises_projections() -> None:
    graph = PlanGraph(
        plan_version=1,
        nodes=[node("a"), node("b")],
        edges=[edge("a", "b", interface="getCode")],
    )
    assert graph.execution_batches == [["a"], ["b"]]
    assert graph.contracts == [
        ContractEdgeView(producer="a", consumer="b", interface="getCode", agreement=None)
    ]
    assert [d.repository for d in graph.task_dag] == ["a", "b"]
    assert graph.task_dag[1].depends_on == ["a"]


def test_plan_graph_autocompletes_missing_nodes_from_edges() -> None:
    graph = PlanGraph(plan_version=1, nodes=[node("a")], edges=[edge("a", "b")])
    assert {n.repository for n in graph.nodes} == {"a", "b"}
    assert graph.execution_batches == [["a"], ["b"]]


def test_plan_graph_rejects_plan_version_below_one() -> None:
    with pytest.raises(ValidationError):
        PlanGraph(plan_version=0)


def test_plan_graph_roundtrip_consistency() -> None:
    """读图 ≡ 投影列：重建后投影必须与物化值一致。"""
    graph = PlanGraph(
        plan_version=2,
        nodes=[node("a"), node("b"), node("c")],
        edges=[
            edge("a", "b"),
            edge("b", "c", source="llm", interface="api", agreement="v3"),
        ],
    )
    rebuilt = PlanGraph.model_validate_json(graph.model_dump_json())
    assert rebuilt.execution_batches == graph.execution_batches
    assert rebuilt.contracts == graph.contracts
    assert rebuilt.task_dag == graph.task_dag


def test_plan_graph_json_uses_from_key() -> None:
    graph = PlanGraph(
        plan_version=1,
        nodes=[node("a"), node("b")],
        edges=[edge("a", "b")],
    )
    payload = graph.model_dump(by_alias=True)
    assert payload["edges"][0]["from"] == "a"
    assert "from_" not in payload["edges"][0]
