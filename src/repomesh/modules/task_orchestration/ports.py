from typing import Protocol
from uuid import UUID

from repomesh.modules.task_orchestration.domain import ExecutionPlan, Task
from repomesh.shared.events import EventEnvelope


class TaskAuditLog(Protocol):
    """Decision-chain audit sink (contract decision-chain v0.1 §3).

    One ``TasksPlanned`` event per created task. Module code depends on this
    port only; the concrete writer is wired at the composition root.
    """

    async def append(self, event: EventEnvelope) -> None: ...



class TaskStore(Protocol):
    async def add(
        self,
        task: Task,
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> None: ...

    async def get(self, task_id: UUID) -> Task | None: ...

    async def get_by_idempotency_key(self, idempotency_key: str) -> tuple[Task, str] | None: ...

    async def assignment_key(self, task_id: UUID) -> str | None: ...
    """The idempotency key this task was assigned under, read back by id.

    The reverse of ``get_by_idempotency_key``, and it exists for one caller:
    re-dispatch (§8.7.4) must republish the task package under the *original*
    key, because the publisher hashes that key into the package's content and a
    different one is refused as a conflict. Without this the key is knowable
    only by whoever built it, which by then is several rounds in the past.
    """

    async def update(self, task: Task, *, expected_version: int) -> None: ...

    async def list_by_project(self, project_id: UUID) -> tuple[Task, ...]: ...

    async def list_by_parent(self, parent_task_id: UUID) -> tuple[Task, ...]: ...


class ExecutionPlanStore(Protocol):
    async def add(self, plan: ExecutionPlan, *, idempotency_key: str) -> None: ...

    async def get(self, plan_id: UUID) -> ExecutionPlan | None: ...

    async def get_by_idempotency_key(self, idempotency_key: str) -> ExecutionPlan | None: ...

    async def update(self, plan: ExecutionPlan, *, expected_version: int) -> None: ...

    async def find_by_leader_task(self, leader_task_id: UUID) -> ExecutionPlan | None: ...

    async def list_all(self) -> tuple[ExecutionPlan, ...]: ...
