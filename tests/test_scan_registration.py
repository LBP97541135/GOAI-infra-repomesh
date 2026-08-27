"""Tests for register_scanned_profiles — scan-status-aware catalog writes."""

from repomesh.modules.repository_intelligence.application import (
    register_scanned_profiles,
)
from repomesh.modules.repository_intelligence.domain import AutoCard, RepositoryProfile
from repomesh.modules.repository_intelligence.infrastructure import (
    InMemoryRepositoryCatalog,
)


def _profile(name: str, *, scan_status: str = "ok") -> RepositoryProfile:
    return RepositoryProfile(
        name=name,
        url=f"https://gitlab.example.com/team/{name}",
        auto_card=None if scan_status == "failed" else AutoCard(deps=("x",)),
        scan_status=scan_status,  # type: ignore[arg-type]
    )


class TestRegisterScannedProfiles:
    async def _run(self, profiles):
        catalog = InMemoryRepositoryCatalog()
        return await register_scanned_profiles(profiles, catalog), catalog

    async def test_failed_scan_never_registered(self):
        """A profile whose scan failed must not enter the catalog."""
        registration, catalog = await self._run([
            _profile("order-service"),
            _profile("broken-service", scan_status="failed"),
        ])

        assert registration.failed == 1
        assert {p.name for p in registration.registered} == {"order-service"}
        names = {p.name for p in await catalog.list()}
        assert names == {"order-service"}

    async def test_duplicate_name_skipped_not_failed(self):
        """An already-registered name is a skip, not a failure."""
        first, catalog = await self._run([_profile("order-service")])
        assert {p.name for p in first.registered} == {"order-service"}
        assert first.skipped == 0

        second = await register_scanned_profiles(
            [_profile("order-service")], catalog
        )
        assert second.skipped == 1
        assert second.failed == 0

    async def test_all_failed_nothing_registered(self):
        registration, catalog = await self._run([
            _profile("a", scan_status="failed"),
            _profile("b", scan_status="failed"),
        ])

        assert registration.failed == 2
        assert await catalog.list() == []

    async def test_ok_profiles_registered(self):
        registration, catalog = await self._run([
            _profile("a"),
            _profile("b"),
        ])

        assert {p.name for p in registration.registered} == {"a", "b"}
        names = {p.name for p in await catalog.list()}
        assert names == {"a", "b"}
