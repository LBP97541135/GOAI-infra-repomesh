from typing import Protocol
from uuid import UUID

from repomesh.modules.task_orchestration.domain import Task


class TaskStore(Protocol):
    async def add(
        self,
        task: Task,
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> None: ...

    async def get(self, task_id: UUID) -> Task | None: ...

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> tuple[Task, str] | None: ...

    async def update(self, task: Task, *, expected_version: int) -> None: ...

    async def list_by_project(self, project_id: UUID) -> tuple[Task, ...]: ...
