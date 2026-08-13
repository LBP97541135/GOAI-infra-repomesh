"""PlanDiff / diff_plan_graphs unit tests (PR-4).

The diff is pure computation over plan-layer graph entities — no storage, no
side effects — shared by the bridge (replan preview/commit) and the API diff
endpoint. These tests pin the contract semantics, including the §9.2 example
from the single-graph design doc.
"""

from __future__ import annotations

from repomesh.modules.repository_intelligence.contracts import (
    GraphEdge,
    GraphNode,
    PlanGraph,
    diff_plan_graphs,
)


def _graph(
    plan_version: int,
    repos: list[str],
    edges: list[tuple[str, str, str, str]],
) -> PlanGraph:
    return PlanGraph(
        plan_version=plan_version,
        nodes=[GraphNode(repository=r) for r in repos],
        edges=[
            GraphEdge(from_=frm, to=to, status=status, source=source)
            for frm, to, status, source in edges
        ],
    )


def test_diff_plan_graphs_matches_design_doc_example() -> None:
    """The §9.2 example: v1 -> v2 adds one confirmed edge and one repository;
    the affected set is exactly the newly involved consumer."""
    v1 = _graph(1, ["ts-verification-code-service"], [])
    v2 = _graph(
        2,
        ["ts-verification-code-service", "ts-auth-service"],
        [("ts-verification-code-service", "ts-auth-service", "confirmed", "llm")],
    )

    diff = diff_plan_graphs(v1, v2)

    assert diff is not None
    assert diff.from_version == 1
    assert diff.to_version == 2
    assert [e.model_dump(by_alias=True) for e in diff.added_edges] == [
        {
            "from": "ts-verification-code-service",
            "to": "ts-auth-service",
            "status": "confirmed",
            "source": "llm",
            "interface": None,
            "agreement": None,
        }
    ]
    assert diff.removed_edges == []
    assert diff.changed_edges == []
    assert diff.added_repos == ["ts-auth-service"]
    assert diff.removed_repos == []
    assert diff.affected_repos == ["ts-auth-service"]


def test_diff_plan_graphs_reports_added_removed_and_changed_edges() -> None:
    """Edge identity is the (producer, consumer) pair: new pairs are added,
    gone pairs are removed, surviving pairs with different attributes are
    changed (not duplicated into added/removed)."""
    v1 = _graph(
        1,
        ["a", "b", "c", "d"],
        [
            ("a", "b", "confirmed", "llm"),
            ("a", "c", "confirmed", "llm"),
            ("a", "d", "candidate", "scan"),
        ],
    )
    v2 = _graph(
        2,
        ["a", "b", "c", "e"],
        [
            ("a", "b", "confirmed", "llm"),  # unchanged
            ("a", "c", "confirmed", "tm"),  # attribute change: llm -> tm
            ("a", "e", "confirmed", "llm"),  # new consumer
        ],
    )

    diff = diff_plan_graphs(v1, v2)

    assert diff is not None
    assert [(e.from_, e.to) for e in diff.added_edges] == [("a", "e")]
    assert [(e.from_, e.to) for e in diff.removed_edges] == [("a", "d")]
    assert [(c.from_, c.to) for c in diff.changed_edges] == [("a", "c")]
    assert diff.changed_edges[0].old.source == "llm"
    assert diff.changed_edges[0].new.source == "tm"
    # affected = added_repos ∪ removed_repos ∪ consumers of added/removed edges
    assert diff.added_repos == ["e"]
    assert diff.removed_repos == ["d"]
    assert diff.affected_repos == ["d", "e"]


def test_diff_plan_graphs_identical_graphs_produce_empty_diff() -> None:
    """Diffing a version against itself yields no change (idempotent view)."""
    graph = _graph(
        2,
        ["a", "b"],
        [("a", "b", "confirmed", "llm")],
    )

    diff = diff_plan_graphs(graph, graph)

    assert diff is not None
    assert diff.added_edges == []
    assert diff.removed_edges == []
    assert diff.changed_edges == []
    assert diff.added_repos == []
    assert diff.removed_repos == []
    assert diff.affected_repos == []


def test_diff_plan_graphs_none_for_missing_side() -> None:
    """Without either version there is nothing to diff — None, not an empty
    diff (a project without a snapshot, or a preview with no new plan)."""
    graph = _graph(1, ["a"], [])

    assert diff_plan_graphs(None, graph) is None
    assert diff_plan_graphs(graph, None) is None
    assert diff_plan_graphs(None, None) is None


def test_diff_plan_graphs_output_is_deterministic() -> None:
    """Lists are sorted by edge key / repository name so repeated calls are
    byte-identical (diff endpoint idempotency)."""
    v1 = _graph(1, ["a", "b", "c"], [("a", "b", "confirmed", "llm")])
    v2 = _graph(
        2,
        ["a", "b", "c", "d", "e"],
        [
            ("a", "b", "confirmed", "llm"),
            ("c", "d", "confirmed", "llm"),
            ("a", "e", "confirmed", "llm"),
        ],
    )

    first = diff_plan_graphs(v1, v2)
    second = diff_plan_graphs(v1, v2)

    assert first is not None and second is not None
    assert first.model_dump(by_alias=True) == second.model_dump(by_alias=True)
    # Edge keys sort by (from_, to): ("a","e") precedes ("c","d").
    assert [e.to for e in first.added_edges] == ["e", "d"]
    # Repositories still sort by name.
    assert first.added_repos == ["d", "e"]
