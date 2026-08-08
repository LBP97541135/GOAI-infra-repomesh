"""Tests for DependencyGraphService — the graph retrieval engine.

Tests cover:
- Edge construction (exact, substring, suffix matching)
- Forward / reverse dependency queries
- edges_in (scoped edge query)
- Topological batching (linear, parallel, cycle detection)
- Empty / low-signal handling
"""

from __future__ import annotations

from repomesh.modules.repository_intelligence.application.dependency_graph import (
    DependencyGraphService,
)
from repomesh.modules.repository_intelligence.domain import (
    AutoCard,
    RepositoryProfile,
)


def _profile(
    name: str,
    deps: tuple[str, ...] = (),
    apis: tuple[str, ...] = (),
) -> RepositoryProfile:
    return RepositoryProfile(
        name=name,
        url=f"https://github.com/org/{name}",
        description=f"Service {name}",
        auto_card=AutoCard(
            top_dirs=("src",),
            deps=deps,
            recent_commits=(),
            exposed_apis=apis,
        ),
    )


# ---------------------------------------------------------------------------
# Edge construction
# ---------------------------------------------------------------------------


class TestEdgeConstruction:
    def test_exact_match(self):
        """Dep string exactly equals a repo name → confirmed edge."""
        profiles = [
            _profile("ts-auth-service"),
            _profile("ts-order-service", deps=("ts-auth-service",)),
        ]
        graph = DependencyGraphService(profiles)

        assert graph.edge_count == 1
        assert graph.confirmed_edge_count == 1

    def test_substring_match(self):
        """Repo name appears inside dep string → possible edge."""
        profiles = [
            _profile("ts-auth-service"),
            _profile("ts-gateway", deps=("github.com/org/ts-auth-service",)),
        ]
        graph = DependencyGraphService(profiles)

        assert graph.edge_count == 1
        assert graph.confirmed_edge_count == 0
        edges = graph.forward_dependencies("ts-gateway")
        assert len(edges) == 1
        assert edges[0].confidence == "possible"

    def test_external_dep_ignored(self):
        """Deps that don't match any repo name should be ignored."""
        profiles = [
            _profile("ts-auth-service"),
            _profile("ts-order-service", deps=("redis-driver", "express", "ts-auth-service")),
        ]
        graph = DependencyGraphService(profiles)

        assert graph.edge_count == 1  # only ts-auth-service matched

    def test_self_loop_ignored(self):
        """A repo listing itself in deps should not create a self-loop."""
        profiles = [
            _profile("ts-order-service", deps=("ts-order-service",)),
        ]
        graph = DependencyGraphService(profiles)

        assert graph.edge_count == 0

    def test_duplicate_deps_deduplicated(self):
        """Same dep appearing multiple times creates only one edge."""
        profiles = [
            _profile("ts-auth-service"),
            _profile("ts-order-service", deps=("ts-auth-service", "ts-auth-service")),
        ]
        graph = DependencyGraphService(profiles)

        assert graph.edge_count == 1


# ---------------------------------------------------------------------------
# Forward / reverse queries
# ---------------------------------------------------------------------------


class TestDependencyQueries:
    def test_forward_dependencies(self):
        """forward_dependencies returns whom I depend on."""
        profiles = [
            _profile("ts-auth-service"),
            _profile("ts-notification-service"),
            _profile("ts-order-service", deps=("ts-auth-service", "ts-notification-service")),
        ]
        graph = DependencyGraphService(profiles)

        fwd = graph.forward_dependencies("ts-order-service")
        producers = {e.producer for e in fwd}
        assert producers == {"ts-auth-service", "ts-notification-service"}

    def test_reverse_dependencies(self):
        """reverse_dependencies returns who depends on me."""
        profiles = [
            _profile("ts-auth-service"),
            _profile("ts-order-service", deps=("ts-auth-service",)),
            _profile("ts-notification-service", deps=("ts-auth-service",)),
        ]
        graph = DependencyGraphService(profiles)

        rev = graph.reverse_dependencies("ts-auth-service")
        consumers = {e.consumer for e in rev}
        assert consumers == {"ts-order-service", "ts-notification-service"}

    def test_no_dependencies(self):
        """Repo with no matching deps returns empty lists."""
        profiles = [
            _profile("ts-isolated"),
        ]
        graph = DependencyGraphService(profiles)

        assert graph.forward_dependencies("ts-isolated") == []
        assert graph.reverse_dependencies("ts-isolated") == []


# ---------------------------------------------------------------------------
# edges_in
# ---------------------------------------------------------------------------


class TestEdgesIn:
    def test_scoped_edges(self):
        """edges_in only returns edges within the given repo set."""
        profiles = [
            _profile("ts-auth-service"),
            _profile("ts-order-service", deps=("ts-auth-service",)),
            _profile("ts-external-service"),
            _profile("ts-billing-service", deps=("ts-external-service",)),
        ]
        graph = DependencyGraphService(profiles)

        # Only include auth and order — billing→external should be excluded
        edges = graph.edges_in(["ts-auth-service", "ts-order-service"])
        assert len(edges) == 1
        assert edges[0].producer == "ts-auth-service"
        assert edges[0].consumer == "ts-order-service"

    def test_empty_repos(self):
        profiles = [_profile("ts-auth-service")]
        graph = DependencyGraphService(profiles)
        assert graph.edges_in([]) == []


# ---------------------------------------------------------------------------
# Topological batches
# ---------------------------------------------------------------------------


class TestTopologicalBatches:
    def test_linear_chain(self):
        """A → B → C produces three sequential batches."""
        profiles = [
            _profile("c-service"),
            _profile("b-service", deps=("c-service",)),
            _profile("a-service", deps=("b-service",)),
        ]
        graph = DependencyGraphService(profiles)

        topo = graph.topological_batches(["a-service", "b-service", "c-service"])
        assert len(topo.batches) == 3
        assert topo.batches[0] == ["c-service"]
        assert topo.batches[1] == ["b-service"]
        assert topo.batches[2] == ["a-service"]
        assert topo.cyclic_repos == []

    def test_parallel_repos(self):
        """Two repos with no deps between them → same batch."""
        profiles = [
            _profile("ts-auth-service"),
            _profile("ts-order-service", deps=("ts-auth-service",)),
            _profile("ts-billing-service", deps=("ts-auth-service",)),
        ]
        graph = DependencyGraphService(profiles)

        topo = graph.topological_batches([
            "ts-auth-service", "ts-order-service", "ts-billing-service",
        ])
        assert len(topo.batches) == 2
        assert topo.batches[0] == ["ts-auth-service"]
        # order + billing are in the same batch (sorted alphabetically)
        assert topo.batches[1] == ["ts-billing-service", "ts-order-service"]

    def test_cycle_detection(self):
        """A depends on B, B depends on A → cycle detected."""
        profiles = [
            _profile("a-service", deps=("b-service",)),
            _profile("b-service", deps=("a-service",)),
        ]
        graph = DependencyGraphService(profiles)

        topo = graph.topological_batches(["a-service", "b-service"])
        assert len(topo.cyclic_repos) == 2
        assert set(topo.cyclic_repos) == {"a-service", "b-service"}
        # Cyclic repos should be in a single batch
        assert len(topo.batches) == 1

    def test_no_edges_single_batch(self):
        """Repos with no edges between them → all in one batch."""
        profiles = [
            _profile("ts-a"),
            _profile("ts-b"),
            _profile("ts-c"),
        ]
        graph = DependencyGraphService(profiles)

        topo = graph.topological_batches(["ts-a", "ts-b", "ts-c"])
        assert len(topo.batches) == 1
        assert set(topo.batches[0]) == {"ts-a", "ts-b", "ts-c"}


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_profiles(self):
        graph = DependencyGraphService([])
        assert graph.edge_count == 0
        assert graph.forward_dependencies("anything") == []

    def test_low_signal_profiles(self):
        """Profiles without AutoCard are skipped."""
        profiles = [
            RepositoryProfile(
                name="ts-service",
                url="https://github.com/org/ts-service",
                auto_card=None,
            ),
        ]
        graph = DependencyGraphService(profiles)
        assert graph.edge_count == 0

    def test_match_reason_audit_trail(self):
        """Every edge has a human-readable match_reason."""
        profiles = [
            _profile("ts-auth-service"),
            _profile("ts-order-service", deps=("ts-auth-service",)),
        ]
        graph = DependencyGraphService(profiles)

        edges = graph.forward_dependencies("ts-order-service")
        assert len(edges) == 1
        assert "exact" in edges[0].match_reason
