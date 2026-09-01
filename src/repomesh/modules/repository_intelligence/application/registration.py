import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import UUID

from repomesh.modules.capability_management.contracts import (
    DEFAULT_TEAM_PROFILE,
    TEAM_CAPABILITY_PROFILES,
)
from repomesh.modules.repository_intelligence.domain import RepositoryProfile
from repomesh.modules.repository_intelligence.ports import RepositoryCatalog
from repomesh.shared.domain import new_id
from repomesh.shared.events import ActorType, EventEnvelope

_logger = logging.getLogger(__name__)


class RegisterRepository:
    def __init__(self, catalog: RepositoryCatalog) -> None:
        self._catalog = catalog

    async def execute(
        self,
        profile: RepositoryProfile,
        *,
        actor_type: ActorType = ActorType.SERVICE,
        actor_id: str = "repomesh-api",
    ) -> None:
        event = EventEnvelope(
            event_type="RepositoryRegistered",
            actor_type=actor_type,
            actor_id=actor_id,
            aggregate_type="Repository",
            aggregate_id=profile.id,
            aggregate_version=1,
            correlation_id=new_id(),
            payload={"name": profile.name, "url": profile.url},
        )
        await self._catalog.add(profile, events=(event,))


class RepositoryNotFound(LookupError):
    pass


class UpdateRepositoryVerification:
    """Replace the operator-owned command/path pair for one repository.

    This is deliberately a full replacement, not an append operation, so a
    browser retry has the same result and cannot duplicate commands or paths.
    Repository scanning remains responsible only for derived profile evidence.
    """

    def __init__(self, catalog: RepositoryCatalog) -> None:
        self._catalog = catalog

    async def execute(
        self,
        repository_id: UUID,
        *,
        test_commands: tuple[str, ...],
        test_paths: tuple[str, ...],
    ) -> RepositoryProfile:
        updated = await self._catalog.update_verification(
            repository_id,
            test_commands=test_commands,
            test_paths=test_paths,
        )
        if updated is None:
            raise RepositoryNotFound(f"Repository not found: {repository_id}")
        return updated


class UpdateRepositoryCapabilityProfile:
    """Set or clear the team capability profile for one repository.

    Full replacement, same retry-safety as ``UpdateRepositoryVerification``.
    The name is validated against ``capability_management``'s published
    profile set here — at the boundary — so an unknown profile cannot reach
    storage and wait to fail a dispatch that tries to assemble under it.
    """

    def __init__(self, catalog: RepositoryCatalog) -> None:
        self._catalog = catalog

    async def execute(
        self,
        repository_id: UUID,
        *,
        capability_profile: str | None,
    ) -> RepositoryProfile:
        if capability_profile is not None:
            # "default" is what storage already says with NULL; keeping one
            # spelling of that state is cheaper than reconciling two forever.
            if capability_profile == DEFAULT_TEAM_PROFILE:
                raise ValueError("clear the capability profile with null instead of 'default'")
            if capability_profile not in TEAM_CAPABILITY_PROFILES:
                raise ValueError(f"unknown capability profile: {capability_profile}")
        updated = await self._catalog.update_capability_profile(
            repository_id,
            capability_profile=capability_profile,
        )
        if updated is None:
            raise RepositoryNotFound(f"Repository not found: {repository_id}")
        return updated


@dataclass(frozen=True, slots=True)
class ScanRegistration:
    """What a scan actually put in the catalog, in the counts the console shows."""

    total_scanned: int = 0
    registered: tuple[RepositoryProfile, ...] = field(default_factory=tuple)
    skipped: int = 0
    failed: int = 0


async def register_scanned_profiles(
    profiles: Sequence[RepositoryProfile],
    catalog: RepositoryCatalog,
) -> ScanRegistration:
    """Register freshly scanned profiles, skipping names already in the catalog.

    Shared by the synchronous scan endpoints and the console's background scan
    tasks so both report the same counts from the same code.

    Skipping by name is what makes re-scanning idempotent, and that idempotence
    is load-bearing: the console offers exactly one retry — scan the whole
    thing again — so a partially failed scan has to be safe to repeat.

    A per-repo registration failure is counted, not raised: one repo that
    cannot be written must not discard the other thirty-nine that could. The
    reason goes to the log, not to the caller, and not into the count's name —
    ``failed`` here means "we could not register it", which is a different
    thing from "we could not scan it".
    """

    existing = {profile.name for profile in await catalog.list()}
    register = RegisterRepository(catalog)
    registered: list[RepositoryProfile] = []
    skipped = 0
    failed = 0

    for profile in profiles:
        if profile.name in existing:
            skipped += 1
            continue
        try:
            await register.execute(profile)
        except Exception:
            _logger.warning("failed to register scanned repository %s", profile.name, exc_info=True)
            failed += 1
            continue
        # Guards against a single scan carrying the same name twice; without
        # it the second copy would hit the catalog's uniqueness constraint and
        # be reported as a failure rather than as the duplicate it is.
        existing.add(profile.name)
        registered.append(profile)

    return ScanRegistration(
        total_scanned=len(profiles),
        registered=tuple(registered),
        skipped=skipped,
        failed=failed,
    )
