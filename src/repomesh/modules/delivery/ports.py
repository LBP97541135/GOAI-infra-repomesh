from typing import Protocol
from uuid import UUID

from .domain import ChangeSet


class ChangeSetStore(Protocol):
    async def add(
        self, change_set: ChangeSet, *, idempotency_key: str, fingerprint: str
    ) -> None: ...

    async def get(self, change_set_id: UUID) -> ChangeSet | None: ...

    async def get_by_idempotency_key(self, key: str) -> tuple[ChangeSet, str] | None: ...

    async def update(self, change_set: ChangeSet, *, expected_version: int) -> None: ...

    async def find_by_candidate(
        self, repository_id: UUID, head_sha: str
    ) -> tuple[ChangeSet, ...]: ...
