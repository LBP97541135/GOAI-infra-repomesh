from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from repomesh.modules.repository_intelligence.domain import RepositoryProfile
from repomesh.shared.events import EventEnvelope


class RepositoryCatalog(Protocol):
    async def add(
        self, profile: RepositoryProfile, *, events: Sequence[EventEnvelope] = ()
    ) -> None: ...

    async def list(self) -> list[RepositoryProfile]: ...

    async def get(self, repository_id: UUID) -> RepositoryProfile | None: ...
