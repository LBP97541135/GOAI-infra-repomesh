from dataclasses import dataclass
from datetime import datetime
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
class TaskTestResultView:
    """One test command the Runner reports as executed.

    ``summary`` is whichever of the Runner's ``stderr``/``stdout``/``summary``
    keys is present; the Runner engine writes none of them today, so it is
    routinely empty. It is declared because delivery's validation snapshot
    already read it out of the raw payload and must keep reading the same
    thing.
    """

    command: str
    exit_code: int
    summary: str = ""


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
    # The on-disk Runner worktree the frozen candidate commit is pushed from.
    # Declared here because delivery cannot publish a candidate without it and
    # was otherwise re-parsing ``result_summary`` to get it.
    workspace_path: str | None = None

    # -- A-18: what the coding agent said about its own verification ---------
    # A Runner run reaching ``runner.completed`` means the *process* finished,
    # not that anything was executed inside it. The live evidence for A-18 is a
    # task that succeeded with ``testResults: []`` while its own summary opens
    # "I could not execute anything to verify it". Those two facts were both in
    # the payload and neither was declared, so nothing downstream could show
    # them at the merge decision.
    #
    # The Runner's ``summary``, verbatim, un-parsed. It is prose the agent
    # wrote; the only honest thing to do with it is show it.
    summary_text: str | None = None
    # Agent-declared blockers, verbatim, **only when the payload carries them
    # as a structured list**. Today's Runner emits no ``blockers`` key at all
    # (see ``repomesh_runner.contracts.RunnerExecutionResult``), so this is
    # empty for every existing row, and that emptiness is the honest answer:
    # the live agent wrote its blockers as a markdown section inside
    # ``summary``, under a heading of its own choosing. Recovering them from
    # there means pattern-matching agent prose, which would report "0 blockers"
    # for every agent that titles the section differently -- a fabricated
    # distinction, and the same class of bug as A-18 itself. Until the Runner
    # declares them, ``summary_text`` is where the words are.
    blockers: tuple[str, ...] = ()
    # The command the Runner says it ran, and what came back. Empty/``None``
    # is exactly the live shape and exactly what makes ``verified`` false.
    test_command: str | None = None
    test_results: tuple[TaskTestResultView, ...] = ()
    # Presence only. The wire carries ``{kind, uri, contentHash}`` per artifact;
    # nothing downstream can fetch one yet, so counting them is the whole claim.
    artifact_count: int = 0

    @property
    def verified(self) -> bool:
        """Did anything actually run, and pass?

        A property rather than a field so no producer can set it to a value its
        own evidence contradicts. The rule is deliberately narrow and reads
        only structured facts: at least one recorded test command, and every
        recorded exit code zero. Absent test results are *not* verified -- the
        run's terminal status says the process ended, never that it checked.
        """

        return bool(self.test_results) and all(
            result.exit_code == 0 for result in self.test_results
        )


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
class DeliveryRefusalView:
    """Why the batch this plan is standing on was not delivered.

    Delivery declining unverified work is correct and stays; what changes is
    that the decline is now a fact the round carries rather than a traceback in
    a background log (defect A-19's silent twin). ``reason`` is the delivering
    side's own sentence, stored verbatim: a projection that reworded it would
    throw away the only part an operator can act on.
    """

    reason: str
    batch_index: int
    repository_id: UUID | None
    task_id: UUID | None
    at: datetime


@dataclass(frozen=True, slots=True)
class ExecutionPlanView:
    id: UUID
    organization_id: UUID
    project_id: UUID
    created_by_agent_id: UUID
    status: ExecutionPlanStatus
    current_batch_index: int
    batches: tuple[tuple[PlannedRepositoryTaskView, ...], ...]
    delivery_refusal: DeliveryRefusalView | None = None


class BatchDeliveryRefused(ValueError):
    """The delivery side declined to deliver this batch, and said why.

    The ``on_batch_deliver`` callback's own word, declared here rather than in
    the integration that raises it for the same reason
    ``TaskPublicationUnavailable`` is declared here: it is the port's vocabulary,
    and the module that invokes the port must be able to name it without
    importing the adapter behind it.

    A ``ValueError`` subclass on purpose. Every refusal in
    ``PlanDeliveryFinalizer._candidates_for_batch`` was already a bare
    ``ValueError``; making the type narrower rather than different means the
    callers and tests that already treat these as ValueErrors keep working,
    while the advancer can single out a *stated* refusal from an ordinary bug.
    An unhandled crash-loop is what this replaces (defect A-19): the refusal
    "Runner evidence has no test results" escaped ``_advance_if_ready`` into a
    background handler that logged and dropped it, so nothing was written,
    nothing was projected, and the console showed green tasks beside an empty
    change set forever.

    ``repository_id`` and ``task_id`` are the candidate the refusal is about,
    when the refusal knows — a contract-coverage refusal names no single
    repository, and inventing one would be worse than leaving it null.
    """

    def __init__(
        self,
        reason: str,
        *,
        repository_id: UUID | None = None,
        task_id: UUID | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.repository_id = repository_id
        self.task_id = task_id


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


class TaskPublicationUnavailable(RuntimeError):
    """The store that carries a Worker's task package cannot take it — yet.

    A Worker is handed its work as files, not as a sentence: the package is
    written to AgentTeams' shared storage and the room message only points at
    it. So a store that is unreachable, unauthenticated or out of room stops
    the dispatch just as surely as a missing Matrix room does, and reads the
    same way — nothing about the request is wrong and nothing about the server
    is broken, the execution plane simply cannot take this yet. Callers
    translate it as retryable (503), never as a 500.

    A published refusal in the same family as ``ExecutionPlaneUnavailable`` and
    ``CollaborationRouteUnavailable``, and here rather than in ``domain``
    because it is the ``TaskAssignmentPublisher`` port's own word: the
    composition root raises it on the port's behalf when the adapter behind it
    fails, and other modules' API layers have to name it to give it a status
    code. Untranslated it escaped the whole stack as a bare ``text/plain`` 500
    — an S3 ``InvalidAccessKeyId`` reported to the operator as "Internal Server
    Error", with nothing to read and nothing to press (defect A-10, found live
    2026-08-12).
    """


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


class RedispatchScope(StrEnum):
    """Which of a round's tasks an explicit re-dispatch reaches (§8.7.4).

    Two shapes, because the live evidence produced two and one setting cannot
    serve both honestly.

    ``UNFINISHED`` is the A-13 shape and the default: tasks that never reached
    a result. Nothing is written to any task row — the mention is simply sent
    again — so the cost of pressing it early is one duplicate notification.

    ``RERUN`` is the shape found on 2026-08-12: a task that reported SUCCEEDED
    without producing what the next stage needed, whose delivery then refused
    in a silent background loop. Fixing the condition is not enough, because
    the result on file is the bad one and ``report`` will not overwrite a final
    task. So this scope additionally sends finished tasks back to work (see
    ``Task.redo``) — a real write, a real re-run, and a batch that stops
    counting itself as done. Kept off the default for exactly that reason.
    """

    UNFINISHED = "unfinished"
    RERUN = "rerun"


@dataclass(frozen=True, slots=True)
class RoundRedispatch:
    """What one explicit re-dispatch of a round did (contract v0.4 §8.7.4)."""

    round_id: UUID
    attempt: str
    scope: RedispatchScope
    task_ids: tuple[UUID, ...]
    """Tasks that were dispatched again, leaders included."""
    reopened_task_ids: tuple[UUID, ...]
    """Finished tasks this call sent back to work; empty unless scope=rerun."""
    settled_task_ids: tuple[UUID, ...]
    """Tasks of the round left alone because they had already finished."""


class TaskRedispatchGateway(Protocol):
    """Dispatch an already-assigned task again under a new attempt.

    Separate from ``TaskAssignmentGateway`` because it is a different act: no
    task is created, no command is fingerprinted, and the only thing that
    varies between two calls is ``attempt`` — the token that makes the room
    message a new Matrix event instead of a deduplicated repeat.
    """

    async def redispatch(
        self, task_id: UUID, *, attempt: str, redo: bool = False
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
