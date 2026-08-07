from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class TaskStatus(StrEnum):
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskExecutionMode(StrEnum):
    COORDINATION = "coordination"
    GOVERNED_WORKER = "governed_worker"
    DIRECT_RUN = "direct_run"


@dataclass(frozen=True, slots=True)
class TaskView:
    id: UUID
    organization_id: UUID
    project_id: UUID
    repository_id: UUID
    parent_task_id: UUID | None
    assigned_by_agent_id: UUID
    assignee_agent_id: UUID
    title: str
    instruction: str
    acceptance: tuple[str, ...]
    status: TaskStatus
    result_summary: str | None
    version: int
    execution_mode: TaskExecutionMode = TaskExecutionMode.GOVERNED_WORKER


@dataclass(frozen=True, slots=True)
class PublishedTaskPackage:
    team_name: str
    task_path: str
    content_hash: str


class TaskAssignmentPublisher(Protocol):
    async def publish(
        self,
        task: TaskView,
        *,
        team_name: str,
        room_id: str,
        assignee_resource_name: str,
        idempotency_key: str,
    ) -> PublishedTaskPackage: ...


@dataclass(frozen=True, slots=True)
class AssignTaskCommand:
    organization_id: UUID
    project_id: UUID
    repository_id: UUID
    assigned_by_agent_id: UUID
    assignee_agent_id: UUID
    title: str
    instruction: str
    acceptance: tuple[str, ...]
    parent_task_id: UUID | None = None
    execution_mode: TaskExecutionMode | None = None


@dataclass(frozen=True, slots=True)
class ReportTaskCommand:
    task_id: UUID
    reporter_agent_id: UUID
    status: TaskStatus
    summary: str


@dataclass(frozen=True, slots=True)
class ProjectTaskProgress:
    project_id: UUID
    total: int
    assigned: int
    in_progress: int
    blocked: int
    succeeded: int
    failed: int
    cancelled: int


class TaskReportGateway(Protocol):
    async def report(self, command: ReportTaskCommand, *, idempotency_key: str) -> TaskView: ...


class TaskReader(Protocol):
    async def get_view(self, task_id: UUID) -> TaskView | None: ...


class TaskExecutionStateGateway(Protocol):
    async def start(self, task_id: UUID, *, agent_id: UUID) -> TaskView: ...

    async def block(self, task_id: UUID, *, agent_id: UUID, summary: str) -> TaskView: ...
