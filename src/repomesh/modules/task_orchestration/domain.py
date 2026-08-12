from dataclasses import dataclass, field, replace
from uuid import UUID

from repomesh.modules.task_orchestration.contracts import (
    ExecutionPlanStatus,
    ExecutionPlanView,
    PlannedRepositoryTaskView,
    TaskOrigin,
    TaskStatus,
    TaskView,
)
from repomesh.shared.domain import new_id
from repomesh.shared.workflow import WorkflowBlocked


class TaskOrchestrationError(Exception):
    pass


class TaskDenied(TaskOrchestrationError):
    pass


class TaskBlocked(TaskDenied, WorkflowBlocked):
    pass


class TaskConflict(TaskOrchestrationError):
    pass


class TaskNotFound(TaskOrchestrationError):
    pass


FINAL_TASK_STATUSES = frozenset(
    {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.SUPERSEDED,
    }
)


@dataclass(frozen=True, slots=True)
class Task:
    organization_id: UUID
    project_id: UUID
    repository_id: UUID
    assigned_by_agent_id: UUID
    assignee_agent_id: UUID
    title: str
    instruction: str
    acceptance: tuple[str, ...]
    parent_task_id: UUID | None = None
    id: UUID = field(default_factory=new_id)
    status: TaskStatus = TaskStatus.ASSIGNED
    result_summary: str | None = None
    version: int = 1
    origin: TaskOrigin = TaskOrigin.PLANNED

    def __post_init__(self) -> None:
        if not self.title.strip() or not self.instruction.strip():
            raise ValueError("task title and instruction are required")
        if not self.acceptance or any(not item.strip() for item in self.acceptance):
            raise ValueError("at least one non-empty acceptance criterion is required")
        if self.assigned_by_agent_id == self.assignee_agent_id:
            raise ValueError("a task must be assigned to another agent")

    def start(self) -> "Task":
        if self.status not in {TaskStatus.ASSIGNED, TaskStatus.BLOCKED}:
            raise TaskConflict(f"cannot start task from {self.status.value}")
        return replace(
            self,
            status=TaskStatus.IN_PROGRESS,
            result_summary=None,
            version=self.version + 1,
        )

    def report(self, status: TaskStatus, summary: str) -> "Task":
        if self.status in FINAL_TASK_STATUSES:
            raise TaskConflict("a final task cannot be reported again")
        if status not in {
            TaskStatus.BLOCKED,
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
        }:
            raise TaskConflict("report status must be blocked, succeeded or failed")
        if not summary.strip():
            raise ValueError("task report summary is required")
        return replace(
            self,
            status=status,
            result_summary=summary.strip(),
            version=self.version + 1,
        )

    def supersede(self, *, reason: str = "", superseded_by: UUID | None = None) -> "Task":
        """Mark this task as superseded by a newer plan version."""
        if self.status in FINAL_TASK_STATUSES:
            raise TaskConflict(f"a {self.status.value} task cannot be superseded")
        return replace(
            self,
            status=TaskStatus.SUPERSEDED,
            result_summary=(f"SUPERSEDED: {reason}" if reason else "SUPERSEDED"),
            version=self.version + 1,
        )

    def to_view(self) -> TaskView:
        return TaskView(
            id=self.id,
            organization_id=self.organization_id,
            project_id=self.project_id,
            repository_id=self.repository_id,
            parent_task_id=self.parent_task_id,
            assigned_by_agent_id=self.assigned_by_agent_id,
            assignee_agent_id=self.assignee_agent_id,
            title=self.title,
            instruction=self.instruction,
            acceptance=self.acceptance,
            status=self.status,
            result_summary=self.result_summary,
            version=self.version,
            origin=self.origin,
        )


@dataclass(frozen=True, slots=True)
class PlannedRepositoryTask:
    """One repository-level task an Organization Leader intends to assign."""

    repository_id: UUID
    title: str
    instruction: str
    acceptance: tuple[str, ...]
    leader_task_id: UUID | None = None
    tests: tuple[str, ...] = ()
    """Verification commands the Worker must run before reporting this task."""

    def __post_init__(self) -> None:
        if not self.title.strip() or not self.instruction.strip():
            raise ValueError("planned task title and instruction are required")
        if not self.acceptance or any(not item.strip() for item in self.acceptance):
            raise ValueError("at least one non-empty acceptance criterion is required")

    def assigned_to(self, leader_task_id: UUID) -> "PlannedRepositoryTask":
        return replace(self, leader_task_id=leader_task_id)

    def to_view(self) -> PlannedRepositoryTaskView:
        return PlannedRepositoryTaskView(
            repository_id=self.repository_id,
            title=self.title,
            instruction=self.instruction,
            acceptance=self.acceptance,
            leader_task_id=self.leader_task_id,
            tests=self.tests,
        )


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """Ordered batches of repository tasks owned by one Organization Leader."""

    organization_id: UUID
    project_id: UUID
    created_by_agent_id: UUID
    batches: tuple[tuple[PlannedRepositoryTask, ...], ...]
    id: UUID = field(default_factory=new_id)
    current_batch_index: int = 0
    status: ExecutionPlanStatus = ExecutionPlanStatus.IN_PROGRESS
    version: int = 1

    def __post_init__(self) -> None:
        if not self.batches or any(not batch for batch in self.batches):
            raise ValueError("an execution plan needs at least one non-empty batch")
        if not 0 <= self.current_batch_index < len(self.batches):
            raise ValueError("current_batch_index is outside the planned batches")

    @property
    def current_batch(self) -> tuple[PlannedRepositoryTask, ...]:
        return self.batches[self.current_batch_index]

    @property
    def is_last_batch(self) -> bool:
        return self.current_batch_index == len(self.batches) - 1

    def leader_task_ids(self, batch_index: int) -> tuple[UUID, ...]:
        return tuple(
            planned.leader_task_id
            for planned in self.batches[batch_index]
            if planned.leader_task_id is not None
        )

    def with_leader_tasks(
        self, batch_index: int, leader_task_ids: tuple[UUID, ...]
    ) -> "ExecutionPlan":
        batch = self.batches[batch_index]
        if len(leader_task_ids) != len(batch):
            raise TaskConflict("every planned task of a batch needs one leader task")
        assigned = tuple(
            planned.assigned_to(task_id)
            for planned, task_id in zip(batch, leader_task_ids, strict=True)
        )
        batches = tuple(
            assigned if index == batch_index else item for index, item in enumerate(self.batches)
        )
        return replace(self, batches=batches, version=self.version + 1)

    def advance(self) -> "ExecutionPlan":
        self._require_in_progress()
        if self.is_last_batch:
            raise TaskConflict("the last batch cannot be advanced")
        return replace(
            self,
            current_batch_index=self.current_batch_index + 1,
            version=self.version + 1,
        )

    def complete(self) -> "ExecutionPlan":
        self._require_in_progress()
        return replace(self, status=ExecutionPlanStatus.COMPLETED, version=self.version + 1)

    def fail(self) -> "ExecutionPlan":
        self._require_in_progress()
        return replace(self, status=ExecutionPlanStatus.FAILED, version=self.version + 1)

    def to_view(self) -> ExecutionPlanView:
        return ExecutionPlanView(
            id=self.id,
            organization_id=self.organization_id,
            project_id=self.project_id,
            created_by_agent_id=self.created_by_agent_id,
            status=self.status,
            current_batch_index=self.current_batch_index,
            batches=tuple(tuple(planned.to_view() for planned in batch) for batch in self.batches),
        )

    def _require_in_progress(self) -> None:
        if self.status is not ExecutionPlanStatus.IN_PROGRESS:
            raise TaskConflict("a finished execution plan cannot change")
