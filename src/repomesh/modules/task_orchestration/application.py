import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID

from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalReader,
    AgentPrincipalStatus,
    AgentPrincipalView,
    AgentRole,
)
from repomesh.modules.collaboration.contracts import (
    CollaborationDeliveryDeferred,
    CollaborationGateway,
    CollaborationMessageKind,
    SendCollaborationMessageCommand,
)
from repomesh.modules.project.contracts import (
    ProjectCheckpoint,
    ProjectCheckpointGateway,
    ProjectTopologyReader,
    RepositoryTeamView,
    TeamDecompositionMode,
    TeamDecompositionModeReader,
    TopologyAwareCheckpointFallback,
)
from repomesh.modules.task_orchestration.contracts import (
    LEADER_PROVENANCE_SOURCE,
    AcceptedPlanView,
    AcceptedReviewView,
    AssignTaskCommand,
    BatchDeliveryRefused,
    DeliveryStatePort,
    ExecutionPlanStatus,
    ExecutionPlanStatusSnapshot,
    ExecutionPlanView,
    LeaderActionErrorCode,
    LeaderActionRefused,
    LeaderAssignmentPhase,
    LeaderAssignmentView,
    LeaderReviewEvidenceView,
    LeaderReviewVerdict,
    LeaderSafetyEnvelopeView,
    LeaderWorkerTaskDraft,
    PlannedTaskExecutionStatus,
    PlanReceipt,
    ProjectTaskProgress,
    PublishedTaskPackage,
    RedispatchScope,
    ReportTaskCommand,
    RepositoryAssignmentPackage,
    RepositoryPlanDecision,
    RepositoryReviewDecision,
    RepositoryTaskFactsView,
    ReviewReceipt,
    RoundRedispatch,
    SupersedeTaskCommand,
    TaskAssignmentGateway,
    TaskAssignmentPublisher,
    TaskOrigin,
    TaskRedispatchGateway,
    TaskReportGateway,
    TaskSpecificationAuthor,
    TaskStatus,
    TaskView,
    WorkerEvidenceView,
    WorkerRosterEntryView,
    WorkerTaskExecutionStatus,
)
from repomesh.modules.task_orchestration.domain import (
    _REDOABLE_TASK_STATUSES,
    FINAL_TASK_STATUSES,
    ExecutionPlan,
    PlannedRepositoryTask,
    RoundNotDispatchable,
    Task,
    TaskBlocked,
    TaskConflict,
    TaskDenied,
    TaskNotFound,
)
from repomesh.modules.task_orchestration.ports import (
    ExecutionPlanStore,
    LeaderAssignmentStore,
    TaskAuditLog,
    TaskStore,
)
from repomesh.shared.domain import new_id
from repomesh.shared.events import ActorType, EventEnvelope
from repomesh.shared.idempotency import command_fingerprint

_logger = logging.getLogger(__name__)

_FAILED_TASK_STATUSES = frozenset({TaskStatus.FAILED, TaskStatus.CANCELLED})

#: A worker task nobody is waiting on any more. Wider than
#: ``FINAL_TASK_STATUSES`` by exactly ``BLOCKED``, because the frozen contract
#: says review opens when no worker task is still ``assigned``/``in_progress``
#: and lists ``blocked`` among the statuses a leader may be shown. A blocked
#: task is resting, not running: something has to decide what happens to it,
#: and in leader mode that decision is the leader's review.
_OPEN_TASK_STATUSES = frozenset({TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS})


def _worker_evidence(task: TaskView) -> WorkerEvidenceView:
    """One worker task's contribution to the round's frozen evidence.

    Everything is read off the task view, which already parses the Runner's
    document out of ``result_summary`` — so there is no run lookup to make and
    no second parser to keep in step with that one.

    ``evidence is None`` is its own fact and is not smoothed over: a task whose
    result was prose rather than a Runner document genuinely produced no run,
    no commit, no changed files and no test results, and the nulls and empty
    lists say so. ``diff_stat`` is always absent because nothing in this system
    computes one; it is left unset rather than filled with a plausible string.
    """

    evidence = task.evidence
    return WorkerEvidenceView(
        worker_task_id=task.id,
        worker_agent_id=task.assignee_agent_id,
        status=task.status,
        run_id=evidence.run_id if evidence is not None else None,
        commit_sha=evidence.commit_sha if evidence is not None else None,
        changed_files=evidence.changed_files if evidence is not None else (),
        test_results=evidence.test_results if evidence is not None else (),
        diff_stat=None,
        summary=evidence.summary_text if evidence is not None else task.result_summary,
    )

#: Every ``idempotency_key`` column in the schema is ``String(200)`` — the task
#: and execution-plan stores here, the collaboration message store a dispatch
#: writes through, and the seven others besides. Named once, checked where keys
#: are derived, and asserted against the live column definitions in
#: ``tests/task_orchestration/test_redispatch_key_length.py`` so that a schema
#: change cannot quietly outgrow it.
IDEMPOTENCY_KEY_LIMIT = 200

#: Hex characters of the attempt digest. Twelve is 48 bits: an operator would
#: have to press re-dispatch on one round about sixteen million times before a
#: pair of presses collided, and a collision costs one swallowed re-send rather
#: than anything durable.
_ATTEMPT_TOKEN_CHARS = 12


def derive_allowed_paths(
    responsibility_paths: tuple[str, ...], test_paths: tuple[str, ...]
) -> tuple[str, ...]:
    """Where work may be written, given who owns what and where the tests are.

    The one derivation, with two callers that must not disagree.

    *Server-side decomposition* calls it per worker to write that worker's
    execution permit (defect A-21: responsibility paths say which product code
    a worker owns and have nothing to say about where the repository keeps its
    tests, so the two disagreed silently until a run died on it — test paths
    only ever widen the permit).

    *Leader-mode assignment* calls it once per team, over the union of the
    team's responsibility paths, to derive the ``safetyEnvelope
    .allowedPathRoots`` a leader plans inside and the server later clamps
    submitted worker tasks against. If these two were separate expressions the
    envelope handed out could bound work the permit would then refuse, or the
    reverse — a leader planning honestly inside its stated bounds and being
    rejected for it.

    A subject with no responsibility paths already gets ``**``, and appending
    test paths to that would only make the rendered permit read as though
    something had been carved out.
    """

    if not responsibility_paths:
        return ("**",)
    return tuple(dict.fromkeys((*responsibility_paths, *test_paths)))


def _dispatch_message_key(key: str, attempt: str | None) -> str:
    """The idempotency key of a dispatch's room message (contract v0.4 §8.7.4).

    One function because the whole re-dispatch capability turns on this string,
    and it is worth having somewhere to point at.

    A collaboration message's idempotency key is used *verbatim* as the Matrix
    ``transaction_id`` in the PUT (see ``SendCollaborationMessage._deliver`` and
    ``AgentTeamsMatrixClient.send_task``), and Matrix deduplicates by
    transaction id: re-sending under a key the homeserver has already seen
    returns the original event and puts nothing new in the room. On top of that
    ``SendCollaborationMessage.send`` short-circuits a key whose message is
    already ``DELIVERED`` and never reaches the messenger at all. Two layers,
    the same effect — a dispatch is a one-shot event, and the second press is
    swallowed twice over.

    Both layers key off this string, so versioning it is what makes an explicit
    re-dispatch a genuinely new room event, and it is deliberately preferred to
    bypassing either guard:

    * ``attempt is None`` — every ordinary caller, including the replay in
      ``AdvanceExecutionPlan._resume``. The key is unchanged, both guards still
      hold, and a healthy batch re-run stays silent. Replay must not spam a
      room, and after this change it still cannot: the code that would have
      had to be weakened to allow it was never touched.
    * ``attempt`` set — one explicit operator re-dispatch. A key no message
      carries yet, so ``send`` writes a new row, hands the messenger a
      transaction id the homeserver has not seen, and the mention lands as a
      new event that a freshly-created container's sync can actually see.

    Within one re-dispatch the attempt token is constant, so pressing the same
    request twice is idempotent by exactly the machinery above — the second
    call finds the DELIVERED message and stops. Idempotent per request,
    genuinely new between requests, which is the whole specification.

    **The attempt is hashed, and the result is bounded.** The first live press
    of this feature died on ``StringDataRightTruncationError: value too long
    for type character varying(200)``, because the console's request key
    (``console-redispatch-<uuid>-<uuid>``, ~60 characters) was appended
    verbatim to an assignment key that was already 165 characters long. Nothing
    about the *identity* of an attempt needs those 60 characters — only that
    two presses differ and one press is stable — so a 12-hex digest carries it
    instead, and the caller may send a key of any length without the column
    caring.

    A digest alone would still only have left three characters of headroom
    under the limit, which is not a margin, it is a coincidence. So the result
    is checked against the limit rather than assumed to fit: an over-long base
    falls back to a digest of itself, which is bounded by construction at
    around forty characters no matter how long key prefixes grow. The readable
    form is what today's keys produce and what an operator will normally see;
    the fallback exists so that correctness does not depend on that remaining
    true. Both are exercised by tests, and both keep the two properties the
    feature rests on — distinct per press, identical on a replay of the same
    press.
    """

    if attempt is None:
        return f"{key}:message"
    token = _attempt_token(attempt)
    derived = f"{key}:message:rd:{token}"
    if len(derived) <= IDEMPOTENCY_KEY_LIMIT:
        return derived
    # The base is too long to carry legibly. Its digest still identifies the
    # task uniquely (assignment keys are unique per task, and this is one),
    # which is all the key has to do; the message row carries ``task_id`` for
    # anyone who needs to read it back.
    return f"rd:{_digest(key, 24)}:{token}"


def _digest(value: str, chars: int) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:chars]


def _attempt_token(attempt: str) -> str:
    """A short, stable, collision-resistant stand-in for the caller's key.

    Deterministic, so re-sending one request replays rather than re-posts; and
    12 hex characters, so two presses a second apart are as distinct as their
    UUIDs were without spending sixty characters of a 200-character column to
    say so.
    """

    return _digest(attempt, _ATTEMPT_TOKEN_CHARS)


class ObserveExecutionPlan:
    """Build the read model used to observe one execution plan and its tasks."""

    def __init__(self, plans: ExecutionPlanStore, tasks: TaskStore) -> None:
        self._plans = plans
        self._tasks = tasks

    async def execute(self, plan_id: UUID) -> ExecutionPlanStatusSnapshot | None:
        plan = await self._plans.get(plan_id)
        if plan is None:
            return None
        view = plan.to_view()
        project_tasks = await self._tasks.list_by_project(view.project_id)
        tasks_by_id = {task.id: task for task in project_tasks}
        children_by_parent: dict[UUID, list[Task]] = {}
        for task in project_tasks:
            if task.parent_task_id is not None:
                children_by_parent.setdefault(task.parent_task_id, []).append(task)
        batches: list[tuple[PlannedTaskExecutionStatus, ...]] = []
        for batch in view.batches:
            statuses: list[PlannedTaskExecutionStatus] = []
            for planned in batch:
                leader = (
                    tasks_by_id.get(planned.leader_task_id)
                    if planned.leader_task_id is not None
                    else None
                )
                children = (
                    children_by_parent.get(planned.leader_task_id, ())
                    if planned.leader_task_id is not None
                    else ()
                )
                statuses.append(
                    PlannedTaskExecutionStatus(
                        repository_id=planned.repository_id,
                        leader_task_id=planned.leader_task_id,
                        leader_status=leader.status if leader is not None else None,
                        worker_tasks=tuple(
                            WorkerTaskExecutionStatus(child.id, child.status) for child in children
                        ),
                    )
                )
            batches.append(tuple(statuses))
        return ExecutionPlanStatusSnapshot(
            plan_id=view.id,
            status=view.status,
            current_batch_index=view.current_batch_index,
            batches=tuple(batches),
        )


class DispatchedWorkerTaskReader:
    """Answers :class:`RoomReportEligibilityReader` from this module's own rules.

    "Carries a published task package" is not a stored flag — nothing writes
    one — so it is derived from the two facts that make it true, and only
    those two:

    * the assignee is a WORKER, and
    * the task has a recorded assignment key, i.e. it was dispatched.

    That pair is exactly the branch in ``_deliver_assignment`` that publishes:
    a WORKER assignee makes publication mandatory there (a missing publisher
    raises rather than skipping), so a dispatched WORKER task and a task with
    a published package are the same set. The derivation lives here, next to
    the code it describes, precisely so that a change to that branch has one
    obvious place to update.

    The window between ``assign(deliver=False)`` and ``deliver_assignment``
    reads as "no room report accepted" even though nothing is published yet.
    That is the right answer for a different reason: a task that has not been
    announced has nothing to report on, and refusing is the safe direction.
    """

    def __init__(self, tasks: TaskStore, directory: AgentPrincipalReader) -> None:
        self._tasks = tasks
        self._directory = directory

    async def accepts_room_report(self, task_id: UUID) -> bool:
        task = await self._tasks.get(task_id)
        if task is None:
            # Not this reader's refusal to make: the report path already has
            # its own answer for a task that does not exist, and swallowing it
            # here would turn a wrong task id into silence.
            return True
        assignee = await self._directory.get_view(task.assignee_agent_id)
        if assignee is None or assignee.role is not AgentRole.WORKER:
            return True
        return await self._tasks.assignment_key(task_id) is None


class TaskOrchestrator:
    def __init__(
        self,
        directory: AgentPrincipalReader,
        topologies: ProjectTopologyReader,
        tasks: TaskStore,
        collaboration: CollaborationGateway,
        publisher: TaskAssignmentPublisher | None = None,
        checkpoints: ProjectCheckpointGateway | None = None,
        audit: TaskAuditLog | None = None,
    ) -> None:
        self._directory = directory
        self._topologies = topologies
        self._tasks = tasks
        self._collaboration = collaboration
        self._publisher = publisher
        self._checkpoints = checkpoints or TopologyAwareCheckpointFallback(topologies)
        self._audit = audit

    async def assign(
        self,
        command: AssignTaskCommand,
        *,
        idempotency_key: str,
        origin: TaskOrigin = TaskOrigin.PLANNED,
        deliver: bool = True,
    ) -> TaskView:
        """Write the task row and, unless told otherwise, announce it.

        ``deliver=False`` stops after the row: no task package, no room
        message. The caller announces it with :meth:`deliver_assignment` once
        everything that announcement implies exists — which for a Worker task
        means its execution permit. See that method for what the old
        single-call shape cost when the two were one act (defect S-1).

        Both branches below honour the flag, the replay branch included: a
        replay whose first attempt died before writing the permit must not
        re-announce a task that still has none.
        """

        key = idempotency_key.strip()
        if not key:
            raise ValueError("idempotency_key is required")
        fingerprint = command_fingerprint(command)
        if existing := await self._tasks.get_by_idempotency_key(key):
            task, previous_fingerprint = existing
            if fingerprint != previous_fingerprint:
                raise TaskConflict("idempotency key was used for a different task")
            if deliver:
                await self._deliver_assignment(task, key)
            return task.to_view()

        assigner = await self._required_agent(command.assigned_by_agent_id)
        assignee = await self._required_agent(command.assignee_agent_id)
        if assignee.leader_agent_id != assigner.id:
            raise TaskDenied("tasks can only be assigned to a direct subordinate")
        topology = await self._topologies.get_view(command.project_id)
        if topology is None or topology.organization_id != command.organization_id:
            raise TaskDenied("project topology does not exist")
        gate = await self._checkpoints.operational_gate(command.project_id)
        if not gate.allowed:
            raise TaskBlocked(gate.reason)
        self._validate_membership(assigner, assignee, command.repository_id, topology)
        if command.parent_task_id is not None:
            parent = await self._tasks.get(command.parent_task_id)
            if parent is None:
                raise TaskNotFound("parent task does not exist")
            if parent.assignee_agent_id != assigner.id:
                raise TaskDenied("only the parent task assignee can create a child task")
            if (
                parent.project_id != command.project_id
                or parent.repository_id != command.repository_id
            ):
                raise TaskDenied("child task must remain in its parent project and repository")

        task = Task(
            organization_id=command.organization_id,
            project_id=command.project_id,
            repository_id=command.repository_id,
            parent_task_id=command.parent_task_id,
            assigned_by_agent_id=assigner.id,
            assignee_agent_id=assignee.id,
            title=command.title.strip(),
            instruction=command.instruction.strip(),
            acceptance=tuple(item.strip() for item in command.acceptance),
            origin=origin,
        )
        await self._tasks.add(
            task,
            idempotency_key=key,
            request_fingerprint=fingerprint,
        )
        await self._emit_tasks_planned(task)
        if deliver:
            await self._deliver_assignment(task, key)
        return task.to_view()

    async def _emit_tasks_planned(self, task: Task) -> None:
        """Contract decision-chain v0.1 §3.2 — one event per created task.

        Emitted only on the fresh-create path: a replay (same idempotency key)
        returns the stored task without a second event, and the projection is
        idempotent on ``event_id`` anyway (§3.3).
        """

        if self._audit is None:
            return
        await self._audit.append(
            EventEnvelope(
                event_type="TasksPlanned",
                actor_type=ActorType.AGENT,
                actor_id=str(task.assigned_by_agent_id),
                aggregate_type="Task",
                aggregate_id=task.id,
                aggregate_version=task.version,
                correlation_id=new_id(),
                organization_id=task.organization_id,
                project_id=task.project_id,
                task_id=task.id,
                payload={
                    "schema_version": 1,
                    "occurred_at": datetime.now(UTC).isoformat(),
                    "task": {
                        "task_id": str(task.id),
                        "repository_id": str(task.repository_id),
                        "title": task.title,
                        "parent_task_id": (
                            str(task.parent_task_id) if task.parent_task_id else None
                        ),
                    },
                    # §2.1: task 直接挂在 integration 决策之后（chain 顺序
                    # classification → confirmation → integration → task → pr）。
                    "upstream_step": "integration",
                },
            )
        )

    async def deliver_assignment(self, task_id: UUID) -> None:
        """Announce a task that has already been written (defect S-1).

        The second half of a dispatch split in two: the task package reaches
        the store and the room is told. A caller that must write an execution
        permit for the task assigns with ``deliver=False``, writes the permit,
        and then calls this — in that order, because the permit is the document
        the Worker's preflight reads. Announcing first loses the dispatch
        outright: a Bridge sitting in the room calls back the instant the
        message lands, ``materialize`` finds no approved specification and
        raises ``SpecificationNotFound``, and the Bridge refuses without
        retrying by design. The task then sits blocked with its permit frozen
        behind it and nothing left for an operator to press. Reproduced live
        twice before the split.

        The key is the task's *original* assignment key, read back from the
        store, for the reason :meth:`redispatch` reads it back: the publisher
        bakes ``idempotency_key`` into ``meta.json`` and hashes that into the
        package's content hash, so a fresh key would be refused as conflicting
        content. Every row :meth:`assign` writes has one; a row without one
        cannot be published under any key this method could invent, and says so
        rather than guessing.

        Idempotency is the delivery path's own — ``key:publication`` for the
        package, the dispatch key for the room message — so a caller that runs
        this twice tells the room once.
        """

        task = await self._required_task(task_id)
        key = await self._tasks.assignment_key(task.id)
        if key is None:
            raise TaskConflict(
                f"task {task.id} has no recorded assignment key, so its task "
                "package cannot be published"
            )
        await self._deliver_assignment(task, key)



    async def _deliver_assignment(
        self, task: Task, key: str, *, dispatch_attempt: str | None = None
    ) -> None:
        assignee = await self._required_agent(task.assignee_agent_id)
        published = None
        if assignee.role is AgentRole.WORKER:
            if self._publisher is None:
                raise RuntimeError("Worker task publisher is not configured")
            topology = await self._topologies.get_view(task.project_id)
            team = (
                next(
                    (
                        item
                        for item in topology.repository_teams
                        if item.repository_id == task.repository_id
                        and task.assignee_agent_id in item.worker_agent_ids
                    ),
                    None,
                )
                if topology
                else None
            )
            if team is None or not team.room_id:
                raise TaskDenied("Worker Team runtime is not ready for task publication")
            published = await self._publisher.publish(
                task.to_view(),
                team_name=team.agentteams_team_name,
                room_id=team.room_id,
                assignee_resource_name=assignee.agentteams_resource_name,
                idempotency_key=f"{key}:publication",
            )
        await self._send_assignment(task, key, published, dispatch_attempt=dispatch_attempt)

    async def _send_assignment(
        self,
        task: Task,
        key: str,
        published: PublishedTaskPackage | None,
        *,
        dispatch_attempt: str | None = None,
    ) -> None:
        await self._collaboration.send(
            SendCollaborationMessageCommand(
                organization_id=task.organization_id,
                project_id=task.project_id,
                repository_id=task.repository_id,
                task_id=task.id,
                sender_agent_id=task.assigned_by_agent_id,
                recipient_agent_id=task.assignee_agent_id,
                kind=CollaborationMessageKind.TASK_ASSIGNMENT,
                subject=task.title,
                body=self._assignment_body(task, published),
                correlation_id=task.id,
            ),
            idempotency_key=_dispatch_message_key(key, dispatch_attempt),
        )

    async def redispatch(
        self, task_id: UUID, *, attempt: str, redo: bool = False
    ) -> TaskView:
        """Dispatch an already-assigned task again, under a new attempt (§8.7.4).

        Everything a first dispatch does except create the task: the package is
        re-published and the room is told again. With ``redo`` false the task
        row itself is left exactly as it is — this is a repeat of the
        *telling*, not a second assignment, and a Worker that was already
        working sees one duplicate notification rather than a rival task.

        ``redo`` is the operator saying the result on file is wrong and the
        work must happen again — the shape found live on 2026-08-12, where a
        task reported SUCCEEDED without the evidence its delivery needed and
        the refusal became a silent background loop. It is the only path here
        that writes a task row, and it has to: ``report`` refuses a final task,
        so a re-run whose task is still SUCCEEDED could never record its own
        outcome. See :meth:`Task.redo` for what it will and will not reopen.

        The key handed to :meth:`_deliver_assignment` is the task's *original*
        assignment key, and that is not a convenience — it is forced. The
        publisher writes the package to a path derived from the task id and
        bakes ``idempotency_key`` into ``meta.json``, which feeds the content
        hash; a re-publication under a fresh key would hash differently and the
        store would refuse it as "conflicts with existing content" — a
        ``ValueError`` that e8014fd deliberately does *not* translate to a
        retryable 503, because retrying cannot change it. So the publication
        must replay under the first attempt's key, and only the room message
        may be versioned. ``attempt`` is what versions it; see
        :func:`_dispatch_message_key`.
        """

        task = await self._required_task(task_id)
        if task.status in FINAL_TASK_STATUSES:
            if not redo:
                raise TaskConflict(
                    f"task {task.id} has already finished ({task.status.value}); "
                    "there is nothing to dispatch again"
                )
            reopened = task.redo()
            await self._tasks.update(reopened, expected_version=task.version)
            task = reopened
        key = await self._tasks.assignment_key(task.id)
        if key is None:
            # The row exists and nothing remembers how it was written. Nothing
            # here can invent a key: the publication's content hash is derived
            # from it, so a guess would be refused by the store rather than
            # silently doing the wrong thing — but it would be refused as a
            # 503, telling the operator to keep pressing. Say it plainly.
            raise TaskConflict(
                f"task {task.id} has no recorded assignment key, so its task "
                "package cannot be republished"
            )
        await self._deliver_assignment(task, key, dispatch_attempt=attempt)
        return task.to_view()

    async def start(self, task_id: UUID, *, agent_id: UUID) -> TaskView:
        task = await self._required_task(task_id)
        if task.assignee_agent_id != agent_id:
            raise TaskDenied("only the assignee can start a task")
        gate = await self._checkpoints.evaluate(
            task.project_id,
            ProjectCheckpoint.EXECUTION,
            f"task:{task.id}:v{task.version}",
            repository_id=task.repository_id,
        )
        if not gate.allowed:
            raise TaskBlocked(gate.reason)
        updated = task.start()
        await self._tasks.update(updated, expected_version=task.version)
        return updated.to_view()

    async def report(self, command: ReportTaskCommand, *, idempotency_key: str) -> TaskView:
        reporter = await self._required_agent(command.reporter_agent_id)
        task = await self._required_task(command.task_id)
        if task.assignee_agent_id != command.reporter_agent_id:
            raise TaskDenied("only the assignee can report a task")
        if task.status is command.status and task.result_summary == command.summary.strip():
            updated = task
        elif task.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED}:
            if task.status is not command.status or task.result_summary != command.summary.strip():
                raise TaskConflict("a final task cannot be reported with different results")
            updated = task
        else:
            updated = task.report(command.status, command.summary)
            await self._tasks.update(updated, expected_version=task.version)
        kind = (
            CollaborationMessageKind.PROGRESS
            if command.status is TaskStatus.BLOCKED
            else CollaborationMessageKind.TASK_REPORT
        )
        try:
            await self._collaboration.send(
                SendCollaborationMessageCommand(
                    organization_id=task.organization_id,
                    project_id=task.project_id,
                    repository_id=task.repository_id,
                    task_id=task.id,
                    sender_agent_id=task.assignee_agent_id,
                    recipient_agent_id=task.assigned_by_agent_id,
                    kind=kind,
                    subject=f"{task.title}: {updated.status.value}",
                    body=command.summary.strip(),
                    correlation_id=task.id,
                ),
                idempotency_key=f"{idempotency_key}:message",
            )
        except CollaborationDeliveryDeferred as error:
            logging.getLogger(__name__).warning(
                "task report accepted while collaboration delivery awaits retry",
                extra={
                    "task_id": str(task.id),
                    "collaboration_message_id": str(error.message_id),
                },
            )
        if reporter.role is AgentRole.REPOSITORY_LEADER and command.status in {
            TaskStatus.BLOCKED,
            TaskStatus.FAILED,
        }:
            await self._checkpoints.evaluate(
                task.project_id,
                ProjectCheckpoint.EXCEPTION_ESCALATION,
                f"task:{updated.id}:v{updated.version}",
                repository_id=task.repository_id,
                requested_by_agent_id=reporter.id,
                title=f"{task.title}：仓库异常升级",
                summary=command.summary.strip(),
            )
        return updated.to_view()

    async def supersede(self, command: SupersedeTaskCommand, *, idempotency_key: str) -> TaskView:
        key = idempotency_key.strip()
        if not key:
            raise ValueError("idempotency_key is required")
        task = await self._required_task(command.task_id)
        reason = command.reason.strip()
        # Idempotent replay: same supersede already persisted.
        if task.status is TaskStatus.SUPERSEDED and task.result_summary == (
            f"SUPERSEDED: {reason}" if reason else "SUPERSEDED"
        ):
            return task.to_view()
        updated = task.supersede(
            reason=reason,
            superseded_by=command.superseded_by_task_id,
        )
        await self._tasks.update(updated, expected_version=task.version)
        return updated.to_view()

    async def progress(self, project_id: UUID) -> ProjectTaskProgress:
        tasks = await self._tasks.list_by_project(project_id)
        counts = {status: 0 for status in TaskStatus}
        for task in tasks:
            counts[task.status] += 1
        return ProjectTaskProgress(
            project_id=project_id,
            total=len(tasks),
            assigned=counts[TaskStatus.ASSIGNED],
            in_progress=counts[TaskStatus.IN_PROGRESS],
            blocked=counts[TaskStatus.BLOCKED],
            succeeded=counts[TaskStatus.SUCCEEDED],
            failed=counts[TaskStatus.FAILED],
            cancelled=counts[TaskStatus.CANCELLED],
        )

    async def list_project_tasks(self, project_id: UUID) -> tuple[TaskView, ...]:
        """Publish immutable task views without exposing the module store."""

        return tuple(task.to_view() for task in await self._tasks.list_by_project(project_id))

    async def _required_agent(self, agent_id: UUID) -> AgentPrincipalView:
        profile = await self._directory.get_view(agent_id)
        if profile is None:
            raise TaskDenied(f"agent does not exist: {agent_id}")
        if profile.status is not AgentPrincipalStatus.ACTIVE:
            raise TaskDenied("agent_disabled")
        return profile

    async def _required_task(self, task_id: UUID) -> Task:
        task = await self._tasks.get(task_id)
        if task is None:
            raise TaskNotFound(f"task does not exist: {task_id}")
        return task

    @staticmethod
    def _validate_membership(assigner, assignee, repository_id, topology) -> None:
        if assigner.id == topology.organization_leader_id:
            if not any(
                team.repository_id == repository_id and team.leader_agent_id == assignee.id
                for team in topology.repository_teams
            ):
                raise TaskDenied("repository leader is not assigned to this project repository")
            return
        if not any(
            team.repository_id == repository_id
            and team.leader_agent_id == assigner.id
            and assignee.id in team.worker_agent_ids
            for team in topology.repository_teams
        ):
            raise TaskDenied("worker is not assigned to this project repository team")

    @staticmethod
    def _assignment_body(task: Task, published: PublishedTaskPackage | None = None) -> str:
        if published is not None:
            return (
                "A verified RepoMesh task package is ready. Do not edit code directly in this "
                "chat session. Call the MCP tool "
                "repomesh-task-control.start_assigned_task with:\n"
                f'{{"task_id":"{task.id}","worker_agent_id":"{task.assignee_agent_id}"}}\n\n'
                f"Task package: {published.task_path}\n"
                f"Content hash: {published.content_hash}\n"
                "RepoMesh Runner will prepare the isolated workspace, invoke the configured "
                "coding-agent adapter, run verification, and persist the result."
            )
        acceptance = "\n".join(f"- {item}" for item in task.acceptance)
        return (
            f"{task.instruction}\n\nAcceptance criteria:\n{acceptance}\n\n"
            "When blocked or finished, reply with only this JSON object:\n"
            "{\n"
            '  "schema": "repomesh.agent-report.v1",\n'
            f'  "sender_agent_id": "{task.assignee_agent_id}",\n'
            f'  "project_id": "{task.project_id}",\n'
            f'  "task_id": "{task.id}",\n'
            '  "status": "blocked|succeeded|failed",\n'
            '  "summary": "what changed, tests run, or the blocking question"\n'
            "}"
        )


class TaskExecutionState:
    """Persist Worker execution transitions without depending on chat delivery."""

    def __init__(self, directory: AgentPrincipalReader, tasks: TaskStore) -> None:
        self._directory = directory
        self._tasks = tasks

    async def start(self, task_id: UUID, *, agent_id: UUID) -> TaskView:
        task = await self._required(task_id, agent_id)
        if task.status is TaskStatus.IN_PROGRESS:
            return task.to_view()
        updated = task.start()
        await self._tasks.update(updated, expected_version=task.version)
        return updated.to_view()

    async def block(self, task_id: UUID, *, agent_id: UUID, summary: str) -> TaskView:
        task = await self._required(task_id, agent_id)
        normalized = summary.strip()
        if task.status is TaskStatus.BLOCKED and task.result_summary == normalized:
            return task.to_view()
        updated = task.report(TaskStatus.BLOCKED, normalized)
        await self._tasks.update(updated, expected_version=task.version)
        return updated.to_view()

    async def _required(self, task_id: UUID, agent_id: UUID) -> Task:
        agent = await self._directory.get_view(agent_id)
        if agent is None or agent.status is not AgentPrincipalStatus.ACTIVE:
            raise TaskDenied("worker agent is missing or disabled")
        task = await self._tasks.get(task_id)
        if task is None:
            raise TaskNotFound(f"task does not exist: {task_id}")
        if task.assignee_agent_id != agent_id:
            raise TaskDenied("only the assignee can change execution state")
        return task


class DecomposeRepositoryTask:
    """Expand a repository task into the Worker tasks that execute it."""

    def __init__(
        self,
        directory: AgentPrincipalReader,
        topologies: ProjectTopologyReader,
        tasks: TaskStore,
        assigner: TaskAssignmentGateway,
        spec_author: TaskSpecificationAuthor | None = None,
    ) -> None:
        self._directory = directory
        self._topologies = topologies
        self._tasks = tasks
        self._assigner = assigner
        self._spec_author = spec_author

    async def execute(
        self,
        repository_task_id: UUID,
        *,
        idempotency_key: str,
        tests: tuple[str, ...] = (),
        test_paths: tuple[str, ...] = (),
    ) -> tuple[TaskView, ...]:
        key = idempotency_key.strip()
        if not key:
            raise ValueError("idempotency_key is required")
        task = await self._tasks.get(repository_task_id)
        if task is None:
            raise TaskNotFound(f"task does not exist: {repository_task_id}")
        leader = await self._directory.get_view(task.assignee_agent_id)
        if leader is None or leader.status is not AgentPrincipalStatus.ACTIVE:
            raise TaskDenied("repository leader is missing or disabled")
        if leader.role is not AgentRole.REPOSITORY_LEADER:
            raise TaskDenied("only a repository leader task can be decomposed")
        team = await self.repository_team(task.project_id, task.repository_id)
        if team.leader_agent_id != leader.id:
            raise TaskDenied("repository team is led by another agent")
        if not team.worker_agent_ids:
            raise TaskDenied("repository team has no worker to execute the task")

        # MVP granularity: one Worker task per repository task.
        worker_agent_id = team.worker_agent_ids[0]
        worker_key = f"{key}:worker:{worker_agent_id}"
        children = await self._tasks.list_by_parent(task.id)
        in_flight = any(
            child.assignee_agent_id == worker_agent_id and child.status not in FINAL_TASK_STATUSES
            for child in children
        )
        # Whether the in-flight Worker task is the one *this* key created, or
        # one some other attempt's keys did, decides what a replay may do with
        # it — and it is a question only the store can answer.
        owned = await self._tasks.get_by_idempotency_key(worker_key)
        if in_flight and owned is None:
            # Someone else's Worker task is already running under this
            # repository task. Assigning would put a second one beside it, so
            # the replay does the one repair that is safe: heal an execution
            # permit that never got written.
            views = tuple(child.to_view() for child in children)
            for child, view in zip(children, views, strict=True):
                if child.status not in FINAL_TASK_STATUSES:
                    await self._ensure_specification(
                        view, tests=tests, test_paths=test_paths, key=key
                    )
            return views

        # Two defects meet in the three calls below, and the order is what
        # settles both.
        #
        # A-10 is why this falls through at all. A replay under a key that owns
        # an in-flight task must not stop here: ``assign`` recognises the key
        # and returns the row it already wrote instead of a second one,
        # ``_ensure_specification`` finds or completes the permit, and
        # ``deliver_assignment`` re-runs the delivery that failed the first
        # time — the package upload and the room message. Short-circuiting,
        # which is what the old unconditional ``in_flight`` return did, left a
        # Worker task nobody had published and nobody had told, and no retry
        # could reach it. The replay story survives the split intact; only its
        # last step moved out of ``assign``.
        #
        # S-1 is why the delivery is now a third call rather than the tail of
        # the first. The permit is the document a Worker's preflight reads, so
        # the room may not learn of a task whose permit is not yet on disk: an
        # online Bridge calls back the instant the message lands, preflight
        # raises ``SpecificationNotFound``, and the Bridge refuses without
        # retrying by design — the dispatch is simply lost. Write the row,
        # write the permit, then say so.
        worker_task = await self._assigner.assign(
            AssignTaskCommand(
                organization_id=task.organization_id,
                project_id=task.project_id,
                repository_id=task.repository_id,
                assigned_by_agent_id=task.assignee_agent_id,
                assignee_agent_id=worker_agent_id,
                title=task.title,
                instruction=task.instruction,
                acceptance=task.acceptance,
                parent_task_id=task.id,
            ),
            idempotency_key=worker_key,
            # A worker task inherits why its repository task exists. The old
            # title-equality judgement had the same effect by accident, since
            # the title is copied down verbatim; here it is stated.
            origin=task.origin,
            deliver=False,
        )
        await self._ensure_specification(worker_task, tests=tests, test_paths=test_paths, key=key)
        await self._assigner.deliver_assignment(worker_task.id)
        return (worker_task,)

    async def _ensure_specification(
        self,
        worker_task: TaskView,
        *,
        tests: tuple[str, ...],
        test_paths: tuple[str, ...],
        key: str,
    ) -> None:
        """Produce the execution permit in the same motion as the executable task.

        The permit is the document the agent reads — ``current-task.md``
        renders "Allowed paths" verbatim — so ``derive_allowed_paths`` is what
        stops a compliant agent from concluding, correctly, that it may not
        write the test its own verification command will look for. The same
        function derives the leader-mode safety envelope; see its docstring
        for why the two must be one expression.
        """

        if self._spec_author is None:
            return
        worker = await self._directory.get_view(worker_task.assignee_agent_id)
        if worker is None or worker.status is not AgentPrincipalStatus.ACTIVE:
            raise TaskDenied("worker agent is missing or disabled")
        allowed_paths = derive_allowed_paths(tuple(worker.responsibility_paths), test_paths)
        await self._spec_author.ensure_approved(
            worker_task,
            allowed_paths=allowed_paths,
            tests=tests,
            idempotency_key=f"{key}:spec:{worker_task.assignee_agent_id}",
        )

    async def worker_roster(self, team: RepositoryTeamView) -> tuple[WorkerRosterEntryView, ...]:
        """The team's workers, as an external leader is allowed to see them.

        Refuses the same two conditions ``execute`` refuses, with the same
        sentences: a team with no worker cannot execute a repository task
        whoever decomposes it, and a roster that quietly omitted a disabled
        worker would hand a leader a plan target that cannot be assigned. The
        contract's roster is ``minItems: 1``, so an empty answer is not a
        thinner package — it is one that fails its own schema.
        """

        if not team.worker_agent_ids:
            raise TaskDenied("repository team has no worker to execute the task")
        roster: list[WorkerRosterEntryView] = []
        for worker_agent_id in team.worker_agent_ids:
            worker = await self._directory.get_view(worker_agent_id)
            if worker is None or worker.status is not AgentPrincipalStatus.ACTIVE:
                raise TaskDenied("worker agent is missing or disabled")
            roster.append(
                WorkerRosterEntryView(
                    worker_agent_id=worker.id,
                    worker_name=worker.agentteams_resource_name,
                    responsibility_paths=tuple(worker.responsibility_paths),
                )
            )
        return tuple(roster)

    @staticmethod
    def safety_envelope(
        roster: tuple[WorkerRosterEntryView, ...],
        *,
        tests: tuple[str, ...],
        test_paths: tuple[str, ...],
    ) -> LeaderSafetyEnvelopeView:
        """The hard bounds handed to a leader, derived from the same facts a permit is.

        ``allowedPathRoots`` is ``derive_allowed_paths`` over the *union* of
        the roster's responsibility paths: a leader may hand any of its
        workers any part of the team's ground, and the per-worker permit each
        task later gets is narrower still. ``testCommands`` and ``testPaths``
        are the batch item's own, unmodified — the envelope reports the
        verification the round already requires, it does not invent any.
        """

        responsibility = tuple(
            dict.fromkeys(path for entry in roster for path in entry.responsibility_paths)
        )
        return LeaderSafetyEnvelopeView(
            allowed_path_roots=derive_allowed_paths(responsibility, test_paths),
            test_paths=test_paths,
            test_commands=tests,
        )

    async def repository_team(self, project_id: UUID, repository_id: UUID) -> RepositoryTeamView:
        topology = await self._topologies.get_view(project_id)
        team = (
            next(
                (item for item in topology.repository_teams if item.repository_id == repository_id),
                None,
            )
            if topology is not None
            else None
        )
        if team is None:
            raise TaskDenied("project repository team does not exist")
        return team


@dataclass(frozen=True, slots=True)
class LeaderDecisionLane:
    """Everything batch assignment needs to park a batch for an external leader.

    One optional parameter rather than three, because the three are only
    useful together: reading a team's decomposition mode is pointless without
    somewhere to record the parked assignment, and recording one nobody is
    told about strands the round. Bundling them makes the half-wired state
    unrepresentable instead of a runtime check.

    Absent, ``AdvanceExecutionPlan`` behaves exactly as it did before this
    lane existed — every team decomposes server-side. Present, the mode reader
    answers per team from the persisted topology (adjudication D-2, PR 5.5B):
    ``LEADER`` only for a team whose external Repository Leader the adoption
    pass actually adopted, ``SERVER`` for every other team and for every
    installation that has provisioned none. Wiring the lane is still not the
    same as switching any team into leader mode.
    """

    modes: TeamDecompositionModeReader
    assignments: LeaderAssignmentStore
    collaboration: CollaborationGateway


class AdvanceExecutionPlan:
    """Drive an execution plan batch by batch as repository tasks reach a result."""

    def __init__(
        self,
        plans: ExecutionPlanStore,
        tasks: TaskStore,
        assigner: TaskAssignmentGateway,
        decomposer: DecomposeRepositoryTask,
        on_plan_completed: Callable[[ExecutionPlanView], Awaitable[None]] | None = None,
        delivery_state: DeliveryStatePort | None = None,
        on_batch_deliver: Callable[[ExecutionPlanView], Awaitable[None]] | None = None,
        leader_lane: LeaderDecisionLane | None = None,
    ) -> None:
        self._plans = plans
        self._tasks = tasks
        self._assigner = assigner
        self._decomposer = decomposer
        self._on_plan_completed = on_plan_completed
        # When wired, batch advancement is gated on the merged state of the
        # current batch's delivery pull requests (delivery-gated mode).
        self._delivery_state = delivery_state
        self._on_batch_deliver = on_batch_deliver
        self._leader_lane = leader_lane

    async def start(self, plan: ExecutionPlan, *, idempotency_key: str) -> ExecutionPlanView:
        key = idempotency_key.strip()
        if not key:
            raise ValueError("idempotency_key is required")
        if existing := await self._plans.get_by_idempotency_key(key):
            return await self._resume(existing, key)
        await self._plans.add(plan, idempotency_key=key)
        assigned = await self._assign_batch(plan, plan.current_batch_index, key_prefix=key)
        return assigned.to_view()

    async def _resume(self, plan: ExecutionPlan, key: str) -> ExecutionPlanView:
        """Re-drive the current batch of a plan a refusal left unfinished.

        ``start`` is a chain of writes — the plan row, its batch's leader task
        assignments, the plan update that records them, then each leader task's
        decomposition into the Worker task that actually gets dispatched — and
        the ones at the far end talk to the execution plane, so they are the
        ones that fail. Returning the stranded row unchanged on the retry,
        which is what recognising the key used to do, reported success for a
        round that had started nothing.

        So a replay re-runs the batch instead of skipping it. Every write it
        makes is keyed off the same prefix as the first attempt — the tasks,
        their decomposition, the assignment messages, the task packages — so
        the work that did get through is *found* rather than duplicated, and
        only what did not is created.

        THE EARLY RETURN THAT USED TO BE HERE WAS THE BUG (defect A-10). It
        skipped the batch once ``leader_task_ids`` was complete, on the theory
        that a fully-assigned batch has nothing left to do. But the plan row is
        updated with those ids *before* the decomposition loop, so every
        failure downstream of it — the Worker task's package upload, its room
        message — lands on a plan that already looks finished. Live, that meant
        a materialize whose S3 upload was refused could never be repaired: the
        retry answered 200 over a round with task rows, an empty bucket and a
        Worker that had never been told anything. Counting leader tasks cannot
        distinguish "dispatched" from "written down", so it is the wrong
        question; re-running and letting each keyed write answer for itself is
        the right one.

        Re-running a *healthy* batch is deliberately cheap rather than
        forbidden: the leader tasks are found by key, the packages are content-
        hashed and rewritten identically, and a delivered room message is
        recognised and not sent twice. A settled plan — failed or completed —
        is still left alone, because that is history and not unfinished work.
        """

        index = plan.current_batch_index
        if plan.status is not ExecutionPlanStatus.IN_PROGRESS:
            return plan.to_view()
        # The first batch was assigned under the caller's key; every later one
        # under the plan's id (see `_advance_if_ready`). Resuming has to reuse
        # whichever prefix that batch already wrote under, or the idempotent
        # lookups miss and the work is done twice.
        prefix = key if index == 0 else str(plan.id)
        assigned = await self._assign_batch(plan, index, key_prefix=prefix)
        return assigned.to_view()

    async def on_task_terminal(self, task_id: UUID) -> None:
        task = await self._tasks.get(task_id)
        if task is None:
            return
        leader_task = task
        if task.parent_task_id is not None:
            parent = await self._tasks.get(task.parent_task_id)
            if parent is None:
                return
            leader_task = await self._roll_up(parent)
        if leader_task.status not in FINAL_TASK_STATUSES:
            return
        plan = await self._plans.find_by_leader_task(leader_task.id)
        if plan is None:
            return
        if plan.status is ExecutionPlanStatus.COMPLETED:
            await self._notify_plan_completed(plan.to_view())
            return
        if plan.status is not ExecutionPlanStatus.IN_PROGRESS:
            return
        if leader_task.id not in plan.leader_task_ids(plan.current_batch_index):
            return
        if leader_task.status is not TaskStatus.SUCCEEDED:
            await self._settle(plan, plan.fail())
            return
        await self._advance_if_ready(plan)

    async def reconsider_task(self, task_id: UUID) -> None:
        """Re-evaluate batch advancement, e.g. after a delivery merge is observed.

        Delivery observations arrive asynchronously (webhook or 15s replay
        worker); once a repository of the current batch is merged this entry
        point lets the plan advance without waiting for another task event.

        The observed task may be a worker task or its repository leader task;
        resolve the owning repository task before locating the plan.
        """
        task = await self._tasks.get(task_id)
        if task is None:
            return
        leader_task = task
        if task.parent_task_id is not None:
            parent = await self._tasks.get(task.parent_task_id)
            if parent is None:
                return
            leader_task = parent
        plan = await self._plans.find_by_leader_task(leader_task.id)
        if plan is None:
            return
        if plan.status is ExecutionPlanStatus.FAILED:
            reopened = await self._reopen_if_repaired(plan)
            if reopened is None:
                return
            plan = reopened
        elif plan.status is not ExecutionPlanStatus.IN_PROGRESS:
            return
        await self._advance_if_ready(plan)

    async def _reopen_if_repaired(self, plan: ExecutionPlan) -> ExecutionPlan | None:
        """Reopen a failed plan whose current batch now succeeds, else None.

        A plan fails on the first non-succeeded leader task of its batch. When
        a repair (a rework task) later carries that leader to SUCCEEDED, the
        plan is the only thing still holding the batch back, so the same
        re-evaluation entry point that handles delivery observations lets it
        continue instead of stranding the repaired work.

        The batch check comes first: reopening a plan whose batch still has a
        failed leader would only fail it again on the next event.
        """

        if not await self._batch_succeeded(plan):
            return None
        reopened = plan.reopen()
        if not await self._settle(plan, reopened):
            return None
        return reopened

    async def _advance_if_ready(self, plan: ExecutionPlan) -> None:
        """Advance the plan only when the batch succeeded and delivery merged.

        In delivery-gated mode (delivery_state wired) the batch is delivered
        first and the plan waits for every repository of the batch to be
        merged before assigning the next batch. Delivery observation events
        drive the waiting plan forward via ``reconsider_task``.
        """
        if not await self._batch_succeeded(plan):
            return
        if self._on_batch_deliver is not None:
            delivered = await self._deliver_batch(plan)
            if delivered is None:
                return
            plan = delivered
        if not await self._delivery_gate(plan):
            return
        if plan.is_last_batch:
            completed = plan.complete()
            if await self._settle(plan, completed):
                await self._notify_plan_completed(completed.to_view())
            return
        advanced = plan.advance()
        if not await self._settle(plan, advanced):
            return
        await self._assign_batch(advanced, advanced.current_batch_index, key_prefix=str(plan.id))

    async def _deliver_batch(self, plan: ExecutionPlan) -> ExecutionPlan | None:
        """Hand the batch to delivery; None when delivery refused it.

        Defect A-19's silent twin. ``_candidates_for_batch`` refuses to publish
        a candidate whose Runner evidence carries no test results — the one
        honest step in that whole chain, and it stays refused here. But the
        refusal was a bare exception thrown out of this method into the Runner
        ingest's best-effort handler, which logs and returns so that a
        scheduling failure never fails ingestion. The consequence was a round
        that refused itself several times a minute in silence: no state
        written, nothing projected, every task green in the console and a
        change set that stayed empty forever.

        So the refusal is caught and *recorded on the round*, and only then
        does this return without advancing. Nothing is weakened: no candidate
        is published, no batch is advanced, no plan is completed. The refusal
        merely stops being invisible.

        Convergence is the other half and needs no new trigger. Every terminal
        Runner event re-enters ``on_task_terminal`` and every delivery
        observation re-enters ``reconsider_task``; both land here. When a
        re-dispatched Worker finally reports evidence that carries test
        results, this call succeeds, the recorded refusal is cleared, and the
        ordinary advance continues from the returned plan. A repeated refusal
        writes nothing (see ``ExecutionPlan.refuse_delivery``), so a stuck
        round costs one row, not one row per event.

        An error that is *not* a stated refusal is re-raised unchanged: an
        adapter that cannot reach GitHub is a fault, not a judgement, and
        recording it as delivery's verdict on the evidence would be inventing
        a reason.
        """

        assert self._on_batch_deliver is not None  # noqa: S101 - guarded by the caller
        try:
            await self._on_batch_deliver(plan.to_view())
        except BatchDeliveryRefused as refusal:
            outcome = plan.refuse_delivery(
                refusal.reason,
                repository_id=refusal.repository_id,
                task_id=refusal.task_id,
            )
            if outcome.plan is not None:
                await self._settle(plan, outcome.plan)
            _logger.warning(
                "Delivery refused batch %d of execution plan %s: %s",
                plan.current_batch_index,
                plan.id,
                refusal.reason,
            )
            return None
        cleared = plan.clear_delivery_refusal()
        if cleared is None:
            return plan
        if not await self._settle(plan, cleared):
            return None
        return cleared

    async def _delivery_gate(self, plan: ExecutionPlan) -> bool:
        """True when every repository of the current batch is already merged."""
        if self._delivery_state is None:
            return True
        states = {
            item.repository_id: item.merged
            for item in await self._delivery_state.repository_states(plan.project_id)
        }
        return all(
            states.get(planned.repository_id, False)
            for planned in plan.batches[plan.current_batch_index]
        )

    async def _assign_batch(
        self, plan: ExecutionPlan, batch_index: int, *, key_prefix: str
    ) -> ExecutionPlan:
        """Assign the batch's leader tasks, then expand each into executable work.

        The expansion forks on the team's decomposition mode (adjudication
        D-2). ``SERVER`` is today's path, unchanged to the byte: the leader
        task is decomposed in the same breath as it is assigned. ``LEADER``
        **stops** after the leader task — no worker task, no decomposer call —
        records the assignment for the leader-actions surface and tells the
        leader where to plan. The batch is then genuinely parked: nothing
        moves it until the leader submits a plan.

        The fork is in the second loop, not the first, because a leader task
        is assigned identically either way; what differs is only what happens
        to it afterwards.
        """

        leader_task_ids: list[UUID] = []
        teams: list[RepositoryTeamView] = []
        for planned in plan.batches[batch_index]:
            team = await self._decomposer.repository_team(plan.project_id, planned.repository_id)
            teams.append(team)
            leader_task = await self._assigner.assign(
                AssignTaskCommand(
                    organization_id=plan.organization_id,
                    project_id=plan.project_id,
                    repository_id=planned.repository_id,
                    assigned_by_agent_id=plan.created_by_agent_id,
                    assignee_agent_id=team.leader_agent_id,
                    title=planned.title,
                    instruction=planned.instruction,
                    acceptance=planned.acceptance,
                ),
                idempotency_key=f"{key_prefix}:b{batch_index}:{planned.repository_id}",
            )
            leader_task_ids.append(leader_task.id)
        assigned = plan.with_leader_tasks(batch_index, tuple(leader_task_ids))
        await self._plans.update(assigned, expected_version=plan.version)
        for planned, leader_task_id, team in zip(
            assigned.batches[batch_index], leader_task_ids, teams, strict=True
        ):
            key = f"{key_prefix}:b{batch_index}:{planned.repository_id}"
            if await self._decomposes_leader_side(plan.project_id, planned.repository_id):
                await self._park_for_leader(plan, planned, leader_task_id, team, key=key)
                continue
            await self._decomposer.execute(
                leader_task_id,
                idempotency_key=f"{key}:decompose",
                tests=planned.tests,
                test_paths=planned.test_paths,
            )
        return assigned

    async def _decomposes_leader_side(self, project_id: UUID, repository_id: UUID) -> bool:
        """Does this team's own leader decompose, or does the platform?

        With no lane wired there is no leader-actions surface to park a batch
        for, so the answer is the historical one and no read is made at all.
        """

        if self._leader_lane is None:
            return False
        mode = await self._leader_lane.modes.decomposition_mode(project_id, repository_id)
        return mode is TeamDecompositionMode.LEADER

    async def _park_for_leader(
        self,
        plan: ExecutionPlan,
        planned: PlannedRepositoryTask,
        leader_task_id: UUID,
        team: RepositoryTeamView,
        *,
        key: str,
    ) -> None:
        """Record the parked assignment and tell the leader it is waiting.

        The roster and the envelope are derived here, once, and stored: they
        are the bounds the leader plans inside and the ones the server will
        clamp its submission against, so re-deriving them later is how the two
        drift apart. ``ensure`` keeps the first write, which is what makes a
        re-run of this batch (``_resume``) a read rather than a new envelope.

        The notification is a second message beside the assignment the
        assigner already delivered, and it is the one that carries the *route*:
        the leader task alone does not say that this team plans leader-side or
        where the decision surface is. It is sent through the ordinary
        collaboration path, so the org-leader → repository-leader pair routes
        it to the team's leader DM room, and it is keyed off the same prefix as
        every other write of this batch — a replay finds the delivered message
        and does not post it twice.
        """

        assert self._leader_lane is not None  # noqa: S101 - guarded by the caller
        roster = await self._decomposer.worker_roster(team)
        await self._leader_lane.assignments.ensure(
            LeaderAssignmentView(
                leader_task_id=leader_task_id,
                organization_id=plan.organization_id,
                project_id=plan.project_id,
                repository_id=planned.repository_id,
                leader_agent_id=team.leader_agent_id,
                phase=LeaderAssignmentPhase.PLANNING,
                safety_envelope=self._decomposer.safety_envelope(
                    roster, tests=planned.tests, test_paths=planned.test_paths
                ),
                worker_roster=roster,
            )
        )
        await self._leader_lane.collaboration.send(
            SendCollaborationMessageCommand(
                organization_id=plan.organization_id,
                project_id=plan.project_id,
                repository_id=planned.repository_id,
                task_id=leader_task_id,
                sender_agent_id=plan.created_by_agent_id,
                recipient_agent_id=team.leader_agent_id,
                kind=CollaborationMessageKind.DECISION,
                subject=f"{planned.title}: awaiting your repository plan",
                body=(
                    "This repository team plans leader-side, so no worker task has been "
                    "created for this round.\n\n"
                    "Read your assignment package, then submit your Engineering Spec, "
                    "task DAG and worker tasks:\n"
                    f"  GET  /api/v1/agent-actions/leader/assignments/{leader_task_id}\n"
                    f"  POST /api/v1/agent-actions/leader/assignments/{leader_task_id}/plan\n\n"
                    "Authenticate with your own external member token. The package names "
                    "your worker roster and the safety envelope your plan is validated "
                    "against; nothing is dispatched until the plan is accepted."
                ),
                correlation_id=leader_task_id,
            ),
            idempotency_key=f"{key}:leader-plan",
        )

    async def _roll_up(self, parent: Task) -> Task:
        if parent.status in FINAL_TASK_STATUSES:
            return parent
        children = await self._tasks.list_by_parent(parent.id)
        if not children:
            return parent
        if await self._open_review_if_leader_side(parent, children):
            # Leader mode: the leader task does not settle itself. It is
            # returned unchanged, which stops ``on_task_terminal`` at its own
            # "not final yet" guard and leaves the batch parked until a verdict
            # arrives through POST /review.
            return parent
        failed = tuple(child for child in children if child.status in _FAILED_TASK_STATUSES)
        if failed:
            updated = parent.report(
                TaskStatus.FAILED,
                f"{len(failed)} of {len(children)} worker tasks did not succeed.",
            )
        elif all(child.status is TaskStatus.SUCCEEDED for child in children):
            updated = parent.report(
                TaskStatus.SUCCEEDED,
                f"All {len(children)} worker tasks succeeded.",
            )
        else:
            return parent
        try:
            await self._tasks.update(updated, expected_version=parent.version)
        except TaskConflict:
            # Another report already moved the parent, so trust the persisted state.
            current = await self._tasks.get(parent.id)
            return current if current is not None else parent
        return updated

    async def _open_review_if_leader_side(self, parent: Task, children: tuple[Task, ...]) -> bool:
        """Turn a finished leader-mode round into a review, and say so.

        Returns True whenever this leader task is one the platform must not
        settle for itself — which is every leader-mode assignment past
        ``planning``, whether or not this particular event completed the round.
        Saying True while work is still in flight is the point: the alternative
        is falling through to the server-side roll-up, and that is exactly the
        automatic termination leader mode exists to remove.

        Server mode reaches none of this. With no lane wired there is no read
        at all, and with a lane wired a server-mode team has no assignment row,
        so the ordinary path is byte-identical to what it was.

        The transition itself is taken once. It fires when no worker task is
        still open, and only out of ``executing`` — a round already in
        ``review_due`` re-enters here on every sibling's terminal event, and
        re-snapshotting would rewrite the evidence a leader may already be
        reading, which is the one thing the snapshot exists to prevent.
        """

        if self._leader_lane is None:
            return False
        assignment = await self._leader_lane.assignments.get(parent.id)
        if assignment is None:
            return False
        if assignment.phase is not LeaderAssignmentPhase.EXECUTING:
            return True
        if any(child.status in _OPEN_TASK_STATUSES for child in children):
            return True
        evidence = LeaderReviewEvidenceView(
            review_revision=assignment.review_revision,
            worker_evidence=tuple(
                _worker_evidence(child.to_view()) for child in children
            ),
        )
        await self._leader_lane.assignments.save(
            replace(
                assignment,
                phase=LeaderAssignmentPhase.REVIEW_DUE,
                review_evidence=evidence,
            )
        )
        await self._notify_review_due(parent, assignment, evidence)
        return True

    async def _notify_review_due(
        self,
        parent: Task,
        assignment: LeaderAssignmentView,
        evidence: LeaderReviewEvidenceView,
    ) -> None:
        """Tell the leader its round is waiting on a verdict.

        The same collaboration path and the same idempotency discipline as the
        planning notification: org-leader → repository-leader routes to the
        team's leader DM room, and the key names the round, so a replay of the
        terminal event that opened review does not post a second time.
        """

        assert self._leader_lane is not None  # noqa: S101 - guarded by the caller
        await self._leader_lane.collaboration.send(
            SendCollaborationMessageCommand(
                organization_id=assignment.organization_id,
                project_id=assignment.project_id,
                repository_id=assignment.repository_id,
                task_id=parent.id,
                sender_agent_id=parent.assigned_by_agent_id,
                recipient_agent_id=assignment.leader_agent_id,
                kind=CollaborationMessageKind.DECISION,
                subject=f"{parent.title}: awaiting your review",
                body=(
                    f"All {len(evidence.worker_evidence)} worker tasks of this round have "
                    "finished, so this repository is waiting on your verdict.\n\n"
                    "Read the evidence, then submit approve, request_rework or escalate:\n"
                    f"  GET  /api/v1/agent-actions/leader/assignments/{parent.id}\n"
                    f"  POST /api/v1/agent-actions/leader/assignments/{parent.id}/review\n\n"
                    "The package carries each worker task's status, run, commit, changed "
                    "files and test results. Nothing is delivered until you approve."
                ),
                correlation_id=parent.id,
            ),
            idempotency_key=(
                f"leader-review:{parent.id}:r{evidence.review_revision}:due"
            ),
        )

    async def _batch_succeeded(self, plan: ExecutionPlan) -> bool:
        for planned in plan.batches[plan.current_batch_index]:
            if planned.leader_task_id is None:
                return False
            leader_task = await self._tasks.get(planned.leader_task_id)
            if leader_task is None or leader_task.status is not TaskStatus.SUCCEEDED:
                return False
        return True

    async def _settle(self, plan: ExecutionPlan, updated: ExecutionPlan) -> bool:
        try:
            await self._plans.update(updated, expected_version=plan.version)
        except TaskConflict:
            # Another caller already moved this plan forward.
            return False
        return True

    async def _notify_plan_completed(self, plan: ExecutionPlanView) -> None:
        if self._on_plan_completed is not None:
            await self._on_plan_completed(plan)


class ReadLeaderAssignment:
    """Hand an external Repository Leader the facts it plans and reviews from.

    The read behind ``GET /agent-actions/leader/assignments/{taskId}``, and
    the whole of the leader-actions surface this slice implements. Every
    refusal it raises carries a frozen code
    (``contracts/leader-actions/v1/fixtures/error-matrix.json``); the HTTP
    layer only maps code to status, so the *judgement* about who may read an
    assignment lives here rather than in a router.

    ``caller_agent_id`` is derived by the caller from the presented token and
    is never taken from a path or a body — the token names the subject
    (adjudication D-6). This use case therefore treats it as established
    identity and answers only the authorization questions: is this principal a
    Repository Leader, does this assignment exist, is this principal *its*
    assignee, and is this team actually planning leader-side.

    The order of those four is deliberate. Role comes first because it is
    decidable without touching any row, so a worker's token learns nothing
    about which task ids exist. Existence comes before assignee because there
    is no way to know who the assignee is without the row. Mode comes last
    because it is a statement about a real assignment the caller really owns —
    ``decomposition_mode_conflict`` tells a leader that this team's planning is
    done server-side, which would be a fact worth leaking if it were said to
    anyone else.
    """

    def __init__(
        self,
        assignments: LeaderAssignmentStore,
        tasks: TaskStore,
        directory: AgentPrincipalReader,
        modes: TeamDecompositionModeReader,
    ) -> None:
        self._authorizer = LeaderAssignmentAuthorizer(assignments, tasks, directory, modes)

    async def execute(
        self, leader_task_id: UUID, *, caller_agent_id: UUID
    ) -> RepositoryAssignmentPackage:
        assignment, task = await self._authorizer.resolve(
            leader_task_id, caller_agent_id=caller_agent_id
        )
        return RepositoryAssignmentPackage(
            assignment=assignment,
            repository_task=RepositoryTaskFactsView(
                title=task.title,
                instruction=task.instruction,
                # The wire carries one text; the row carries the criteria as
                # separate lines and joining them is the only lossless way to
                # spend a single string on them.
                acceptance="\n".join(task.acceptance),
            ),
        )


class LeaderAssignmentAuthorizer:
    """Who may act on one leader assignment, asked once for all three endpoints.

    Every request to this surface asks the same four questions in the same
    order, and answering them in three places is how two of them would one day
    answer differently. Extracted rather than duplicated for that reason alone;
    the order itself is unchanged and still deliberate:

    Role comes first because it is decidable without touching any row, so a
    worker's token learns nothing about which task ids exist. Existence comes
    before assignee because there is no way to know who the assignee is without
    the row. Mode comes last because it is a statement about a real assignment
    the caller really owns — ``decomposition_mode_conflict`` tells a leader that
    this team's planning is done server-side, which would be a fact worth
    leaking if it were said to anyone else.

    ``caller_agent_id`` is derived by the API layer from the presented token and
    never taken from a path or a body: the token names the subject
    (adjudication D-6), so this treats it as established identity.
    """

    def __init__(
        self,
        assignments: LeaderAssignmentStore,
        tasks: TaskStore,
        directory: AgentPrincipalReader,
        modes: TeamDecompositionModeReader,
    ) -> None:
        self._assignments = assignments
        self._tasks = tasks
        self._directory = directory
        self._modes = modes

    async def resolve(
        self, leader_task_id: UUID, *, caller_agent_id: UUID
    ) -> tuple[LeaderAssignmentView, Task]:
        caller = await self._directory.get_view(caller_agent_id)
        if (
            caller is None
            or caller.status is not AgentPrincipalStatus.ACTIVE
            or caller.role is not AgentRole.REPOSITORY_LEADER
        ):
            raise LeaderActionRefused(
                LeaderActionErrorCode.FORBIDDEN_ROLE,
                "this credential does not belong to an active repository leader",
            )
        assignment = await self._assignments.get(leader_task_id)
        task = await self._tasks.get(leader_task_id)
        if assignment is None or task is None:
            # A task with no assignment is the ordinary case — every
            # server-mode task in the installation is one — and an assignment
            # with no task is a broken invariant. Neither is an assignment
            # this surface can answer for, and saying which is which would
            # turn the endpoint into a task-existence oracle.
            raise LeaderActionRefused(
                LeaderActionErrorCode.ASSIGNMENT_NOT_FOUND,
                "no leader assignment exists for this task id",
            )
        if task.assignee_agent_id != caller_agent_id:
            raise LeaderActionRefused(
                LeaderActionErrorCode.FORBIDDEN_NOT_ASSIGNEE,
                "this leader assignment belongs to another repository leader",
            )
        mode = await self._modes.decomposition_mode(
            assignment.project_id, assignment.repository_id
        )
        if mode is not TeamDecompositionMode.LEADER:
            raise LeaderActionRefused(
                LeaderActionErrorCode.DECOMPOSITION_MODE_CONFLICT,
                "this team's planning is done server-side, so leader submissions are refused",
            )
        return assignment, task


def _plan_fingerprint(decision: RepositoryPlanDecision) -> str:
    return _document_fingerprint(decision.raw)


def _review_fingerprint(decision: RepositoryReviewDecision) -> str:
    return _document_fingerprint(decision.raw)


def _document_fingerprint(document: dict[str, object]) -> str:
    """A stable identity for one submitted document.

    ``sort_keys`` because two submissions of the same plan are the same plan
    whatever order a client serialised its object keys in — JSON objects are
    unordered, and a fingerprint that said otherwise would turn an honest
    idempotent retry into a ``phase_conflict``.
    """

    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


class SubmitRepositoryPlan:
    """Accept a Repository Leader's own plan, or refuse it with a frozen code.

    The server does not author any of what arrives here: the Engineering Spec,
    the DAG and the worker task drafts are the leader's products and are
    persisted verbatim. What the server does is *clamp* — five families of
    check, each with its own frozen code — and then create the worker tasks
    through the ordinary assignment path, so a leader-planned task is
    dispatched, published and permitted exactly like a server-planned one.

    **The envelope is read, never re-derived.** ``safety_envelope`` comes off
    the stored assignment, which is the copy the leader was handed when the
    batch parked. Re-deriving it here would let a worker's responsibility paths
    change while the leader was planning and reject a submission that was
    inside its bounds when it was made.

    **Idempotency is decided before the phase is.** An accepted plan moves the
    assignment to ``executing``, so a leader whose 200 was lost and who retries
    would meet a phase check that says ``planning`` is over. The contract says
    that retry must get its receipt back, so the fingerprint is consulted first
    and the phase check only guards submissions that are genuinely new.
    """

    def __init__(
        self,
        assignments: LeaderAssignmentStore,
        tasks: TaskStore,
        directory: AgentPrincipalReader,
        modes: TeamDecompositionModeReader,
        assigner: TaskAssignmentGateway,
        spec_author: TaskSpecificationAuthor | None = None,
    ) -> None:
        self._authorizer = LeaderAssignmentAuthorizer(assignments, tasks, directory, modes)
        self._assignments = assignments
        self._directory = directory
        self._assigner = assigner
        self._spec_author = spec_author

    async def execute(
        self,
        leader_task_id: UUID,
        decision: RepositoryPlanDecision,
        *,
        caller_agent_id: UUID,
    ) -> PlanReceipt:
        assignment, task = await self._authorizer.resolve(
            leader_task_id, caller_agent_id=caller_agent_id
        )
        fingerprint = _plan_fingerprint(decision)
        if assignment.accepted_plan is not None:
            if assignment.accepted_plan.fingerprint == fingerprint:
                return PlanReceipt(
                    leader_task_id=leader_task_id,
                    plan_revision=assignment.accepted_plan.plan_revision,
                    worker_task_ids=assignment.accepted_plan.worker_task_ids,
                )
            raise LeaderActionRefused(
                LeaderActionErrorCode.PHASE_CONFLICT,
                "a different plan has already been accepted for this leader task",
            )
        if assignment.phase is not LeaderAssignmentPhase.PLANNING:
            raise LeaderActionRefused(
                LeaderActionErrorCode.PHASE_CONFLICT,
                f"a plan may only be submitted while planning; this assignment is "
                f"{assignment.phase.value}",
            )

        _assert_plan_is_within_envelope(decision, assignment)

        worker_task_ids: list[UUID] = []
        for draft in decision.worker_tasks:
            # Keyed on the leader task and the node, not on the position in the
            # list: a node is the leader's own name for one piece of work, and
            # the coverage check above has already made those names unique. A
            # replay that reached this loop halfway therefore finds the tasks it
            # already created rather than making a second set beside them.
            key = f"leader-plan:{leader_task_id}:{draft.node_id}"
            worker_task = await self._assigner.assign(
                AssignTaskCommand(
                    organization_id=assignment.organization_id,
                    project_id=assignment.project_id,
                    repository_id=assignment.repository_id,
                    assigned_by_agent_id=assignment.leader_agent_id,
                    assignee_agent_id=draft.assignee_worker_agent_id,
                    title=draft.title,
                    instruction=draft.instruction,
                    acceptance=task.acceptance,
                    parent_task_id=leader_task_id,
                ),
                idempotency_key=key,
                origin=task.origin,
                # Row, permit, then the room — never the room first. See
                # ``TaskOrchestrator.deliver_assignment`` for what a message
                # that outruns its permit costs (defect S-1).
                deliver=False,
            )
            await self._author_permit(worker_task, draft, key=key)
            await self._assigner.deliver_assignment(worker_task.id)
            worker_task_ids.append(worker_task.id)

        receipt = PlanReceipt(
            leader_task_id=leader_task_id,
            # 1, and only ever 1 today. The contract reserves growth to the
            # formal rework path, and rework returns the assignment to
            # ``executing`` rather than to ``planning`` — so nothing in this
            # slice can reach a second plan submission, and a number that
            # counted resubmissions would be exactly the thing the receipt's
            # own description forbids.
            plan_revision=1,
            worker_task_ids=tuple(worker_task_ids),
        )
        await self._assignments.save(
            replace(
                assignment,
                phase=LeaderAssignmentPhase.EXECUTING,
                accepted_plan=AcceptedPlanView(
                    fingerprint=fingerprint,
                    plan_revision=receipt.plan_revision,
                    worker_task_ids=receipt.worker_task_ids,
                    decision=decision.raw,
                ),
            )
        )
        return receipt

    async def _author_permit(
        self, worker_task: TaskView, draft: LeaderWorkerTaskDraft, *, key: str
    ) -> None:
        """The execution permit, carrying the leader's own paths and tests.

        This is the one place a leader-planned task differs from a
        server-planned one, and it is the point of the whole slice: the allowed
        paths are the leader's ``allowedPaths`` rather than
        ``derive_allowed_paths`` over the worker's responsibility, and the tests
        are the leader's ``tests``. Both have already been clamped — paths to
        the stored envelope's roots, tests to a superset of its commands — so
        what reaches the permit is narrower than what the server would have
        derived, never wider.
        """

        if self._spec_author is None:
            return
        worker = await self._directory.get_view(worker_task.assignee_agent_id)
        if worker is None or worker.status is not AgentPrincipalStatus.ACTIVE:
            raise TaskDenied("worker agent is missing or disabled")
        await self._spec_author.ensure_approved(
            worker_task,
            allowed_paths=draft.allowed_paths,
            tests=draft.tests,
            idempotency_key=f"{key}:spec",
        )


def _assert_plan_is_within_envelope(
    decision: RepositoryPlanDecision, assignment: LeaderAssignmentView
) -> None:
    """The five clamp families, in the order their codes are decided.

    Order is a contract-visible choice, because a plan can break more than one
    rule and only one code comes back. It runs from "is this a leader's product
    at all" outward to "is each task inside its bounds", so the code a leader
    receives names the outermost thing wrong with the submission rather than an
    incidental consequence of it:

    1. provenance — a plan without it is not the leader's product, and nothing
       further about it is worth reporting;
    2. DAG coverage — nodes and worker tasks must correspond one-to-one and
       edges must name declared nodes, because a cycle check over a graph whose
       nodes are not settled would be answering about a different graph;
    3. DAG cycle;
    4. assignee, then allowed paths, then tests — per worker task, in the order
       the frozen invariant lists them.
    """

    if decision.provenance.source != LEADER_PROVENANCE_SOURCE:
        raise LeaderActionRefused(
            LeaderActionErrorCode.PLAN_INVALID_PROVENANCE,
            "a repository plan must carry leader-codex-session provenance",
        )

    nodes = set(decision.nodes)
    if len(nodes) != len(decision.nodes):
        raise LeaderActionRefused(
            LeaderActionErrorCode.PLAN_INVALID_DAG_COVERAGE,
            "the task DAG declares the same node more than once",
        )
    claimed = [draft.node_id for draft in decision.worker_tasks]
    if len(set(claimed)) != len(claimed) or set(claimed) != nodes:
        raise LeaderActionRefused(
            LeaderActionErrorCode.PLAN_INVALID_DAG_COVERAGE,
            "every DAG node must be named by exactly one worker task, and vice versa",
        )
    for source, target in decision.edges:
        if source not in nodes or target not in nodes:
            raise LeaderActionRefused(
                LeaderActionErrorCode.PLAN_INVALID_DAG_COVERAGE,
                "a DAG edge references a node the plan does not declare",
            )
    if _has_cycle(decision.nodes, decision.edges):
        raise LeaderActionRefused(
            LeaderActionErrorCode.PLAN_INVALID_DAG_CYCLE,
            "the task DAG contains a cycle, so no execution order exists",
        )

    roster = {entry.worker_agent_id for entry in assignment.worker_roster}
    envelope = assignment.safety_envelope
    for draft in decision.worker_tasks:
        if draft.assignee_worker_agent_id not in roster:
            raise LeaderActionRefused(
                LeaderActionErrorCode.PLAN_INVALID_ASSIGNEE,
                "a worker task is assigned to an agent outside this team's roster",
            )
        for path in draft.allowed_paths:
            if not _within_roots(path, envelope.allowed_path_roots):
                raise LeaderActionRefused(
                    LeaderActionErrorCode.PLAN_INVALID_ALLOWED_PATHS,
                    "a worker task's allowed paths fall outside the safety envelope",
                )
        missing = set(envelope.test_commands) - set(draft.tests)
        if missing:
            raise LeaderActionRefused(
                LeaderActionErrorCode.PLAN_INVALID_TESTS_REMOVED,
                "a worker task drops a verification command the envelope requires",
            )


class SubmitRepositoryReview:
    """Accept a Repository Leader's verdict over the round's own evidence.

    Three outcomes, and each one is reached through a mechanism that already
    exists rather than a new one:

    *approve* reports the leader task SUCCEEDED through the ordinary
    ``TaskReportGateway``, so the leader's ``summary`` becomes the roll-up body
    the Organization Manager reads and the delivery gate — which has always
    required a SUCCEEDED leader task — starts taking candidates without a line
    of delivery code changing.

    *escalate* reports it BLOCKED through the same gateway, which is what
    triggers the existing EXCEPTION_ESCALATION checkpoint: that gateway already
    raises one whenever a repository leader reports BLOCKED or FAILED. BLOCKED
    is deliberately not a final status, so the plan stalls for an operator
    rather than failing itself (AC-07: a leader that cannot finish is not the
    same as a round that failed).

    *request_rework* creates new revision worker tasks through the formal
    assignment path and leaves every already-terminal worker task untouched.
    The assignment returns to ``executing`` and its review revision advances,
    so the next round's evidence is a new snapshot and the next verdict has its
    own idempotency key.

    Nothing here reads a worker task's live state to decide: the verdict is
    judged against the stored evidence snapshot, which is what makes it
    attributable to what the leader was actually shown.
    """

    def __init__(
        self,
        assignments: LeaderAssignmentStore,
        tasks: TaskStore,
        directory: AgentPrincipalReader,
        modes: TeamDecompositionModeReader,
        assigner: TaskAssignmentGateway,
        reporter: TaskReportGateway,
        spec_author: TaskSpecificationAuthor | None = None,
        on_leader_task_terminal: Callable[[UUID], Awaitable[None]] | None = None,
    ) -> None:
        self._authorizer = LeaderAssignmentAuthorizer(assignments, tasks, directory, modes)
        self._assignments = assignments
        self._directory = directory
        self._assigner = assigner
        self._reporter = reporter
        self._spec_author = spec_author
        # The same collaborator the Runner gateway holds, for the same reason:
        # a leader task reaching a terminal status is what lets the execution
        # plan advance, and in leader mode this is the only place that happens.
        self._on_leader_task_terminal = on_leader_task_terminal

    async def execute(
        self,
        leader_task_id: UUID,
        decision: RepositoryReviewDecision,
        *,
        caller_agent_id: UUID,
    ) -> ReviewReceipt:
        assignment, task = await self._authorizer.resolve(
            leader_task_id, caller_agent_id=caller_agent_id
        )
        fingerprint = _review_fingerprint(decision)
        # The revision is part of the idempotency key, so a replay is looked up
        # under the round it judged rather than under the round now open.
        for accepted in assignment.accepted_reviews:
            if accepted.fingerprint == fingerprint:
                return ReviewReceipt(
                    leader_task_id=leader_task_id,
                    verdict=LeaderReviewVerdict(accepted.verdict),
                    review_revision=accepted.review_revision,
                    leader_task_status=accepted.leader_task_status,
                    rework_task_ids=accepted.rework_task_ids,
                )
        if assignment.accepted_review(assignment.review_revision) is not None:
            raise LeaderActionRefused(
                LeaderActionErrorCode.PHASE_CONFLICT,
                "a different verdict has already been accepted for this review round",
            )
        if (
            assignment.phase is not LeaderAssignmentPhase.REVIEW_DUE
            or assignment.review_evidence is None
        ):
            raise LeaderActionRefused(
                LeaderActionErrorCode.PHASE_CONFLICT,
                f"a review may only be submitted when review is due; this assignment is "
                f"{assignment.phase.value}",
            )

        _assert_review_is_answerable(decision, assignment.review_evidence)

        revision = assignment.review_evidence.review_revision
        if decision.verdict is LeaderReviewVerdict.REQUEST_REWORK:
            rework_task_ids = await self._create_rework(assignment, task, decision, revision)
            updated = replace(
                assignment,
                phase=LeaderAssignmentPhase.EXECUTING,
                review_revision=revision + 1,
                # Cleared, not kept: the next round is judged against its own
                # snapshot, and leaving this one in place would let a stale
                # package answer a GET during the new round.
                review_evidence=None,
            )
            status = TaskStatus.IN_PROGRESS.value
        else:
            reported = (
                TaskStatus.SUCCEEDED
                if decision.verdict is LeaderReviewVerdict.APPROVE
                else TaskStatus.BLOCKED
            )
            if task.status in FINAL_TASK_STATUSES:
                if task.status is not reported:
                    raise LeaderActionRefused(
                        LeaderActionErrorCode.PHASE_CONFLICT,
                        f"the leader task has already finished as {task.status.value}, "
                        f"so it cannot accept a {decision.verdict.value} review",
                    )
                # Recovery from a partial earlier attempt: the task result is
                # immutable, but its assignment can still be review_due when
                # reporting settled the task before the accepted review was
                # saved.  Preserve that result and finish the pending review;
                # reporting again with a newly generated summary would turn a
                # recoverable retry into TaskConflict.
            else:
                await self._reporter.report(
                    ReportTaskCommand(
                        task_id=leader_task_id,
                        reporter_agent_id=assignment.leader_agent_id,
                        status=reported,
                        summary=decision.summary,
                    ),
                    idempotency_key=f"leader-review:{leader_task_id}:r{revision}:report",
                )
            rework_task_ids = ()
            updated = replace(assignment, phase=LeaderAssignmentPhase.CLOSED)
            status = reported.value

        receipt = ReviewReceipt(
            leader_task_id=leader_task_id,
            verdict=decision.verdict,
            review_revision=revision,
            leader_task_status=status,
            rework_task_ids=rework_task_ids,
        )
        await self._assignments.save(
            replace(
                updated,
                accepted_reviews=(
                    *assignment.accepted_reviews,
                    AcceptedReviewView(
                        fingerprint=fingerprint,
                        verdict=receipt.verdict.value,
                        review_revision=receipt.review_revision,
                        leader_task_status=receipt.leader_task_status,
                        rework_task_ids=receipt.rework_task_ids,
                    ),
                ),
            )
        )
        if (
            decision.verdict is LeaderReviewVerdict.APPROVE
            and self._on_leader_task_terminal is not None
        ):
            # Only after the assignment is saved: the advance may deliver the
            # batch, and a delivery that ran against an assignment still
            # recorded as review_due would be reading a state that no longer
            # matches the task it is delivering for.
            await self._on_leader_task_terminal(leader_task_id)
        return receipt

    async def _create_rework(
        self,
        assignment: LeaderAssignmentView,
        task: Task,
        decision: RepositoryReviewDecision,
        revision: int,
    ) -> tuple[UUID, ...]:
        """One new worker task per actionable finding, through the formal path.

        ``TaskOrigin.REWORK`` is the existing vocabulary and it fits: the enum
        says "created to repair", the read model already counts such a task as
        an attempt beyond the first and displays its repository as repairing,
        and a leader-requested repair is exactly that. Inventing a third origin
        would leave every one of those consumers unable to see this task.

        Already-terminal worker tasks are not touched (frozen invariant 5). The
        repair is a *new* task carrying the leader's instruction, so the
        evidence the verdict was based on stays exactly as it was reviewed.
        """

        created: list[UUID] = []
        evidence_by_task = {
            item.worker_task_id: item
            for item in (
                assignment.review_evidence.worker_evidence
                if assignment.review_evidence is not None
                else ()
            )
        }
        for index, finding in enumerate(decision.rework_findings):
            reviewed = evidence_by_task[finding.worker_task_id]
            key = f"leader-review:{assignment.leader_task_id}:r{revision}:{index}"
            worker_task = await self._assigner.assign(
                AssignTaskCommand(
                    organization_id=assignment.organization_id,
                    project_id=assignment.project_id,
                    repository_id=assignment.repository_id,
                    assigned_by_agent_id=assignment.leader_agent_id,
                    assignee_agent_id=reviewed.worker_agent_id,
                    title=task.title,
                    instruction=(
                        f"{finding.rework_instruction}\n\n"
                        f"Review finding on task {finding.worker_task_id}: {finding.note}"
                    ),
                    acceptance=task.acceptance,
                    parent_task_id=assignment.leader_task_id,
                ),
                idempotency_key=key,
                origin=TaskOrigin.REWORK,
                # A repair is announced only once its own permit exists, for
                # the same reason the plan's tasks are (defect S-1).
                deliver=False,
            )
            await self._author_rework_permit(assignment, worker_task, key=key)
            await self._assigner.deliver_assignment(worker_task.id)
            created.append(worker_task.id)
        return tuple(created)

    async def _author_rework_permit(
        self, assignment: LeaderAssignmentView, worker_task: TaskView, *, key: str
    ) -> None:
        """The repair's permit, bounded by the stored envelope.

        The leader named an instruction, not a path list, so the permit falls
        back to the envelope's own roots and commands — the widest thing the
        round was ever allowed and the same bound the original plan was clamped
        to. A repair cannot therefore reach further than the work it repairs.
        """

        if self._spec_author is None:
            return
        worker = await self._directory.get_view(worker_task.assignee_agent_id)
        if worker is None or worker.status is not AgentPrincipalStatus.ACTIVE:
            raise TaskDenied("worker agent is missing or disabled")
        await self._spec_author.ensure_approved(
            worker_task,
            allowed_paths=assignment.safety_envelope.allowed_path_roots,
            tests=assignment.safety_envelope.test_commands,
            idempotency_key=f"{key}:spec",
        )


def _assert_review_is_answerable(
    decision: RepositoryReviewDecision, evidence: LeaderReviewEvidenceView
) -> None:
    """Frozen invariant 5's review half: findings must be actionable and real.

    A ``request_rework`` with nothing to act on would move the assignment back
    to ``executing`` with no work in flight — a round parked forever with no
    signal that anything is wrong. A finding naming a task outside the evidence
    is a verdict about something the leader was not shown, which is the one
    thing the evidence snapshot exists to prevent.
    """

    reviewed = {item.worker_task_id for item in evidence.worker_evidence}
    for finding in decision.findings:
        if finding.worker_task_id not in reviewed:
            raise LeaderActionRefused(
                LeaderActionErrorCode.REVIEW_INVALID_FINDINGS,
                "a finding names a worker task outside this round's evidence",
            )
    if decision.verdict is LeaderReviewVerdict.REQUEST_REWORK and not decision.rework_findings:
        raise LeaderActionRefused(
            LeaderActionErrorCode.REVIEW_INVALID_FINDINGS,
            "requesting rework needs at least one finding carrying a rework instruction",
        )


def _within_roots(path: str, roots: tuple[str, ...]) -> bool:
    """Is this path under one of the envelope's roots?

    Prefix matching on the normalised text, because the envelope's roots are
    the same glob-ish path strings ``derive_allowed_paths`` produces and the
    permit renders — comparing them as filesystem paths would resolve against a
    working directory this process does not have and the leader never sees.

    A trailing ``**`` is stripped from a root before comparison: the envelope
    spells a directory root either way, and a leader that wrote the plainer
    form must not be refused for it.
    """

    candidate = path.strip().lstrip("./")
    for root in roots:
        normalised = root.strip().lstrip("./").removesuffix("**").removesuffix("*")
        if not normalised or candidate.startswith(normalised):
            return True
    return False


def _has_cycle(nodes: tuple[str, ...], edges: tuple[tuple[str, str], ...]) -> bool:
    """Kahn's algorithm: a graph with a cycle cannot be fully ordered."""

    outgoing: dict[str, list[str]] = {node: [] for node in nodes}
    indegree = dict.fromkeys(nodes, 0)
    for source, target in edges:
        outgoing[source].append(target)
        indegree[target] += 1
    ready = [node for node, degree in indegree.items() if degree == 0]
    ordered = 0
    while ready:
        node = ready.pop()
        ordered += 1
        for target in outgoing[node]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    return ordered != len(nodes)


class RedispatchRound:
    """Tell a round's idle Workers again (defect A-13, contract v0.4 §8.7.4).

    The defect this exists for, stated as adjudicated: 派工是一次性事件、不是可
    收敛的状态——agent 那一个回合没干成,控制台就永远停在 running,没有任何一层
    会重试、报警或让人在 GUI 上重发。

    A round's dispatch happens once: the package is written to shared storage
    and a mention is posted to the room, and from there the Worker's single
    react turn is the whole of the mechanism. If that turn does not finish the
    work — the mention is consumed by a container that then dies, the pull of
    the package is refused, the mention is delivered to a room whose recipient
    does not exist yet — nothing downstream notices. The tasks stay ``running``
    and the Workers stay idle, indefinitely, because "dispatched" was recorded
    as an event that happened rather than as a state to be reached.

    This is the operator's convergence handle, and deliberately only that:
    there is no timer and no automatic retry in this iteration. A person who
    can see that a round is stuck presses a button, and every non-terminal task
    of the round is dispatched again — the same package, republished
    idempotently, and the same mention, re-posted as a *new* Matrix event.

    Two things it is careful not to be:

    *Not a second assignment.* No task is created, no plan is advanced, no
    status is written. A task that is genuinely in flight receives a duplicate
    notification, which is the honest cost and is what the confirm dialog says.

    *Not a judgement.* Nothing here decides whether a round is "stuck" — that
    inference would be wrong the moment a Worker is merely slow. The capability
    is offered whenever there is a non-terminal task to dispatch and refused
    when there is not, and the operator supplies the judgement.
    """

    def __init__(
        self,
        plans: ExecutionPlanStore,
        tasks: TaskStore,
        dispatcher: TaskRedispatchGateway,
        assignments: LeaderAssignmentStore | None = None,
    ) -> None:
        self._plans = plans
        self._tasks = tasks
        self._dispatcher = dispatcher
        self._assignments = assignments

    async def execute(
        self,
        round_id: UUID,
        *,
        attempt: str,
        scope: RedispatchScope = RedispatchScope.UNFINISHED,
    ) -> RoundRedispatch:
        token = attempt.strip()
        if not token:
            raise ValueError("attempt is required")
        plan = await self._plans.get(round_id)
        if plan is None:
            raise TaskNotFound(f"round does not exist: {round_id}")

        pending, settled = await self._partition(plan)
        # ``rerun`` reaches the finished tasks too — the shape where the result
        # on file is the problem. Only the ones that may be sent back to work:
        # a CANCELLED or SUPERSEDED task is a decision, not a bad result, and
        # ``Task.redo`` refuses it, so it must not be queued here either.
        review_rounds: dict[UUID, LeaderAssignmentView] = {}
        if scope is RedispatchScope.RERUN and self._assignments is not None:
            for task in (*pending, *settled):
                assignment = await self._assignments.get(task.id)
                if assignment is not None and assignment.phase in {
                    LeaderAssignmentPhase.REVIEW_DUE,
                    LeaderAssignmentPhase.CLOSED,
                }:
                    review_rounds[task.id] = assignment

        # A leader-mode parent is the stable identity of the accepted plan. A
        # stale planning notice cannot reopen it: the Bridge reads the durable
        # assignment phase first and discards that notice once the assignment
        # has moved past ``planning``. Re-running the parent would therefore
        # leave an ASSIGNED leader task nobody can finish. Keep that task and
        # its accepted plan settled; the workers are the results being redone,
        # and the assignment is moved to a fresh review round below.
        if review_rounds:
            pending = [task for task in pending if task.id not in review_rounds]
        redoable = (
            [
                task
                for task in settled
                if task.status in _REDOABLE_TASK_STATUSES
                and task.id not in review_rounds
            ]
            if scope is RedispatchScope.RERUN
            else []
        )
        if not pending and not redoable and not settled:
            # A plan row with no task rows under it: materialization wrote the
            # round and stopped before it wrote any work. Re-dispatch has
            # nothing to re-send, and answering 200 would be a lie the operator
            # would act on by waiting. The button that finishes this round is
            # materialize, and the sentence says so.
            raise RoundNotDispatchable(
                f"round {round_id} has no tasks yet; it was never fully "
                "materialised, so there is nothing to dispatch again — "
                "materialize the issue instead"
            )
        if not pending and not redoable:
            raise RoundNotDispatchable(
                f"every task of round {round_id} has already finished "
                f"({len(settled)} task(s)); a finished round is history, not "
                "unfinished work — ask for scope=rerun if a result on file is "
                "the thing that is wrong"
            )

        # Reset before telling any Worker. A very fast report must see
        # ``executing`` and be allowed to open review_due; doing this after the
        # messages would leave a race where every Worker finished against the
        # old closed assignment and no review notice was emitted. The phase
        # guard makes an ambiguous retry converge: once this save lands, the
        # same request observes EXECUTING and does not increment again.
        for assignment in review_rounds.values():
            await self._assignments.save(  # type: ignore[union-attr]
                replace(
                    assignment,
                    phase=LeaderAssignmentPhase.EXECUTING,
                    review_revision=assignment.review_revision + 1,
                    review_evidence=None,
                )
            )

        dispatched: list[UUID] = []
        for task in pending:
            await self._dispatcher.redispatch(task.id, attempt=token)
            dispatched.append(task.id)
        reopened: list[UUID] = []
        for task in redoable:
            await self._dispatcher.redispatch(task.id, attempt=token, redo=True)
            dispatched.append(task.id)
            reopened.append(task.id)
        untouched = {task.id for task in settled} - set(reopened)
        return RoundRedispatch(
            round_id=round_id,
            attempt=token,
            scope=scope,
            task_ids=tuple(dispatched),
            reopened_task_ids=tuple(reopened),
            settled_task_ids=tuple(task.id for task in settled if task.id in untouched),
        )

    async def _partition(self, plan: ExecutionPlan) -> tuple[list[Task], list[Task]]:
        """The round's tasks, split into what may still be told and what is done.

        Walked from the plan rather than from the project, because "this
        round's tasks" is a question only the plan can answer: a project
        accumulates rounds, and re-dispatching a previous round's stragglers
        from this round's button would be a surprise.

        Every batch is walked, not only the current one. Later batches have no
        leader task ids yet and contribute nothing; earlier ones only advance
        once they succeeded, so they contribute nothing either. Walking all of
        them costs a few lookups and saves a special case that would be wrong
        the first time a batch advanced over a partially-settled repository.

        Leaders are included alongside their Workers. A leader task is
        dispatched the same way — a room message it is expected to act on — and
        the live billing specimen is precisely a leader whose mention never
        arrived, so excluding them would leave one of the three shapes alive.
        """

        pending: list[Task] = []
        settled: list[Task] = []
        seen: set[UUID] = set()
        for batch_index in range(len(plan.batches)):
            for leader_task_id in plan.leader_task_ids(batch_index):
                leader = await self._tasks.get(leader_task_id)
                if leader is None:
                    continue
                for task in (leader, *await self._tasks.list_by_parent(leader.id)):
                    if task.id in seen:
                        continue
                    seen.add(task.id)
                    bucket = settled if task.status in FINAL_TASK_STATUSES else pending
                    bucket.append(task)
        return pending, settled
