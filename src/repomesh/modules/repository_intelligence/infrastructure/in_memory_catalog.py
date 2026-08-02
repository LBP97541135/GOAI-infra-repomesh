from collections.abc import Sequence
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
