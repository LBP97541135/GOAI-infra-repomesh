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


class TaskAssignmentGenerationReader(Protocol):
    async def allows_projection(
        self, task_id: UUID, assignment_attempt_id: UUID, generation: int
    ) -> bool: ...

    async def complete_current(
        self, task_id: UUID, assignment_attempt_id: UUID, generation: int
    ) -> None: ...


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

    # Null on a run that failed before it could commit -- which is most of them,
    # and used to be the reason the whole view was thrown away (A-18, fourth
    # face). A failed run's evidence is still evidence: ``summary_text`` carries
    # the reason the operator has to act on. Consumers that need a real head
    # (delivery's candidate publication) must refuse a null themselves; there is
    # no sha here to fall back to and inventing one is how a wrong head gets
    # merged.
    commit_sha: str | None
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
    test_paths: tuple[str, ...] = ()
    depends_on: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class AppendPlanTaskInput:
    repository_id: UUID
    title: str
    instruction: str
    acceptance: tuple[str, ...]
    depends_on: tuple[UUID, ...] = ()
    tests: tuple[str, ...] = ()
    test_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AppendPlanTasksCommand:
    plan_id: UUID
    expected_plan_version: int
    actor_agent_id: UUID
    reason: str
    items: tuple[AppendPlanTaskInput, ...]
    mode: str = "commit"


@dataclass(frozen=True, slots=True)
class DynamicPlanRevisionView:
    id: UUID
    plan_id: UUID
    revision: int
    base_plan_version: int
    result_plan_version: int | None
    actor_agent_id: UUID
    reason: str
    status: str
    appended_repository_ids: tuple[UUID, ...]
    previous_batches: tuple[tuple[UUID, ...], ...]
    new_batches: tuple[tuple[UUID, ...], ...]
    created_at: datetime


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
    """Assign a task to an agent, and tell its room the task exists.

    ``origin`` is deliberately a keyword of the call and not a field of
    AssignTaskCommand: the command is hashed into ``request_fingerprint`` and
    compared on every idempotent replay, so adding a field to it would change
    the fingerprint of every command shape and make replays of tasks persisted
    before this change fail with a spurious conflict. Origin is also not part
    of a request's identity — it follows from which caller builds the task, and
    the callers own disjoint idempotency-key namespaces (``ci-rework:...`` vs.
    the plan's key prefix), so one key can never mean two different origins.

    **Dispatch and announcement are two calls, not one** (defect S-1). Writing
    the task row and telling the room about it used to be a single act, which
    put the room message ahead of anything the caller still had to write for
    that task — and for a Worker task the caller writes the execution permit.
    A Bridge sitting in the room calls back the moment the message lands, its
    preflight finds no approved specification, and by design it refuses without
    retrying: the dispatch is lost outright. So a caller with a permit to write
    assigns with ``deliver=False``, writes the permit, then calls
    :meth:`deliver_assignment`. ``deliver`` defaults to true so that a caller
    with nothing to write between the two — a leader task, a CI rework — keeps
    the single-call shape it always had, byte for byte.
    """

    async def assign(
        self,
        command: AssignTaskCommand,
        *,
        idempotency_key: str,
        origin: TaskOrigin = TaskOrigin.PLANNED,
        deliver: bool = True,
    ) -> TaskView: ...

    async def deliver_assignment(self, task_id: UUID) -> None: ...


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


class RoomReportEligibilityReader(Protocol):
    """May a room's JSON message still move this task's state? (D-7)

    ``False`` for the one case the adjudication closes: a task assigned to a
    WORKER that was dispatched with a published task package. That task is a
    coding task, its truth is the Runner's event stream, and a chat message
    claiming it succeeded is at best a duplicate and at worst a Worker
    reporting on work it did not do. Everything else — a leader task, a task
    nothing ever dispatched, a task id that names nothing — stays ``True``, so
    the paths this ruling does not close keep their existing behaviour,
    including their existing refusals.

    Narrow on purpose. ``collaboration`` needs exactly this one bit and must
    not learn to read task rows to compute it: the rule is
    ``task_orchestration``'s (it is a statement about how *its* dispatch works)
    and lives with the module that could change it.
    """

    async def accepts_room_report(self, task_id: UUID) -> bool: ...


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


# ---------------------------------------------------------------------------
# LeaderDecision — the external Repository Leader's decision surface
#
# Producer of ``contracts/leader-actions/v1``. Everything below is this
# module's own vocabulary for that frozen wire contract: the phase a leader
# assignment is in, the facts the server hands a leader to plan from, and the
# refusal codes the HTTP surface may answer with. The wire shape is produced
# by ``RepositoryAssignmentPackage.to_wire`` and nowhere else, so an API layer
# cannot re-derive it into a second, drifting copy.
# ---------------------------------------------------------------------------


class LeaderAssignmentPhase(StrEnum):
    """Where one leader assignment stands in its own state machine.

    The four values are the frozen wire enum
    (``repository-assignment-package.schema.json``), declared in full because
    they are the contract's vocabulary. Only ``PLANNING`` is *reachable*
    today: a batch parked for an external leader is written in that phase and
    stays there until ``POST /plan`` lands, which is a later slice. Declaring
    the others is not the same as implementing their transitions, and a
    partial enum would have forced the wire producer to invent names.
    """

    PLANNING = "planning"
    EXECUTING = "executing"
    REVIEW_DUE = "review_due"
    CLOSED = "closed"


class LeaderActionErrorCode(StrEnum):
    """The frozen machine-readable refusals of the leader-actions surface.

    Mirrors ``structured-error.schema.json``'s enum exactly, and the whole
    enum is declared rather than the subset one slice raises: the code-to-
    status mapping is frozen in ``fixtures/error-matrix.json`` as a single
    table, and a producer that knew half of it could reuse a code under the
    wrong status without any test noticing.
    """

    INVALID_TOKEN = "invalid_token"
    FORBIDDEN_NOT_ASSIGNEE = "forbidden_not_assignee"
    FORBIDDEN_ROLE = "forbidden_role"
    ASSIGNMENT_NOT_FOUND = "assignment_not_found"
    PHASE_CONFLICT = "phase_conflict"
    DECOMPOSITION_MODE_CONFLICT = "decomposition_mode_conflict"
    PLAN_INVALID_DAG_CYCLE = "plan_invalid_dag_cycle"
    PLAN_INVALID_DAG_COVERAGE = "plan_invalid_dag_coverage"
    PLAN_INVALID_ASSIGNEE = "plan_invalid_assignee"
    PLAN_INVALID_ALLOWED_PATHS = "plan_invalid_allowed_paths"
    PLAN_INVALID_TESTS_REMOVED = "plan_invalid_tests_removed"
    PLAN_INVALID_PROVENANCE = "plan_invalid_provenance"
    REVIEW_INVALID_FINDINGS = "review_invalid_findings"


class LeaderActionRefused(Exception):
    """A verdict on a leader-actions request, carrying its frozen code.

    Declared in ``contracts`` rather than ``domain`` for the reason the
    collaboration module gives for its own hierarchy: the API layer has to map
    these onto status codes, and reaching into a module's ``domain`` to read
    its refusals is importing internals. The code travels with the exception
    so the translation is a table lookup rather than a chain of ``except``
    clauses that could disagree with the frozen matrix.
    """

    def __init__(self, code: LeaderActionErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class LeaderSafetyEnvelopeView:
    """The hard bounds a leader's plan is validated against.

    Derived once, when the batch is parked, and stored — not re-derived on
    every read. A worker's responsibility paths can change while a leader is
    planning, and an envelope that moved underneath the plan would reject a
    submission that was inside its bounds when it was handed out. The envelope
    the leader was given is the envelope the clamp must use.
    """

    allowed_path_roots: tuple[str, ...]
    test_paths: tuple[str, ...]
    test_commands: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorkerRosterEntryView:
    """One worker a leader may assign to, as the leader sees it.

    ``worker_name`` is the agent's AgentTeams resource name: the only name
    RepoMesh holds for a principal, and the one a leader will see again in the
    room. No workspace path and no room id — a leader is given text and
    structured facts, never a place on disk (adjudication D-8).
    """

    worker_agent_id: UUID
    worker_name: str
    responsibility_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorkerEvidenceView:
    """What one worker task produced, frozen at the moment review opened.

    A snapshot, not a live read (adjudication A-6). A verdict has to be
    attributable to the facts it was given: a leader that approved two green
    runs must not be recorded as having approved whatever those tasks look
    like after a later redispatch rewrote their evidence.

    Every nullable field here is nullable because the Runner genuinely does not
    always produce it — a run that failed before committing has no ``commit_sha``,
    a task whose ``result_summary`` is prose rather than a Runner document has
    no structured evidence at all. ``diff_stat`` is nullable for a stronger
    reason: nothing in this system produces one (the Runner's result carries no
    diffstat and the delivery read model hardcodes ``None``), so it is here to
    be honestly absent rather than to be filled in later by a guess.
    """

    worker_task_id: UUID
    worker_agent_id: UUID
    status: TaskStatus
    run_id: UUID | None
    commit_sha: str | None
    changed_files: tuple[str, ...]
    test_results: tuple[TaskTestResultView, ...]
    diff_stat: str | None = None
    summary: str | None = None


@dataclass(frozen=True, slots=True)
class LeaderReviewEvidenceView:
    """The immutable package one review round is judged against."""

    review_revision: int
    worker_evidence: tuple[WorkerEvidenceView, ...]


@dataclass(frozen=True, slots=True)
class AcceptedPlanView:
    """The leader's accepted product, kept verbatim beside what it created.

    ``decision`` is the submitted document exactly as it arrived. The server
    persists the leader's Engineering Spec and DAG and never rewrites them, so
    the stored copy *is* the wire copy — which is also what makes the plan
    attributable to the leader rather than to a server that reformatted it.

    ``fingerprint`` is what makes a repeat submission decidable. Identical is
    the same plan and answers 200 with ``receipt``; anything else is a second
    plan for a key that already has one, and the contract says that is a
    refusal rather than a silent replacement.
    """

    fingerprint: str
    plan_revision: int
    worker_task_ids: tuple[UUID, ...]
    decision: dict[str, object]


@dataclass(frozen=True, slots=True)
class AcceptedReviewView:
    """One accepted verdict, keyed in its assignment by the revision it judged."""

    fingerprint: str
    verdict: str
    review_revision: int
    leader_task_status: str
    rework_task_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class LeaderAssignmentView:
    """The persisted record of a batch parked for an external leader.

    Written by batch assignment at the moment it decides not to decompose
    server-side, and read back by every leader-actions request. It carries the
    facts that must not drift (the envelope, the roster) and the phase; the
    repository task's own text is *not* copied here, because the task row is
    its source of truth and a second copy could only ever disagree with it.

    Everything after ``worker_roster`` is the state machine's own memory, and
    all of it is defaulted so that the record batch assignment writes is
    unchanged: a freshly parked assignment is a planning assignment with no
    plan, no evidence and no verdicts, which is exactly what those defaults
    say.
    """

    leader_task_id: UUID
    organization_id: UUID
    project_id: UUID
    repository_id: UUID
    leader_agent_id: UUID
    phase: LeaderAssignmentPhase
    safety_envelope: LeaderSafetyEnvelopeView
    worker_roster: tuple[WorkerRosterEntryView, ...]
    #: The round the *next* review verdict is judged against. 1 while the first
    #: round is planned and executed; incremented by ``request_rework``, which
    #: is the only thing that opens another round.
    review_revision: int = 1
    accepted_plan: AcceptedPlanView | None = None
    review_evidence: LeaderReviewEvidenceView | None = None
    #: Accepted verdicts, by the revision each judged. A collection rather than
    #: one slot because ``request_rework`` opens a second round and round 1's
    #: receipt must stay replayable after it does.
    accepted_reviews: tuple[AcceptedReviewView, ...] = ()

    def accepted_review(self, review_revision: int) -> AcceptedReviewView | None:
        return next(
            (item for item in self.accepted_reviews if item.review_revision == review_revision),
            None,
        )


@dataclass(frozen=True, slots=True)
class RepositoryTaskFactsView:
    """The repository-level task as the Organization Manager assigned it."""

    title: str
    instruction: str
    acceptance: str


@dataclass(frozen=True, slots=True)
class RepositoryDependencyEdge:
    """Discovery's cross-repository fact: downstream depends on upstream."""

    upstream_repository_id: UUID
    downstream_repository_id: UUID


#: ``repositoryTask.title`` is ``maxLength: 200`` on the wire while the task
#: row is ``String(500)``; ``workerRoster[].workerName`` is ``maxLength: 100``
#: while a resource name has no declared bound. Truncating is the lesser evil:
#: the alternative is a package that fails its own frozen schema and reaches
#: no leader at all.
_WIRE_TITLE_LIMIT = 200
_WIRE_WORKER_NAME_LIMIT = 100


def _clamp(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit]


@dataclass(frozen=True, slots=True)
class RepositoryAssignmentPackage:
    """Response of ``GET /agent-actions/leader/assignments/{taskId}``.

    Assembled per request from the assignment record plus the leader task row,
    so the text a leader reads is always the task's current text while the
    bounds it is clamped by are the frozen ones.

    ``advisory_*`` is everything the server offers as a *hint*: the wire object
    carries ``authoritative: false`` and nothing in it is validated against or
    clamped to. Empty is a legitimate answer and is what this slice produces —
    a hint the server does not have is better absent than invented.
    """

    assignment: LeaderAssignmentView
    repository_task: RepositoryTaskFactsView
    advisory_dependency_edges: tuple[RepositoryDependencyEdge, ...] = ()
    advisory_discovery_evidence: str | None = None
    advisory_decomposition_hint: str | None = None

    def to_wire(self) -> dict[str, object]:
        """The frozen ``repository-assignment-package.v1`` document.

        ``reviewEvidence`` is the contract's first frozen invariant made
        executable: null in ``planning``/``executing``, non-null in
        ``review_due``/``closed``. It is derived from the phase rather than from
        whether the field happens to be populated, so a package can never say
        "here is the evidence" about a round that has not finished, nor hide
        evidence from a round that has — a review verdict may only ever be
        based on the package's own evidence, and this is where that is true.

        A ``review_due``/``closed`` assignment with no stored evidence is a
        broken invariant rather than a thinner package, and raises: the frozen
        schema declares ``workerEvidence`` as ``minItems: 1``, so there is no
        honest document to emit for it.
        """

        assignment = self.assignment
        shows_evidence = assignment.phase in {
            LeaderAssignmentPhase.REVIEW_DUE,
            LeaderAssignmentPhase.CLOSED,
        }
        if shows_evidence and assignment.review_evidence is None:
            raise LeaderActionRefused(
                LeaderActionErrorCode.PHASE_CONFLICT,
                "this assignment is in a review phase but carries no evidence package",
            )
        advisory: dict[str, object] = {"authoritative": False}
        if self.advisory_discovery_evidence:
            advisory["discoveryEvidence"] = self.advisory_discovery_evidence
        if self.advisory_dependency_edges:
            advisory["dependencyEdges"] = [
                {
                    "upstreamRepositoryId": str(edge.upstream_repository_id),
                    "downstreamRepositoryId": str(edge.downstream_repository_id),
                }
                for edge in self.advisory_dependency_edges
            ]
        if self.advisory_decomposition_hint:
            advisory["decompositionHint"] = self.advisory_decomposition_hint
        return {
            "schemaVersion": "repomesh.leader-actions.assignment-package.v1",
            "leaderTaskId": str(assignment.leader_task_id),
            "phase": assignment.phase.value,
            "organizationId": str(assignment.organization_id),
            "projectId": str(assignment.project_id),
            "repositoryId": str(assignment.repository_id),
            "repositoryTask": {
                "title": _clamp(self.repository_task.title, _WIRE_TITLE_LIMIT),
                "instruction": self.repository_task.instruction,
                "acceptance": self.repository_task.acceptance,
            },
            "workerRoster": [
                {
                    "workerAgentId": str(entry.worker_agent_id),
                    "workerName": _clamp(entry.worker_name, _WIRE_WORKER_NAME_LIMIT),
                    "responsibilityPaths": list(entry.responsibility_paths),
                }
                for entry in assignment.worker_roster
            ],
            "safetyEnvelope": {
                "allowedPathRoots": list(assignment.safety_envelope.allowed_path_roots),
                "testPaths": list(assignment.safety_envelope.test_paths),
                "testCommands": list(assignment.safety_envelope.test_commands),
            },
            "advisoryContext": advisory,
            "reviewEvidence": (
                _review_evidence_wire(assignment.review_evidence) if shows_evidence else None
            ),
        }


def _review_evidence_wire(evidence: "LeaderReviewEvidenceView | None") -> dict[str, object]:
    """The frozen ``reviewEvidence`` object, omitting what does not exist.

    ``diffStat`` and ``summary`` are optional in the schema and typed as
    strings, so a missing one is left out rather than emitted as ``null``:
    ``null`` would fail the schema, and inventing a diff stat nothing in this
    system computes would be worse than either.
    """

    assert evidence is not None  # noqa: S101 - guarded by the caller's phase check
    items: list[dict[str, object]] = []
    for item in evidence.worker_evidence:
        wire: dict[str, object] = {
            "workerTaskId": str(item.worker_task_id),
            "workerAgentId": str(item.worker_agent_id),
            "status": item.status.value,
            "runId": str(item.run_id) if item.run_id is not None else None,
            "commitSha": item.commit_sha,
            "changedFiles": list(item.changed_files),
            "testResults": [
                {"command": result.command, "exitCode": result.exit_code}
                for result in item.test_results
            ],
        }
        if item.diff_stat:
            wire["diffStat"] = item.diff_stat
        if item.summary:
            wire["summary"] = item.summary
        items.append(wire)
    return {"reviewRevision": evidence.review_revision, "workerEvidence": items}


# ---------------------------------------------------------------------------
# The two writes: what a leader submits, and what it gets back
# ---------------------------------------------------------------------------

PLAN_DECISION_SCHEMA_VERSION = "repomesh.leader-actions.plan-decision.v1"
REVIEW_DECISION_SCHEMA_VERSION = "repomesh.leader-actions.review-decision.v1"
PLAN_RECEIPT_SCHEMA_VERSION = "repomesh.leader-actions.plan-receipt.v1"
REVIEW_RECEIPT_SCHEMA_VERSION = "repomesh.leader-actions.review-receipt.v1"

#: The one provenance a leader product may claim (frozen invariant 5). A
#: server-authored plan cannot carry it honestly, which is the whole point of
#: requiring it.
LEADER_PROVENANCE_SOURCE = "leader-codex-session"


@dataclass(frozen=True, slots=True)
class LeaderProvenanceView:
    """Where a leader product came from, as the leader states it."""

    source: str
    session_thread_id: str
    turn_id: str | None = None


@dataclass(frozen=True, slots=True)
class LeaderWorkerTaskDraft:
    """One worker task the leader wants created, before the server clamps it."""

    node_id: str
    assignee_worker_agent_id: UUID
    title: str
    instruction: str
    allowed_paths: tuple[str, ...]
    tests: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RepositoryPlanDecision:
    """Request body of ``POST .../plan``, parsed but not yet validated.

    ``from_wire`` does shape, ``SubmitRepositoryPlan`` does judgement. The split
    matters because the two answer with different codes: a body that is not the
    frozen document at all cannot be told apart from any other malformed request
    and is a 422 from the framework, while a well-formed plan that breaks an
    invariant is a 409 carrying the frozen code that names which one.

    ``raw`` is the document exactly as submitted, kept so the accepted plan can
    be persisted verbatim and fingerprinted without re-serialising it through
    a shape this class might one day round-trip imperfectly.
    """

    engineering_spec_summary: str
    engineering_spec_markdown: str
    nodes: tuple[str, ...]
    edges: tuple[tuple[str, str], ...]
    worker_tasks: tuple[LeaderWorkerTaskDraft, ...]
    provenance: LeaderProvenanceView
    raw: dict[str, object]


@dataclass(frozen=True, slots=True)
class PlanReceipt:
    """200 response of ``POST .../plan``."""

    leader_task_id: UUID
    plan_revision: int
    worker_task_ids: tuple[UUID, ...]

    def to_wire(self) -> dict[str, object]:
        return {
            "schemaVersion": PLAN_RECEIPT_SCHEMA_VERSION,
            "leaderTaskId": str(self.leader_task_id),
            "planRevision": self.plan_revision,
            "workerTaskIds": [str(task_id) for task_id in self.worker_task_ids],
        }


class LeaderReviewVerdict(StrEnum):
    """The three ways a review round can end (frozen wire enum)."""

    APPROVE = "approve"
    REQUEST_REWORK = "request_rework"
    ESCALATE = "escalate"


@dataclass(frozen=True, slots=True)
class LeaderReviewFinding:
    """One thing the leader found. ``rework_instruction`` makes it actionable."""

    worker_task_id: UUID
    note: str
    rework_instruction: str | None = None


@dataclass(frozen=True, slots=True)
class RepositoryReviewDecision:
    """Request body of ``POST .../review``, parsed but not yet validated."""

    verdict: LeaderReviewVerdict
    summary: str
    findings: tuple[LeaderReviewFinding, ...]
    provenance: LeaderProvenanceView
    raw: dict[str, object]

    @property
    def rework_findings(self) -> tuple[LeaderReviewFinding, ...]:
        return tuple(finding for finding in self.findings if finding.rework_instruction)


@dataclass(frozen=True, slots=True)
class ReviewReceipt:
    """200 response of ``POST .../review``.

    ``leader_task_status`` is the contract's frozen verdict-to-outcome mapping
    rather than a re-read of the task row, and for ``request_rework`` the two
    are deliberately not the same thing: the row stays exactly as open as it
    was (``Task.report`` accepts only blocked/succeeded/failed, so there is no
    honest transition to make), and the receipt reports ``in_progress`` because
    that is what the contract froze the verdict to mean — this round is not
    finished and new work is in flight.
    """

    leader_task_id: UUID
    verdict: LeaderReviewVerdict
    review_revision: int
    leader_task_status: str
    rework_task_ids: tuple[UUID, ...]

    def to_wire(self) -> dict[str, object]:
        return {
            "schemaVersion": REVIEW_RECEIPT_SCHEMA_VERSION,
            "leaderTaskId": str(self.leader_task_id),
            "verdict": self.verdict.value,
            "reviewRevision": self.review_revision,
            "leaderTaskStatus": self.leader_task_status,
            "reworkTaskIds": [str(task_id) for task_id in self.rework_task_ids],
        }
