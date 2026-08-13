"""Plan-layer graph version diff contracts (PR-4).

Diffing two immutable plan snapshots is pure computation over the graph
entities — no storage, no side effects. ``diff_plan_graphs`` is the single
implementation shared by the bridge (replan preview/commit) and the API diff
endpoint, so preview and commit always describe the same change.

Edge identity is the ``(from_, to)`` producer/consumer pair (``derive_edges``
deduplicates by that key, so a pair is unique within a version). A pair that
exists in both versions but carries different attributes is reported in
``changed_edges``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from repomesh.modules.repository_intelligence.contracts.graph import (
    EdgeSource,
    EdgeStatus,
    GraphEdge,
    PlanGraph,
)


class DiffEdge(BaseModel):
    """An edge difference (producer -> consumer) with its version snapshot."""

    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(
        ...,
        serialization_alias="from",
        validation_alias="from",
        description="Producer repository (depended upon).",
    )
    to: str = Field(..., description="Consumer repository (dependent).")
    status: EdgeStatus | None = None
    source: EdgeSource | None = None
    interface: str | None = None
    agreement: str | None = None


class EdgeChangeView(BaseModel):
    """Attribute change of a ``(from_, to)`` edge across two versions."""

    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(..., serialization_alias="from", validation_alias="from")
    to: str = Field(...)
    old: DiffEdge
    new: DiffEdge


class PlanDiff(BaseModel):
    """Graph difference between two plan-layer snapshot versions.

    ``affected_repos`` is the repository-level change footprint:
    ``added_repos ∪ removed_repos ∪ {to of every added/removed edge}``.
    """

    from_version: int
    to_version: int
    added_edges: list[DiffEdge] = Field(default_factory=list)
    removed_edges: list[DiffEdge] = Field(default_factory=list)
    changed_edges: list[EdgeChangeView] = Field(default_factory=list)
    added_repos: list[str] = Field(default_factory=list)
    removed_repos: list[str] = Field(default_factory=list)
    affected_repos: list[str] = Field(default_factory=list)


def _edge_key(edge: GraphEdge) -> tuple[str, str]:
    return (edge.from_, edge.to)


def _to_diff_edge(edge: GraphEdge) -> DiffEdge:
    return DiffEdge(
        from_=edge.from_,
        to=edge.to,
        status=edge.status,
        source=edge.source,
        interface=edge.interface,
        agreement=edge.agreement,
    )


def _edge_props(edge: GraphEdge) -> tuple:
    return (edge.status, edge.source, edge.interface, edge.agreement)


def diff_plan_graphs(
    from_graph: PlanGraph | None,
    to_graph: PlanGraph | None,
) -> PlanDiff | None:
    """Compute the version diff between two plan-layer graphs.

    Returns ``None`` when either side is missing — there is nothing to diff
    against (a project without a snapshot, or a preview that produced no new
    plan). All lists are sorted for deterministic output (diff is idempotent).
    """
    if from_graph is None or to_graph is None:
        return None

    from_edges = {_edge_key(e): e for e in from_graph.edges}
    to_edges = {_edge_key(e): e for e in to_graph.edges}
    from_repos = {n.repository for n in from_graph.nodes}
    to_repos = {n.repository for n in to_graph.nodes}

    added_keys = sorted(set(to_edges) - set(from_edges))
    removed_keys = sorted(set(from_edges) - set(to_edges))
    shared_keys = sorted(set(from_edges) & set(to_edges))

    added_edges = [_to_diff_edge(to_edges[key]) for key in added_keys]
    removed_edges = [_to_diff_edge(from_edges[key]) for key in removed_keys]
    changed_edges = [
        EdgeChangeView(
            from_=key[0],
            to=key[1],
            old=_to_diff_edge(from_edges[key]),
            new=_to_diff_edge(to_edges[key]),
        )
        for key in shared_keys
        if _edge_props(from_edges[key]) != _edge_props(to_edges[key])
    ]

    added_repos = sorted(to_repos - from_repos)
    removed_repos = sorted(from_repos - to_repos)
    affected_repos = sorted(
        set(added_repos)
        | set(removed_repos)
        | {key[1] for key in added_keys}
        | {key[1] for key in removed_keys}
    )

    return PlanDiff(
        from_version=from_graph.plan_version,
        to_version=to_graph.plan_version,
        added_edges=added_edges,
        removed_edges=removed_edges,
        changed_edges=changed_edges,
        added_repos=added_repos,
        removed_repos=removed_repos,
        affected_repos=affected_repos,
    )
