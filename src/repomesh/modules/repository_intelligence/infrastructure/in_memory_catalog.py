from collections.abc import Sequence
from dataclasses import replace
from uuid import UUID

from repomesh.modules.repository_intelligence.domain import RepositoryProfile
from repomesh.shared.events import EventEnvelope


class InMemoryRepositoryCatalog:
    def __init__(self) -> None:
        self._profiles: dict[UUID, RepositoryProfile] = {}
        self.events: list[EventEnvelope] = []

    async def add(
        self, profile: RepositoryProfile, *, events: Sequence[EventEnvelope] = ()
    ) -> None:
        self._profiles[profile.id] = profile
        self.events.extend(events)

    async def list(self) -> list[RepositoryProfile]:
        return list(self._profiles.values())

    async def get(self, repository_id: UUID) -> RepositoryProfile | None:
        return self._profiles.get(repository_id)

    async def update_verification(
        self,
        repository_id: UUID,
        *,
        test_commands: tuple[str, ...],
        test_paths: tuple[str, ...],
    ) -> RepositoryProfile | None:
        profile = self._profiles.get(repository_id)
        if profile is None:
            return None
        updated = replace(
            profile,
            test_commands=test_commands,
            test_paths=test_paths,
        )
        self._profiles[repository_id] = updated
        return updated

    async def update_capability_profile(
        self,
        repository_id: UUID,
        *,
        capability_profile: str | None,
    ) -> RepositoryProfile | None:
        profile = self._profiles.get(repository_id)
        if profile is None:
            return None
        updated = replace(profile, capability_profile=capability_profile)
        self._profiles[repository_id] = updated
        return updated
