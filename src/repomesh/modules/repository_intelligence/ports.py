from typing import Protocol
from uuid import UUID

from .domain import RepositoryProfile


class RepositoryCatalog(Protocol):
    async def add(self, profile: RepositoryProfile) -> None: ...

    async def list(self) -> list[RepositoryProfile]: ...

    async def get(self, repository_id: UUID) -> RepositoryProfile | None: ...

