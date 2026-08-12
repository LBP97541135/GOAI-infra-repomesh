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
    SUPERSEDED = "superseded"


class TaskOrigin(StrEnum):
    """Why this task exists.

    Consumers used to answer this by comparing ``title`` against the literal
    CIReworkTaskCreator assigns, so rewording a display string silently
    changed derived read-model fields (attempt number, ``repairing`` display
    status, repair timeline) with no test going red. Origin is a declared
    fact of the producer instead of a side effect of presentation text.
    """

    PLANNED = "planned"  # produced directly by a plan batch
    REWORK = "rework"  # created to repair a failed delivery candidate


@dataclass(frozen=True, slots=True)
class TaskEvidenceView:
    """Structured Runner evidence for a task, when the task has any.

    ``result_summary`` is free text by contract and carries three unrelated
    shapes (a Runner JSON document, ``SUPERSEDED: ...``, and plain prose), so
    a consumer parsing it was reading a field the producer never promised.
    This view is the declared, parsed form; it is ``None`` whenever the task
    carries no structured evidence.
    """

    commit_sha: str  # non-empty; without it there is no evidence to report
    run_id: UUID | None  # null when the Runner reported no run id
    changed_files: tuple[str, ...]
    base_sha: str | None


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
    origin: TaskOrigin = TaskOrigin.PLANNED
    evidence: TaskEvidenceView | None = None


class ExecutionPlanStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class PlannedRepositoryTaskView:
    repository_id: UUID
    title: str
    instruction: str
    acceptance: tuple[str, ...]
    leader_task_id: UUID | None
    tests: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExecutionPlanView:
    id: UUID
    organization_id: UUID
    project_id: UUID
    created_by_agent_id: UUID
    status: ExecutionPlanStatus
    current_batch_index: int
    batches: tuple[tuple[PlannedRepositoryTaskView, ...], ...]


@dataclass(frozen=True, slots=True)
class WorkerTaskExecutionStatus:
    task_id: UUID
    status: TaskStatus


@dataclass(frozen=True, slots=True)
class PlannedTaskExecutionStatus:
    repository_id: UUID
    leader_task_id: UUID | None
    leader_status: TaskStatus | None
    worker_tasks: tuple[WorkerTaskExecutionStatus, ...]


@dataclass(frozen=True, slots=True)
class ExecutionPlanStatusSnapshot:
    plan_id: UUID
    status: ExecutionPlanStatus
    current_batch_index: int
    batches: tuple[tuple[PlannedTaskExecutionStatus, ...], ...]


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


@dataclass(frozen=True, slots=True)
class CreateCIReworkTaskCommand:
    organization_id: UUID
    project_id: UUID
    change_set_id: UUID
    repository_id: UUID
    repository_manager_agent_id: UUID
    worker_agent_id: UUID
    parent_task_id: UUID
    failed_head_sha: str
    failure_summary: str
    acceptance: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReportTaskCommand:
    task_id: UUID
    reporter_agent_id: UUID
    status: TaskStatus
    summary: str
    plan_version: int = 1  # plan version the reporting agent was based on
    plan_revision_needed: bool = False  # whether replanning is requested


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


@dataclass(frozen=True, slots=True)
class DeliveryGatedRepositoryView:
    """Delivery state of one repository within a project's ChangeSet.

    Used by the batch-advancement gate; it deliberately carries no delivery
    module types so task orchestration only depends on the merged flag.
    """

    repository_id: UUID
    merged: bool


class DeliveryStatePort(Protocol):
    """Read-only delivery state used to gate batch advancement on merged PRs.

    When a batch's repository tasks all succeed, the plan waits until every
    repository of the batch is merged before advancing to the next batch.
    The port returns delivery state for all repositories of a project; the
    adapter is wired in the composition root.
    """

    async def repository_states(
        self, project_id: UUID
    ) -> tuple[DeliveryGatedRepositoryView, ...]: ...


class TaskAssignmentGateway(Protocol):
    """Assign a task to an agent.

    ``origin`` is deliberately a keyword of the call and not a field of
    AssignTaskCommand: the command is hashed into ``request_fingerprint`` and
    compared on every idempotent replay, so adding a field to it would change
    the fingerprint of every command shape and make replays of tasks persisted
    before this change fail with a spurious conflict. Origin is also not part
    of a request's identity — it follows from which caller builds the task, and
    the callers own disjoint idempotency-key namespaces (``ci-rework:...`` vs.
    the plan's key prefix), so one key can never mean two different origins.
    """

    async def assign(
        self,
        command: AssignTaskCommand,
        *,
        idempotency_key: str,
        origin: TaskOrigin = TaskOrigin.PLANNED,
    ) -> TaskView: ...


class TaskSpecificationAuthor(Protocol):
    """Ensure the approved, frozen Task Specification a Worker task needs before execution."""

    async def ensure_approved(
        self,
        task: TaskView,
        *,
        allowed_paths: tuple[str, ...],
        tests: tuple[str, ...],
        idempotency_key: str,
    ) -> None: ...


class TaskReportGateway(Protocol):
    async def report(self, command: ReportTaskCommand, *, idempotency_key: str) -> TaskView: ...


@dataclass(frozen=True, slots=True)
class SupersedeTaskCommand:
    """Mark a task as SUPERSEDED by a newer plan version."""

    task_id: UUID
    reason: str = ""
    superseded_by_task_id: UUID | None = None  # id of the replacing task, if any


class TaskSuperseder(Protocol):
    """Cancel or supersede a task that is executing or queued."""

    async def supersede(
        self, command: SupersedeTaskCommand, *, idempotency_key: str
    ) -> TaskView: ...


class TaskReader(Protocol):
    async def get_view(self, task_id: UUID) -> TaskView | None: ...


class ProjectTaskReader(Protocol):
    """Read task views for cross-module project coordination."""

    async def list_project_tasks(self, project_id: UUID) -> tuple[TaskView, ...]: ...


class TaskExecutionStateGateway(Protocol):
    async def start(self, task_id: UUID, *, agent_id: UUID) -> TaskView: ...

    async def block(self, task_id: UUID, *, agent_id: UUID, summary: str) -> TaskView: ...
