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
from itertools import permutations

from repomesh.modules.repository_intelligence.application.service_registry import (
    ServiceRegistry,
    build_service_registry,
)
from repomesh.modules.repository_intelligence.domain import (
    Mechanism,
    RepositoryProfile,
)


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """A directed edge: consumer depends on producer.

    ``consumer`` calls ``producer``'s API.
    If producer changes, consumer may be affected.

    ``confidence``:
      - ``confirmed`` — hard evidence (mechanisms ① ② ⑤); the only class
        that participates in topological ordering.
      - ``declared`` — self-declared configuration (mechanisms ③ ④); a
        discovery hint, never topology.

    Every edge is evidence-backed: the legacy free-text ``deps`` string
    guessing (old substring/suffix matching) was removed — a string that
    does not resolve through the service registry never fabricates an edge.
    ``mechanism`` — which evidence mechanism proved the edge (audit trail).
    ``match_reason`` — human-readable audit trail.
    """

    producer: str
    consumer: str
    confidence: str  # "confirmed" | "declared"
    mechanism: Mechanism = "SOURCE"
    match_reason: str = ""


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

    def __init__(
        self,
        profiles: list[RepositoryProfile],
        registry: ServiceRegistry | None = None,
    ) -> None:
        self._registry = registry or build_service_registry(profiles)
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

        Only ``confirmed`` edges constrain ordering: ``declared`` edges
        (mechanisms ③ ④) are discovery hints and never topology
        (:class:`GraphEdge`). A shared database or a compose ``depends_on``
        must not reorder a deployment plan.

        Batch 1 = repos with no internal dependencies (safe to start first).
        Subsequent batches = repos whose dependencies are all in earlier batches.

        If a cycle is detected, cyclic repos are placed in the same batch
        and returned in ``cyclic_repos``.
        """
        repo_set = set(repos)
        relevant = [
            e for e in self._edges
            if e.confidence == "confirmed"
            and e.producer in repo_set
            and e.consumer in repo_set
        ]

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
        """Extract directed edges from AutoCard dependency evidence.

        Two paths, both producing :class:`GraphEdge` with ``mechanism`` +
        ``confidence``:

        1. **Event-based (preferred)** — ``auto_card.dep_evidence``: each ref
           names an identifier the service registry resolves exactly against
           the catalog (repo name today; artifactId / spring.application.name
           / Feign name / deploy service names once the Phase 2/3/5 parsers
           fill the registry). No string guessing, so a public library can
           never fabricate an edge (problem 5.2). BUILD, RUNTIME_CALL and
           DEPLOY evidence all resolve here; DEPLOY contributes its
           ``declared`` confidence as-is.
        2. **Shared-resource edges** — ``SHARED_RESOURCE`` evidence (mechanism
           ③) matches *resource identifier to resource identifier*, never
           through the service registry: any two repositories declaring the
           same database/Redis/MQ/bucket share it, so they get a bidirectional
           ``declared`` edge (a discovery hint — shared state, not a call).

        Edges are deduplicated per (consumer, producer); the event path wins
        the slot when both paths would produce the same pair.
        """
        edges: list[GraphEdge] = []
        seen: set[tuple[str, str]] = set()

        for profile in profiles:
            if not profile.auto_card:
                continue
            card = profile.auto_card
            if not card.dep_evidence:
                continue

            # Event-based edges: exact resolution via the service registry.
            for ref in card.dep_evidence:
                producer = self._resolve_evidence(ref.name, profile.name)
                if producer is None:
                    continue
                key = (profile.name, producer)
                if key in seen:
                    continue
                seen.add(key)
                edges.append(
                    GraphEdge(
                        producer=producer,
                        consumer=profile.name,
                        confidence=ref.confidence,
                        mechanism=ref.mechanism,
                        match_reason=(
                            f"{ref.mechanism}: '{ref.name}' resolves to repo "
                            f"'{producer}'"
                        ),
                    )
                )

        # Shared-resource edges: the identifier is a *resource*, so the
        # match is resource-to-resource, not name-to-repository. Bidirectional
        # and declared: shared state is a discovery hint, never topology.
        for resource, repos in self._shared_resource_groups(profiles).items():
            for consumer, producer in permutations(repos, 2):
                key = (consumer, producer)
                if key in seen:
                    continue
                seen.add(key)
                edges.append(
                    GraphEdge(
                        producer=producer,
                        consumer=consumer,
                        confidence="declared",
                        mechanism="SHARED_RESOURCE",
                        match_reason=(
                            f"SHARED_RESOURCE: '{consumer}' and '{producer}' "
                            f"share resource '{resource}'"
                        ),
                    )
                )

        return edges

    def _shared_resource_groups(
        self, profiles: list[RepositoryProfile]
    ) -> dict[str, list[str]]:
        """Group repositories by the shared resources they declare.

        Returns ``{resource_identifier: [repo_a, repo_b, …]}`` for resources
        declared by at least two repositories. Identifiers are compared
        case-insensitively (``DATABASE:Orders-Db`` == ``DATABASE:orders-db``);
        the returned key is lower-cased for the audit trail.
        """

        groups: dict[str, set[str]] = defaultdict(set)
        for profile in profiles:
            if profile.auto_card is None:
                continue
            for evidence in profile.auto_card.dep_evidence:
                if evidence.mechanism != "SHARED_RESOURCE":
                    continue
                groups[evidence.name.lower()].add(profile.name)
        return {
            resource: sorted(repos)
            for resource, repos in groups.items()
            if len(repos) >= 2
        }

    def _resolve_evidence(self, identifier: str, consumer: str) -> str | None:
        """Resolve an evidence identifier to a catalog repository.

        Delegates to the service registry, which resolves exactly (no
        substring guessing) and case-insensitively. Returns ``None`` for
        identifiers that name no catalog repository — a public library, a
        repo outside the scanned org — so no edge is built.
        """
        producer = self._registry.resolve(identifier)
        if producer is None or producer == consumer:
            return None
        return producer

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
