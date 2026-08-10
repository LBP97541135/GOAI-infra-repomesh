"""Dependency graph service — infrastructure-layer retrieval engine.

Builds a directed dependency graph from RepositoryProfile AutoCard data.
Provides deterministic structural queries (reverse deps, edges, topological
sort) that replace LLM guessing in confirmation and integration phases.

Design principles:
- Graph provides CANDIDATES and CONSTRAINTS, not final answers
- LLM still makes semantic decisions (whether a contract is needed, what it says)
- Falls back to MVP behaviour when graph data is unavailable (graph=None)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from repomesh.modules.repository_intelligence.domain import RepositoryProfile


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """A directed edge: consumer depends on producer.

    ``consumer`` calls ``producer``'s API.
    If producer changes, consumer may be affected.
    """

    producer: str
    consumer: str
    confidence: str  # "confirmed" | "possible"
    match_reason: str  # audit trail: "exact" | "substring:dep contains name"


@dataclass(frozen=True, slots=True)
class TopoResult:
    """Result of topological sort."""

    batches: list[list[str]]
    cyclic_repos: list[str] = field(default_factory=list)


class DependencyGraphService:
    """Build a dependency graph from RepositoryProfile AutoCard data.

    The graph is directed: an edge consumer → producer means
    "consumer depends on producer" (consumer calls producer's API).
    """

    def __init__(self, profiles: list[RepositoryProfile]) -> None:
        self._names: set[str] = {p.name for p in profiles}
        self._edges: list[GraphEdge] = self._build_edges(profiles)
        self._forward: dict[str, list[GraphEdge]] = self._index_forward()
        self._reverse: dict[str, list[GraphEdge]] = self._index_reverse()

    # ------------------------------------------------------------------
    # Public queries
    # ------------------------------------------------------------------

    def reverse_dependencies(self, repo_name: str) -> list[GraphEdge]:
        """Who depends on me? (impacts — callers of my API)."""
        return list(self._reverse.get(repo_name, []))

    def forward_dependencies(self, repo_name: str) -> list[GraphEdge]:
        """Whom do I depend on? (my depends_on list)."""
        return list(self._forward.get(repo_name, []))

    def edges_in(self, repos: list[str]) -> list[GraphEdge]:
        """All directed edges within the given repo set."""
        repo_set = set(repos)
        return [
            e for e in self._edges
            if e.producer in repo_set and e.consumer in repo_set
        ]

    def topological_batches(self, repos: list[str]) -> TopoResult:
        """Topologically sort repos into execution batches.

        Batch 1 = repos with no internal dependencies (safe to start first).
        Subsequent batches = repos whose dependencies are all in earlier batches.

        If a cycle is detected, cyclic repos are placed in the same batch
        and returned in ``cyclic_repos``.
        """
        repo_set = set(repos)
        relevant = [e for e in self._edges if e.producer in repo_set and e.consumer in repo_set]

        # Build adjacency: consumer → set(producers it depends on)
        deps: dict[str, set[str]] = {r: set() for r in repo_set}
        for edge in relevant:
            deps[edge.consumer].add(edge.producer)

        batches: list[list[str]] = []
        placed: set[str] = set()
        remaining = set(repo_set)

        while remaining:
            # Find all repos whose deps are fully placed
            ready = {r for r in remaining if deps[r] <= placed}

            if not ready:
                # Cycle detected — extract all remaining as cyclic
                cyclic = sorted(remaining)
                batches.append(cyclic)
                return TopoResult(batches=batches, cyclic_repos=cyclic)

            batch = sorted(ready)
            batches.append(batch)
            placed |= ready
            remaining -= ready

        return TopoResult(batches=batches)

    @property
    def edge_count(self) -> int:
        return len(self._edges)

    @property
    def confirmed_edge_count(self) -> int:
        return sum(1 for e in self._edges if e.confidence == "confirmed")

    # ------------------------------------------------------------------
    # Internal: graph construction
    # ------------------------------------------------------------------

    def _build_edges(
        self, profiles: list[RepositoryProfile]
    ) -> list[GraphEdge]:
        """Extract directed edges from AutoCard.deps via name matching.

        Matching strategy (from-wide):
        1. Exact match: dep string equals a repo name
        2. Substring match: repo name appears as substring in dep
        3. Suffix match: dep ends with repo name

        Confirmed = exact match only.
        Possible = substring or suffix match.
        """
        edges: list[GraphEdge] = []
        seen: set[tuple[str, str]] = set()

        for profile in profiles:
            if not profile.auto_card or not profile.auto_card.deps:
                continue

            for dep in profile.auto_card.deps:
                matched = self._match_dep_to_name(dep, profile.name)
                if matched is None:
                    continue

                key = (profile.name, matched[0])  # (consumer, producer_name)
                if key in seen:
                    continue
                seen.add(key)

                edges.append(matched[1])  # GraphEdge

        return edges

    def _match_dep_to_name(
        self, dep: str, consumer: str
    ) -> tuple[str, GraphEdge] | None:
        """Match a dep string to the best registered repo name.

        Collects all candidate matches, then picks the best one by:
        1. Match tier — exact > substring > suffix
        2. Name length — longer name is more specific (auth-service > auth)

        Returns (producer_name, GraphEdge) or None.
        """
        dep_lower = dep.lower().strip()
        candidates: list[tuple[int, int, str, GraphEdge]] = []

        for name in self._names:
            if name == consumer:
                continue
            name_lower = name.lower()

            # Tier 0: exact match (highest priority)
            if name_lower == dep_lower:
                candidates.append((
                    0, len(name), name,
                    GraphEdge(
                        producer=name, consumer=consumer,
                        confidence="confirmed",
                        match_reason=f"exact: dep == {name}",
                    ),
                ))
                continue

            # Tier 1: substring match (dep contains name)
            if name_lower in dep_lower:
                candidates.append((
                    1, len(name), name,
                    GraphEdge(
                        producer=name, consumer=consumer,
                        confidence="possible",
                        match_reason=f"substring: '{name}' found in '{dep}'",
                    ),
                ))
                continue

            # Tier 2: suffix match (dep ends with name)
            if dep_lower.endswith(name_lower):
                candidates.append((
                    2, len(name), name,
                    GraphEdge(
                        producer=name, consumer=consumer,
                        confidence="possible",
                        match_reason=f"suffix: dep ends with '{name}'",
                    ),
                ))

        if not candidates:
            return None

        # Sort: lowest tier number first, then longest name first
        candidates.sort(key=lambda c: (c[0], -c[1]))
        best = candidates[0]
        return best[2], best[3]

    def _index_forward(self) -> dict[str, list[GraphEdge]]:
        """consumer → [edges where this consumer depends on producers]."""
        idx: dict[str, list[GraphEdge]] = defaultdict(list)
        for edge in self._edges:
            idx[edge.consumer].append(edge)
        return dict(idx)

    def _index_reverse(self) -> dict[str, list[GraphEdge]]:
        """producer → [edges where consumers depend on this producer]."""
        idx: dict[str, list[GraphEdge]] = defaultdict(list)
        for edge in self._edges:
            idx[edge.producer].append(edge)
        return dict(idx)
