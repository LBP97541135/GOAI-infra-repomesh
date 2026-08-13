"""Unified dependency graph contracts (PR-1).

Single source of truth for repository dependency relationships, per the
Single-Graph design (docs/chenwenhui/统一依赖图SingleGraph架构方案-2026-08-13.md).

One graph entity, two layers:
- World layer (v0): scan-derived ``candidate`` edges, long-lived, not versioned.
- Plan layer (v1..vn): ``confirmed`` edges plus plan-time semantics, versioned
  into plan snapshots.

All consumers read projections (``execution_batches`` / ``contracts`` /
``task_dag``) computed from this single edge set. Any moment where
"read graph != projection columns" is a bug.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

EdgeStatus = Literal["candidate", "confirmed"]
EdgeSource = Literal["scan", "tm", "llm"]


class GraphNode(BaseModel):
    """Plan-layer node: a repository plus plan-time semantics."""

    repository: str
    instruction: str | None = None  # LLM-supplied task instruction
    tests: list[str] = Field(default_factory=list)


class GraphEdge(BaseModel):
    """A directed dependency edge.

    ``from_`` is the producer (depended upon); ``to`` is the consumer
    (dependent). If the producer changes, the consumer may be affected.

    Only ``confirmed`` edges participate in topology; ``candidate`` edges
    serve discovery. ``interface`` / ``agreement`` carry contract metadata
    and turn the edge into a contract edge.
    """

    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(
        ...,
        serialization_alias="from",
        validation_alias="from",
        description="Producer repository (depended upon).",
    )
    to: str = Field(..., description="Consumer repository (dependent).")
    status: EdgeStatus = "candidate"
    source: EdgeSource = "scan"  # audit: where the edge came from
    interface: str | None = None
    agreement: str | None = None


class ContractEdgeView(BaseModel):
    """Projection of a confirmed edge carrying contract metadata."""

    producer: str
    consumer: str
    interface: str
    agreement: str | None = None


class TaskDagNodeView(BaseModel):
    """Projection of a plan-layer node with its confirmed dependency list."""

    repository: str
    instruction: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)


class PlanGraph(BaseModel):
    """Versioned plan-layer graph persisted into plan snapshots.

    ``plan_version`` is monotonically increasing. ``edges`` hold all plan
    edges (confirmed by design; candidate edges are tolerated for early
    integration stages but never enter topology). Projection columns are
    materialised on construction from ``nodes`` + ``edges``.
    """

    plan_version: int = Field(ge=1)
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    # ↓ derived projections (materialised at write time; recomputed from
    #   nodes/edges at read time — equality is the consistency assertion)
    execution_batches: list[list[str]] = Field(default_factory=list)
    contracts: list[ContractEdgeView] = Field(default_factory=list)
    task_dag: list[TaskDagNodeView] = Field(default_factory=list)

    @model_validator(mode="after")
    def _materialise_projections(self) -> PlanGraph:
        # Completeness: nodes must cover both endpoints of every edge.
        node_names = {n.repository for n in self.nodes}
        missing = {name for e in self.edges for name in (e.from_, e.to)} - node_names
        if missing:
            self.nodes = [
                *self.nodes,
                *(GraphNode(repository=name) for name in sorted(missing)),
            ]
        self.edges = derive_edges(self.nodes, self.edges)
        self.execution_batches = project_batches(self.nodes, self.edges)
        self.contracts = project_contracts(self.edges)
        self.task_dag = project_task_dag(self.nodes, self.edges)
        return self


def derive_edges(nodes: list[GraphNode], edges: list[GraphEdge]) -> list[GraphEdge]:
    """Normalise an edge list.

    - Drops self-loops.
    - Deduplicates by (from_, to) keeping the first occurrence.
    - Raises on dangling edges: every endpoint must exist in ``nodes``
      (nodes-first caller responsibility; ``PlanGraph`` auto-completes).
    """
    node_names = {n.repository for n in nodes}
    seen: set[tuple[str, str]] = set()
    derived: list[GraphEdge] = []
    for edge in edges:
        if edge.from_ == edge.to:
            continue
        if edge.from_ not in node_names or edge.to not in node_names:
            raise ValueError(
                f"dangling edge {edge.from_} -> {edge.to}: endpoint missing from nodes"
            )
        key = (edge.from_, edge.to)
        if key in seen:
            continue
        seen.add(key)
        derived.append(edge)
    return derived


def project_batches(
    nodes: list[GraphNode], edges: list[GraphEdge]
) -> list[list[str]]:
    """Kahn-style topological sort over the plan node set.

    The node universe is ``nodes`` (every plan repo appears in exactly one
    batch); ordering uses **confirmed** edges only. Batch 1 = repos with no
    unresolved dependencies; each later batch holds repos whose dependencies
    are all in earlier batches. Cycles collapse into a single final batch
    (they cannot be ordered).
    """
    confirmed = [e for e in edges if e.status == "confirmed"]
    repos = {n.repository for n in nodes}
    deps: dict[str, set[str]] = {r: set() for r in repos}
    for edge in confirmed:
        if edge.from_ in deps and edge.to in deps:
            deps[edge.to].add(edge.from_)

    batches: list[list[str]] = []
    placed: set[str] = set()
    remaining = set(repos)
    while remaining:
        ready = {r for r in remaining if deps[r] <= placed}
        if not ready:
            # Cycle detected — remaining repos share one batch.
            batches.append(sorted(remaining))
            return batches
        batch = sorted(ready)
        batches.append(batch)
        placed |= ready
        remaining -= ready
    return batches


def project_contracts(edges: list[GraphEdge]) -> list[ContractEdgeView]:
    """Project contract metadata from confirmed edges that carry an interface."""
    views: list[ContractEdgeView] = []
    for edge in sorted(edges, key=lambda e: (e.from_, e.to)):
        if edge.status == "confirmed" and edge.interface:
            views.append(
                ContractEdgeView(
                    producer=edge.from_,
                    consumer=edge.to,
                    interface=edge.interface,
                    agreement=edge.agreement,
                )
            )
    return views


def project_task_dag(
    nodes: list[GraphNode], edges: list[GraphEdge]
) -> list[TaskDagNodeView]:
    """Project plan-layer nodes plus their confirmed dependency lists."""
    confirmed = [e for e in edges if e.status == "confirmed"]
    deps: dict[str, list[str]] = defaultdict(list)
    for edge in confirmed:
        deps[edge.to].append(edge.from_)

    views: list[TaskDagNodeView] = []
    for node in sorted(nodes, key=lambda n: n.repository):
        views.append(
            TaskDagNodeView(
                repository=node.repository,
                instruction=node.instruction,
                depends_on=sorted(deps.get(node.repository, [])),
                tests=list(node.tests),
            )
        )
    return views
