import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

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
