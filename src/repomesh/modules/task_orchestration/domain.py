from dataclasses import dataclass, field, replace
from uuid import UUID

from repomesh.modules.task_orchestration.contracts import TaskStatus, TaskView
from repomesh.shared.domain import new_id


class TaskOrchestrationError(Exception):
    pass


class TaskDenied(TaskOrchestrationError):
    pass


class TaskConflict(TaskOrchestrationError):
    pass


class TaskNotFound(TaskOrchestrationError):
    pass


_FINAL_STATUSES = frozenset({TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED})


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
        if self.status in _FINAL_STATUSES:
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
        )
