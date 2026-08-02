from uuid import UUID

from repomesh.modules.repository_intelligence.domain import RepositoryProfile


class InMemoryRepositoryCatalog:
    def __init__(self) -> None:
        self._profiles: dict[UUID, RepositoryProfile] = {}

    async def add(self, profile: RepositoryProfile) -> None:
        self._profiles[profile.id] = profile

    async def list(self) -> list[RepositoryProfile]:
        return list(self._profiles.values())

    async def get(self, repository_id: UUID) -> RepositoryProfile | None:
        return self._profiles.get(repository_id)
