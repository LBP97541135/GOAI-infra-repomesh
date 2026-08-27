"""Tests for DependencyGraphService — the graph retrieval engine.

Tests cover:
- Edge construction (BUILD evidence resolution, external deps ignored)
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
    DepEvidence,
    RepositoryProfile,
)


def _profile(
    name: str,
    deps: tuple[str, ...] = (),
    apis: tuple[str, ...] = (),
    evidence: tuple[DepEvidence, ...] = (),
    deploy_identities: tuple[str, ...] = (),
) -> RepositoryProfile:
    # ``deps`` is a test convenience: it becomes BUILD evidence (mechanism ①),
    # exactly as scan_remote converts parsed build manifests today. The
    # legacy free-text ``deps`` → string-guess edge path is gone.
    build_evidence = tuple(
        DepEvidence(
            name=dep,
            mechanism="BUILD",
            confidence="confirmed",
        )
        for dep in deps
    )
    return RepositoryProfile(
        name=name,
        url=f"https://github.com/org/{name}",
        description=f"Service {name}",
        auto_card=AutoCard(
            top_dirs=("src",),
            deps=deps,
            dep_evidence=evidence + build_evidence,
            deploy_identities=deploy_identities,
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
# Event-based edges (Phase 1.2): dep_evidence → service registry
# ---------------------------------------------------------------------------


class TestDepEvidenceEdges:
    def test_evidence_resolves_exactly(self):
        """An evidence identifier naming a catalog repo → confirmed edge."""
        profiles = [
            _profile("ts-auth-service"),
            _profile(
                "ts-order-service",
                evidence=(
                    DepEvidence(
                        name="ts-auth-service",
                        mechanism="BUILD",
                        confidence="confirmed",
                    ),
                ),
            ),
        ]
        graph = DependencyGraphService(profiles)

        assert graph.edge_count == 1
        assert graph.confirmed_edge_count == 1
        edges = graph.forward_dependencies("ts-order-service")
        assert len(edges) == 1
        assert edges[0].producer == "ts-auth-service"
        assert edges[0].mechanism == "BUILD"
        assert "resolves to repo" in edges[0].match_reason

    def test_unresolved_evidence_produces_no_edge(self):
        """A public library / unknown identifier never fabricates an edge."""
        profiles = [
            _profile("ts-auth-service"),
            _profile(
                "ts-order-service",
                evidence=(
                    DepEvidence(
                        name="com.fasterxml.jackson.core:jackson-databind",
                        mechanism="BUILD",
                        confidence="confirmed",
                    ),
                ),
            ),
        ]
        graph = DependencyGraphService(profiles)

        assert graph.edge_count == 0

    def test_declared_evidence_is_not_topology(self):
        """SHARED_RESOURCE/DEPLOY evidence → declared, never confirmed."""
        profiles = [
            _profile("ts-payment-service"),
            _profile(
                "ts-billing-service",
                evidence=(
                    DepEvidence(
                        name="ts-payment-service",
                        mechanism="SHARED_RESOURCE",
                        confidence="declared",
                    ),
                ),
            ),
        ]
        graph = DependencyGraphService(profiles)

        assert graph.edge_count == 1
        assert graph.confirmed_edge_count == 0
        edges = graph.forward_dependencies("ts-billing-service")
        assert edges[0].confidence == "declared"
        assert edges[0].mechanism == "SHARED_RESOURCE"

    def test_evidence_resolution_is_case_insensitive(self):
        """Registry resolution ignores case in both directions."""
        profiles = [
            _profile("TS-Auth-Service"),
            _profile(
                "ts-order-service",
                evidence=(
                    DepEvidence(
                        name="ts-auth-service",
                        mechanism="BUILD",
                        confidence="confirmed",
                    ),
                ),
            ),
        ]
        graph = DependencyGraphService(profiles)

        assert graph.edge_count == 1
        assert graph.forward_dependencies("ts-order-service")[0].producer == (
            "TS-Auth-Service"
        )

    def test_evidence_self_loop_ignored(self):
        """Evidence naming the consumer's own repo must not self-loop."""
        profiles = [
            _profile(
                "ts-order-service",
                evidence=(
                    DepEvidence(
                        name="ts-order-service",
                        mechanism="BUILD",
                        confidence="confirmed",
                    ),
                ),
            ),
        ]
        graph = DependencyGraphService(profiles)

        assert graph.edge_count == 0

    def test_first_evidence_mechanism_wins_the_pair_slot(self):
        """Same (consumer, producer) from two evidence refs → one edge.

        The first mechanism processed claims the dedup slot; RUNTIME_CALL
        precedes the BUILD ref in ``dep_evidence`` order, so it wins.
        """
        profiles = [
            _profile("ts-auth-service"),
            _profile(
                "ts-order-service",
                deps=("ts-auth-service",),  # becomes BUILD evidence (after)
                evidence=(
                    DepEvidence(
                        name="ts-auth-service",
                        mechanism="RUNTIME_CALL",
                        confidence="confirmed",
                    ),
                ),
            ),
        ]
        graph = DependencyGraphService(profiles)

        assert graph.edge_count == 1
        edges = graph.forward_dependencies("ts-order-service")
        assert len(edges) == 1
        assert edges[0].mechanism == "RUNTIME_CALL"
        assert "RUNTIME_CALL" in edges[0].match_reason

    def test_deps_strings_no_longer_build_edges(self):
        """Free-text ``deps`` without evidence never fabricate an edge.

        The legacy substring/suffix guess channel was removed: a card that
        only carries raw strings (no resolved evidence) contributes nothing
        to the graph, even for a string that equals a catalog repo name.
        """
        profiles = [
            RepositoryProfile(
                name="ts-auth-service",
                url="https://github.com/org/ts-auth-service",
                description="Service ts-auth-service",
                auto_card=AutoCard(
                    top_dirs=("src",),
                    deps=("ts-auth-service",),
                    dep_evidence=(),
                    recent_commits=(),
                    exposed_apis=(),
                ),
            ),
            RepositoryProfile(
                name="ts-order-service",
                url="https://github.com/org/ts-order-service",
                description="Service ts-order-service",
                auto_card=AutoCard(
                    top_dirs=("src",),
                    deps=("ts-auth-service",),
                    dep_evidence=(),
                    recent_commits=(),
                    exposed_apis=(),
                ),
            ),
        ]
        graph = DependencyGraphService(profiles)

        assert graph.edge_count == 0


# ---------------------------------------------------------------------------
# Shared-resource edges (mechanism ③): resource identifier ↔ resource identifier
# ---------------------------------------------------------------------------


def _shared_profile(name: str, resource: str) -> RepositoryProfile:
    """A repository declaring one shared resource."""
    return _profile(
        name,
        evidence=(
            DepEvidence(
                name=resource,
                mechanism="SHARED_RESOURCE",
                confidence="declared",
            ),
        ),
    )


class TestSharedResourceEdges:
    def test_two_repos_sharing_a_database_get_bidirectional_edges(self):
        """Shared state couples both directions: declared, never topology."""
        profiles = [
            _shared_profile("ts-order-service", "DATABASE:orders-db"),
            _shared_profile("ts-payment-service", "DATABASE:orders-db"),
        ]
        graph = DependencyGraphService(profiles)

        assert graph.edge_count == 2
        assert graph.confirmed_edge_count == 0
        by_consumer = {
            e.consumer: e
            for e in graph.edges_in(["ts-order-service", "ts-payment-service"])
        }
        assert by_consumer["ts-order-service"].producer == "ts-payment-service"
        assert by_consumer["ts-payment-service"].producer == "ts-order-service"
        assert all(e.mechanism == "SHARED_RESOURCE" for e in by_consumer.values())
        assert all(e.confidence == "declared" for e in by_consumer.values())
        assert "share resource" in by_consumer["ts-order-service"].match_reason

    def test_three_repos_sharing_one_resource_form_a_complete_graph(self):
        profiles = [
            _shared_profile("ts-a-service", "REDIS:cache-01:6379"),
            _shared_profile("ts-b-service", "REDIS:cache-01:6379"),
            _shared_profile("ts-c-service", "REDIS:cache-01:6379"),
        ]
        graph = DependencyGraphService(profiles)

        # 3 repos × 2 directions = 6 bidirectional shared edges.
        assert graph.edge_count == 6

    def test_single_owner_of_a_resource_has_no_edge(self):
        """One repository alone on a resource shares it with nobody."""
        profiles = [_shared_profile("ts-order-service", "DATABASE:orders-db")]
        graph = DependencyGraphService(profiles)

        assert graph.edge_count == 0

    def test_same_name_different_resource_kinds_do_not_match(self):
        """DATABASE:orders and BUCKET:orders are different resources."""
        profiles = [
            _shared_profile("ts-order-service", "DATABASE:orders"),
            _shared_profile("ts-assets-service", "BUCKET:orders"),
        ]
        graph = DependencyGraphService(profiles)

        assert graph.edge_count == 0

    def test_shared_resource_matching_is_case_insensitive(self):
        profiles = [
            _shared_profile("ts-order-service", "DATABASE:Orders-Db"),
            _shared_profile("ts-payment-service", "DATABASE:orders-db"),
        ]
        graph = DependencyGraphService(profiles)

        assert graph.edge_count == 2

    def test_confirmed_call_edge_wins_the_slot_over_shared(self):
        """a→b via RUNTIME_CALL stays confirmed; the shared b→a is declared."""
        profiles = [
            _profile("ts-payment-service"),
            _profile(
                "ts-order-service",
                evidence=(
                    DepEvidence(
                        name="ts-payment-service",
                        mechanism="RUNTIME_CALL",
                        confidence="confirmed",
                    ),
                    DepEvidence(
                        name="DATABASE:orders-db",
                        mechanism="SHARED_RESOURCE",
                        confidence="declared",
                    ),
                ),
            ),
            _shared_profile("ts-payment-service", "DATABASE:orders-db"),
        ]
        graph = DependencyGraphService(profiles)

        assert graph.edge_count == 2
        order_to_payment = graph.forward_dependencies("ts-order-service")
        payment_to_order = graph.forward_dependencies("ts-payment-service")
        assert order_to_payment[0].mechanism == "RUNTIME_CALL"
        assert order_to_payment[0].confidence == "confirmed"
        assert payment_to_order[0].mechanism == "SHARED_RESOURCE"
        assert payment_to_order[0].confidence == "declared"


def _deploy_profile(
    name: str,
    targets: tuple[str, ...] = (),
    deploy_identities: tuple[str, ...] = (),
) -> RepositoryProfile:
    """A repository with DEPLOY evidence and/or deploy identities."""
    return _profile(
        name,
        evidence=tuple(
            DepEvidence(
                name=target,
                mechanism="DEPLOY",
                confidence="declared",
            )
            for target in targets
        ),
        deploy_identities=deploy_identities,
    )


class TestDeployEdges:
    def test_deploy_evidence_resolves_to_a_declared_edge(self):
        """compose depends_on / selector refs resolve like ①②, but declared."""
        profiles = [
            _profile("ts-payment-service"),
            _deploy_profile(
                "ts-checkout-service",
                targets=("ts-payment-service",),
            ),
        ]
        graph = DependencyGraphService(profiles)

        assert graph.edge_count == 1
        assert graph.confirmed_edge_count == 0
        edges = graph.forward_dependencies("ts-checkout-service")
        assert edges[0].producer == "ts-payment-service"
        assert edges[0].mechanism == "DEPLOY"
        assert edges[0].confidence == "declared"
        assert "resolves to repo" in edges[0].match_reason

    def test_deploy_identity_enables_resolution_when_names_differ(self):
        """depends_on names a service, not the repo — deploy aliases bridge."""
        profiles = [
            _deploy_profile(
                "ts-payment-service",
                deploy_identities=("payment-svc",),
            ),
            _deploy_profile(
                "ts-checkout-service",
                targets=("payment-svc",),
            ),
        ]
        graph = DependencyGraphService(profiles)

        assert graph.edge_count == 1
        edge = graph.forward_dependencies("ts-checkout-service")[0]
        assert edge.producer == "ts-payment-service"
        assert edge.mechanism == "DEPLOY"

    def test_unresolved_deploy_reference_produces_no_edge(self):
        """depends_on an external/infra service names no catalog repo."""
        profiles = [
            _deploy_profile(
                "ts-checkout-service",
                targets=("payment-gateway",),
            ),
        ]
        graph = DependencyGraphService(profiles)

        assert graph.edge_count == 0

    def test_deploy_identity_cannot_hijack_another_repos_platform_name(self):
        """Authoritative repo names register before self-declared aliases."""
        profiles = [
            _deploy_profile(
                "ts-a-service",
                targets=("ts-b-service",),
                deploy_identities=("ts-b-service",),
            ),
            _profile("ts-b-service"),
        ]
        graph = DependencyGraphService(profiles)

        # ts-a's self-declared alias "ts-b-service" resolves to the real
        # ts-b-service (authoritative wins), never back to ts-a itself.
        assert graph.edge_count == 1
        edge = graph.forward_dependencies("ts-a-service")[0]
        assert edge.producer == "ts-b-service"
        assert edge.consumer == "ts-a-service"

    def test_declared_deploy_edge_does_not_reorder_topology(self):
        """mechanism ④ is a discovery hint — never participates in topology."""
        profiles = [
            _profile("ts-c-service"),
            _profile("ts-b-service", deps=("ts-c-service",)),
            _deploy_profile(
                "ts-a-service",
                targets=("ts-z-service",),
            ),
            _profile("ts-z-service"),
        ]
        graph = DependencyGraphService(profiles)

        topo = graph.topological_batches(
            ["ts-a-service", "ts-b-service", "ts-c-service", "ts-z-service"]
        )
        # Only the confirmed b→c edge constrains ordering. a has no
        # confirmed dependency (its a→z edge is declared), so it belongs in
        # batch 1 alongside c and z — the declared edge must not reorder the
        # deployment plan.
        assert topo.batches == [
            ["ts-a-service", "ts-c-service", "ts-z-service"],
            ["ts-b-service"],
        ]
        assert topo.cyclic_repos == []


def _source_profile(name: str, refs: tuple[str, ...] = ()) -> RepositoryProfile:
    """A repository with SOURCE evidence (mechanism ⑤, confirmed)."""
    return _profile(
        name,
        evidence=tuple(
            DepEvidence(
                name=ref,
                mechanism="SOURCE",
                confidence="confirmed",
            )
            for ref in refs
        ),
    )


class TestSourceEdges:
    def test_submodule_url_resolves_to_a_confirmed_edge(self):
        """A .gitmodules URL's repo name resolves like ①② — but confirmed."""
        profiles = [
            _profile("ts-common"),
            _source_profile(
                "ts-app",
                refs=("ts-common",),
            ),
        ]
        graph = DependencyGraphService(profiles)

        assert graph.edge_count == 1
        assert graph.confirmed_edge_count == 1
        edges = graph.forward_dependencies("ts-app")
        assert edges[0].producer == "ts-common"
        assert edges[0].mechanism == "SOURCE"
        assert edges[0].confidence == "confirmed"
        assert "resolves to repo" in edges[0].match_reason

    def test_go_work_outside_use_resolves_to_confirmed_edge(self):
        """``use ../ts-common`` in go.work is a confirmed source reference."""
        profiles = [
            _profile("ts-common"),
            _source_profile("ts-app", refs=("ts-common",)),
        ]
        graph = DependencyGraphService(profiles)

        assert graph.edge_count == 1
        assert graph.confirmed_edge_count == 1

    def test_unresolved_source_ref_produces_no_edge(self):
        """A submodule pointing outside the org creates no edge."""
        profiles = [
            _source_profile("ts-app", refs=("nginx",)),
        ]
        graph = DependencyGraphService(profiles)

        assert graph.edge_count == 0

    def test_source_ref_participates_in_topology(self):
        """confirmed SOURCE edges constrain batches like BUILD/RUNTIME_CALL."""
        profiles = [
            _profile("ts-c-service"),
            _profile("ts-b-service", deps=("ts-c-service",)),
            _source_profile("ts-a-service", refs=("ts-b-service",)),
        ]
        graph = DependencyGraphService(profiles)

        topo = graph.topological_batches(
            ["ts-a-service", "ts-b-service", "ts-c-service"]
        )
        assert topo.batches == [
            ["ts-c-service"],
            ["ts-b-service"],
            ["ts-a-service"],
        ]
        assert topo.cyclic_repos == []


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
        assert "resolves to repo" in edges[0].match_reason
