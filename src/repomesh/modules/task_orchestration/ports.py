from typing import Protocol
from uuid import UUID

from repomesh.modules.task_orchestration.contracts import LeaderAssignmentView
from repomesh.modules.task_orchestration.domain import ExecutionPlan, Task


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


class LeaderAssignmentStore(Protocol):
    """Where a batch parked for an external Repository Leader is recorded.

    Two operations, because the leader-actions surface asks two questions and
    batch assignment answers one of them.

    ``ensure`` is idempotent *by existence*, not by key, and deliberately
    returns the stored record rather than the one it was handed. Batch
    assignment re-runs whole (see ``AdvanceExecutionPlan._resume``), so the
    second call arrives with a freshly derived envelope; overwriting would let
    the bounds a leader is planning against move underneath it, which is
    precisely what the frozen envelope exists to prevent. First write wins,
    every replay reads it back.
    """

    async def ensure(self, assignment: LeaderAssignmentView) -> LeaderAssignmentView: ...

    async def get(self, leader_task_id: UUID) -> LeaderAssignmentView | None: ...

    async def save(self, assignment: LeaderAssignmentView) -> None:
        """Persist a state-machine transition of an assignment that exists.

        Deliberately *not* ``ensure`` with different words. ``ensure`` protects
        the parked record from being rewritten by a replay of batch assignment,
        which is why it keeps the first write; this one exists to move the phase
        forward, accept a plan, snapshot evidence or record a verdict, and every
        one of those is a second write that must land.

        Safe without a version column because the guard is one layer up and is
        stronger than optimistic locking would be here: a transition is only
        taken after the phase has been checked, and the writes a transition
        performs — worker tasks through the assigner, verdicts under a
        fingerprint — are each independently idempotent. Two concurrent
        identical submissions therefore converge on the same rows and the same
        receipt rather than racing for a version.
        """
        ...
