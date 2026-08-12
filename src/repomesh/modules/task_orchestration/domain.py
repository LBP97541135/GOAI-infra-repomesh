import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import UUID

from repomesh.modules.task_orchestration.contracts import (
    DeliveryRefusalView,
    ExecutionPlanStatus,
    ExecutionPlanView,
    PlannedRepositoryTaskView,
    TaskEvidenceView,
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


def _parse_evidence(result_summary: str | None) -> TaskEvidenceView | None:
    """Read structured Runner evidence out of a task's free-text summary.

    ``result_summary`` holds three unrelated shapes: the Runner gateway writes
    a JSON document, ``supersede()`` writes ``SUPERSEDED: ...``, and a plain
    agent report writes prose. Anything that is not a JSON object carrying a
    non-empty ``commitSha`` has no evidence to report, and returns ``None``.

    HONEST GAP (not solved here): this still depends on the Runner happening to
    write these particular keys. What this function changes is *where* that
    dependency lives — it is now inside the producing module, next to the
    contract that declares TaskEvidenceView, instead of in a consumer parsing a
    field that was only ever promised to be free text. Removing the dependency
    for real needs the Runner to write structured columns; that is separate
    work, and TaskEvidenceView's shape is meant to survive it unchanged.
    """

    if not result_summary:
        return None
    try:
        document = json.loads(result_summary)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    commit_sha = document.get("commitSha")
    if not isinstance(commit_sha, str) or not commit_sha:
        return None
    raw_run_id = document.get("runId")
    try:
        run_id = UUID(str(raw_run_id)) if raw_run_id else None
    except ValueError:
        # A run id that is not a UUID is not a run id we can hand out typed.
        run_id = None
    raw_changed = document.get("changedFiles")
    changed_files = (
        tuple(str(item) for item in raw_changed) if isinstance(raw_changed, list) else ()
    )
    base_sha = document.get("baseSha")
    workspace_path = document.get("workspacePath")
    return TaskEvidenceView(
        commit_sha=commit_sha,
        run_id=run_id,
        changed_files=changed_files,
        base_sha=str(base_sha) if base_sha else None,
        workspace_path=str(workspace_path) if workspace_path else None,
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
            evidence=_parse_evidence(self.result_summary),
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
class DeliveryRefusal:
    """A stated refusal to deliver the batch the plan is standing on.

    Not a status. The plan is still IN_PROGRESS and its batch still succeeded;
    what is recorded is that the delivering side looked at the evidence and
    said no, in its own words. Keeping it beside the status rather than inside
    it is what makes convergence possible: nothing has to be un-failed when the
    evidence improves, the refusal is simply cleared.
    """

    reason: str
    batch_index: int
    at: datetime
    repository_id: UUID | None = None
    task_id: UUID | None = None

    def same_as(self, other: "DeliveryRefusal") -> bool:
        """Whether this is the refusal already recorded, ignoring the clock.

        The advance path is re-entered on every terminal Runner event and every
        delivery observation, so an unresolved refusal is restated constantly.
        Comparing without ``at`` is what stops a stuck round from writing a new
        row — and bumping the aggregate's version — several times a minute.
        """

        return (
            self.reason == other.reason
            and self.batch_index == other.batch_index
            and self.repository_id == other.repository_id
            and self.task_id == other.task_id
        )

    def to_view(self) -> DeliveryRefusalView:
        return DeliveryRefusalView(
            reason=self.reason,
            batch_index=self.batch_index,
            repository_id=self.repository_id,
            task_id=self.task_id,
            at=self.at,
        )


@dataclass(frozen=True, slots=True)
class DeliveryRefusalOutcome:
    """What ``refuse_delivery`` decided: a plan to write, or nothing new to say.

    ``plan`` is None when the refusal is a repeat, so the caller can tell "we
    already know" from "we just learned" without comparing versions.
    """

    plan: "ExecutionPlan | None"
    refusal: DeliveryRefusal


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
    delivery_refusal: DeliveryRefusal | None = None
    """The delivering side's last stated refusal, or None once it is resolved."""

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

    def reopen(self) -> "ExecutionPlan":
        """Return a failed plan to IN_PROGRESS once its batch was repaired.

        ``fail()`` is reached from a single non-succeeded leader task, so a plan
        died the moment one repository's first attempt failed. Repairing that
        repository then had nowhere to land: the rework task could succeed and
        roll its leader up to SUCCEEDED while the plan stayed FAILED forever,
        because every mutator is guarded by ``_require_in_progress``.

        Reopening only restores the status. It never skips a batch or invents
        progress -- the caller must have established that the current batch now
        succeeds, and the ordinary advance path takes it from there. COMPLETED
        stays terminal: a delivered plan is history, not something to revisit.
        """

        if self.status is not ExecutionPlanStatus.FAILED:
            raise TaskConflict("only a failed execution plan can be reopened")
        return replace(self, status=ExecutionPlanStatus.IN_PROGRESS, version=self.version + 1)

    def refuse_delivery(
        self,
        reason: str,
        *,
        repository_id: UUID | None = None,
        task_id: UUID | None = None,
        at: datetime | None = None,
    ) -> "DeliveryRefusalOutcome":
        """Record why this plan's current batch was not delivered.

        Deliberately not guarded by ``_require_in_progress``: recording a
        refusal is not progress, it is the reason there is none, and a plan
        that failed for another cause may still be carrying an unresolved one.

        Returns ``None`` for the plan when the same refusal is already
        recorded. That is the difference between a state and a log — a batch
        stuck on missing test results restates its refusal on every Runner
        event, and each restatement writing a new version would bury the round
        the projection is trying to explain.
        """

        refusal = DeliveryRefusal(
            reason=reason,
            batch_index=self.current_batch_index,
            at=at or datetime.now(UTC),
            repository_id=repository_id,
            task_id=task_id,
        )
        if self.delivery_refusal is not None and self.delivery_refusal.same_as(refusal):
            return DeliveryRefusalOutcome(plan=None, refusal=self.delivery_refusal)
        return DeliveryRefusalOutcome(
            plan=replace(self, delivery_refusal=refusal, version=self.version + 1),
            refusal=refusal,
        )

    def clear_delivery_refusal(self) -> "ExecutionPlan | None":
        """Drop a refusal the delivering side no longer makes; None if there was none.

        Convergence lives here. Once a re-dispatched Worker reports evidence
        that does carry test results, delivery accepts the batch and the round
        must stop saying it was refused — without any operator action, and
        without a second code path that has to remember to.
        """

        if self.delivery_refusal is None:
            return None
        return replace(self, delivery_refusal=None, version=self.version + 1)

    def to_view(self) -> ExecutionPlanView:
        return ExecutionPlanView(
            id=self.id,
            organization_id=self.organization_id,
            project_id=self.project_id,
            created_by_agent_id=self.created_by_agent_id,
            status=self.status,
            current_batch_index=self.current_batch_index,
            batches=tuple(tuple(planned.to_view() for planned in batch) for batch in self.batches),
            delivery_refusal=(
                self.delivery_refusal.to_view() if self.delivery_refusal is not None else None
            ),
        )

    def _require_in_progress(self) -> None:
        if self.status is not ExecutionPlanStatus.IN_PROGRESS:
            raise TaskConflict("a finished execution plan cannot change")
