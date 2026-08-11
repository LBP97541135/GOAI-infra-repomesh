from typing import Protocol
from uuid import UUID

from .domain import ValidationSnapshot


class ValidationSnapshotStore(Protocol):
    async def add(self, snapshot: ValidationSnapshot) -> None: ...

    async def get(self, snapshot_id: UUID) -> ValidationSnapshot | None: ...

    async def list_by_project(self, project_id: UUID) -> tuple[ValidationSnapshot, ...]: ...
