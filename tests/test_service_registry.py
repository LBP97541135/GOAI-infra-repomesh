"""Tests for ServiceRegistry — the alias → repository calibration layer."""

from repomesh.modules.repository_intelligence.application.service_registry import (
    ServiceAliases,
    ServiceRegistry,
    build_service_registry,
)
from repomesh.modules.repository_intelligence.domain import AutoCard, RepositoryProfile


class TestServiceRegistry:
    def test_resolves_repo_name_and_aliases_case_insensitively(self) -> None:
        registry = ServiceRegistry()
        registry.register(
            ServiceAliases(
                repo_name="ts-order-service",
                build_ids=("order-service", "order-service-api"),
            )
        )

        assert registry.resolve("ts-order-service") == "ts-order-service"
        assert registry.resolve("order-service") == "ts-order-service"
        assert registry.resolve("ORDER-SERVICE-API") == "ts-order-service"
        assert registry.resolve("unknown-service") is None

    def test_first_registration_wins_on_collision(self) -> None:
        """An authoritative platform name must not be hijacked by an alias."""
        registry = ServiceRegistry()
        registry.register(
            ServiceAliases(repo_name="ts-order-service", build_ids=("order",))
        )
        # Another repo claiming the same alias loses.
        registry.register(
            ServiceAliases(repo_name="another-service", build_ids=("order",))
        )

        assert registry.resolve("order") == "ts-order-service"

    def test_blank_names_are_ignored(self) -> None:
        registry = ServiceRegistry()
        registry.register(ServiceAliases(repo_name="svc", spring_names=("  ", "")))
        assert len(registry) == 1
        assert "svc" in registry

    def test_build_service_registry_from_profiles(self) -> None:
        profiles = [
            RepositoryProfile(name="ts-order-service", url="..."),
            RepositoryProfile(name="ts-payment-service", url="..."),
        ]
        registry = build_service_registry(profiles)

        assert len(registry) == 2
        assert registry.resolve("ts-order-service") == "ts-order-service"
        assert registry.resolve("ts-payment-service") == "ts-payment-service"

    def test_build_registry_registers_self_declared_identities(self) -> None:
        """auto_card.identities land in the registry as build aliases."""
        profiles = [
            RepositoryProfile(
                name="ts-order-service",
                url="...",
                auto_card=AutoCard(
                    top_dirs=("src",),
                    deps=(),
                    recent_commits=(),
                    exposed_apis=(),
                    low_signal=False,
                    identities=("com.example:order-service", "order-service"),
                ),
            ),
            RepositoryProfile(name="ts-payment-service", url="..."),
        ]
        registry = build_service_registry(profiles)

        assert registry.resolve("com.example:order-service") == "ts-order-service"
        assert registry.resolve("order-service") == "ts-order-service"
        # The authoritative name is not hijacked by the alias.
        assert registry.resolve("ts-order-service") == "ts-order-service"
        assert registry.resolve("ts-payment-service") == "ts-payment-service"

    def test_build_identity_cannot_hijack_another_repos_platform_name(self) -> None:
        """Authoritative repo names register before self-declared aliases."""
        profiles = [
            RepositoryProfile(
                name="ts-checkout-service",
                url="...",
                auto_card=AutoCard(
                    top_dirs=("src",),
                    deps=(),
                    recent_commits=(),
                    exposed_apis=(),
                    low_signal=False,
                    # This repo wrongly declares another repo's platform name.
                    identities=("ts-payment-service",),
                ),
            ),
            RepositoryProfile(name="ts-payment-service", url="..."),
        ]
        registry = build_service_registry(profiles)

        # ts-payment-service is the platform name of the second repo; the
        # first repo's self-declared alias must not steal it.
        assert registry.resolve("ts-payment-service") == "ts-payment-service"

    def test_build_registry_registers_deploy_identities(self) -> None:
        """auto_card.deploy_identities land in the registry as aliases."""
        profiles = [
            RepositoryProfile(
                name="ts-order-service",
                url="...",
                auto_card=AutoCard(
                    top_dirs=("src",),
                    deps=(),
                    recent_commits=(),
                    exposed_apis=(),
                    low_signal=False,
                    deploy_identities=("order-svc", "order-frontend"),
                ),
            ),
            RepositoryProfile(name="ts-payment-service", url="..."),
        ]
        registry = build_service_registry(profiles)

        assert registry.resolve("order-svc") == "ts-order-service"
        assert registry.resolve("ORDER-FRONTEND") == "ts-order-service"
        # The authoritative name is not hijacked by the alias.
        assert registry.resolve("ts-order-service") == "ts-order-service"
        assert registry.resolve("ts-payment-service") == "ts-payment-service"

    def test_deploy_identity_cannot_hijack_another_repos_platform_name(self) -> None:
        """Authoritative repo names register before deploy aliases too."""
        profiles = [
            RepositoryProfile(
                name="ts-checkout-service",
                url="...",
                auto_card=AutoCard(
                    top_dirs=("src",),
                    deps=(),
                    recent_commits=(),
                    exposed_apis=(),
                    low_signal=False,
                    # A compose file declaring another repo's platform name.
                    deploy_identities=("ts-payment-service",),
                ),
            ),
            RepositoryProfile(name="ts-payment-service", url="..."),
        ]
        registry = build_service_registry(profiles)

        assert registry.resolve("ts-payment-service") == "ts-payment-service"
