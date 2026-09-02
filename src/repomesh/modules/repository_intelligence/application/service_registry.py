"""Service registry — the calibration layer between evidence and graph nodes.

Repository names, Maven artifactIds, npm package names, go module paths,
``spring.application.name`` values and Feign client names are all *aliases*
for the same logical service. The registry aggregates them into one
alias → repository lookup, so evidence from any mechanism (pom.xml, Feign
annotations, datasource config) can be resolved to the repository that owns
it.

It is a byproduct of an org scan: every scanned repository contributes its
authoritative name (platform metadata) plus any aliases its build and
runtime files declare. Build aliases land with the dep parsers (Phase 2),
call-declaration aliases with the Phase 3 parsers; the registry accepts
them from day one.
"""

from __future__ import annotations

from dataclasses import dataclass

from repomesh.modules.repository_intelligence.domain import RepositoryProfile


@dataclass(frozen=True, slots=True)
class ServiceAliases:
    """Every identifier that resolves to one repository.

    ``repo_name`` is authoritative (platform metadata, or the URL-path
    fallback of a single-repo scan). The remaining fields are self-declared
    aliases from build/runtime files, and stay empty when no such files were
    read.
    """

    repo_name: str
    build_ids: tuple[str, ...] = ()
    """Build-manifest identifiers: Maven artifactId, npm package name, go
    module path, PEP 508 distribution name (from ``auto_card.identities``)."""
    spring_names: tuple[str, ...] = ()
    feign_names: tuple[str, ...] = ()
    deploy_names: tuple[str, ...] = ()
    """Deployment-manifest identifiers: compose service names, k8s app
    labels, Service names (from ``auto_card.deploy_identities``, Phase 5)."""

    def iter_names(self) -> tuple[str, ...]:
        return (
            self.repo_name,
            *self.build_ids,
            *self.spring_names,
            *self.feign_names,
            *self.deploy_names,
        )


class ServiceRegistry:
    """Alias → repository-name lookup for a scanned organization.

    Resolution is case-insensitive. On collision the first registration wins:
    an org scan registers the authoritative platform name first, so a later
    self-declared alias can never hijack a repository's identity.
    """

    def __init__(self) -> None:
        self._by_alias: dict[str, str] = {}

    def register(self, aliases: ServiceAliases) -> None:
        """Register a repository under every name it answers to."""
        for name in aliases.iter_names():
            key = name.strip().lower()
            if key:
                self._by_alias.setdefault(key, aliases.repo_name)

    def resolve(self, alias: str) -> str | None:
        """Map *alias* to the owning repository name, if known."""
        return self._by_alias.get(alias.strip().lower())

    def __len__(self) -> int:
        return len(self._by_alias)

    def __contains__(self, alias: str) -> bool:
        return alias.strip().lower() in self._by_alias


def build_service_registry(profiles: list[RepositoryProfile]) -> ServiceRegistry:
    """Aggregate a scanned org's self-reported identifiers.

    Two passes, and the order is the contract: every authoritative
    ``repo_name`` is registered *before* any self-declared alias, so a
    build identity that happens to equal another repository's platform
    name can never hijack it (``setdefault`` keeps the first — the
    authoritative — registration). Call-declaration aliases
    (spring.application.name, Feign names) are wired in by the Phase 3
    parsers the same way.
    """
    registry = ServiceRegistry()

    # Pass 1: authoritative platform names.
    for profile in profiles:
        registry.register(ServiceAliases(repo_name=profile.name))

    # Pass 2: self-declared build identities (auto_card.identities, Phase 2)
    # and deploy identities (auto_card.deploy_identities, Phase 5).
    for profile in profiles:
        if profile.auto_card is not None and (
            profile.auto_card.identities or profile.auto_card.deploy_identities
        ):
            registry.register(
                ServiceAliases(
                    repo_name=profile.name,
                    build_ids=profile.auto_card.identities,
                    deploy_names=profile.auto_card.deploy_identities,
                )
            )
    return registry
