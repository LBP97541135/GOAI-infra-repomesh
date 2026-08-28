"""Defect S-1: a Worker task's room may not be told before its permit exists.

Found twice on the live Bridge line. The dispatch was one act — write the task
row, publish the package, tell the room — and the execution permit was written
by the *caller*, after that act returned. A Bridge sitting in the room answers
a task assignment immediately by asking the server to start the task; the
server's preflight reads the approved specification, found none, raised
``SpecificationNotFound``, and the Bridge refused without retrying, by design.
The dispatch was then gone: the task stayed blocked and its permit was frozen
into place a moment later, with nothing left for an operator to press.

The fix splits the act in two on the gateway's own interface — ``assign(...,
deliver=False)`` writes the row, ``deliver_assignment`` announces it — so the
three callers that owe a Worker task a permit can write it in between. The
invariant these tests hold is one sentence:

    **the announcement of a Worker task never precedes its execution permit.**

Which is why the collaboration fake here does not merely record a send. It
performs the online Bridge's callback read at exactly the moment the real one
does, and a fake that only counted messages would have watched the defect go
past. Everything below is the real ``TaskOrchestrator``, the real decomposer
and the real leader use cases over in-memory ports; nothing reaches Matrix,
MinIO or a database.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from repomesh.modules.collaboration.contracts import (
    CollaborationRouteUnavailable,
    SendCollaborationMessageCommand,
)
from repomesh.modules.task_orchestration.application import (
    AdvanceExecutionPlan,
    DecomposeRepositoryTask,
    TaskOrchestrator,
)
from repomesh.modules.task_orchestration.contracts import (
    AssignTaskCommand,
    LeaderReviewFinding,
    LeaderReviewVerdict,
    PublishedTaskPackage,
    TaskPublicationUnavailable,
    TaskView,
)
from repomesh.modules.task_orchestration.domain import TaskNotFound

from .test_leader_review_lifecycle import TEST_PATHS, TESTS, Round
from .test_plan_execution import Environment
from .test_round_redispatch import OpenCheckpoints


class PermitRefused(RuntimeError):
    """The Specification module refusing to write a permit, as a store can."""


class Journal:
    """One ordered log across every fake, because order is the whole defect.

    Per-fake counters cannot answer "did the permit come first?" — only a
    record that spans the permit author, the package publisher and the room
    can, and it has to be read per task because a round dispatches several.
    """

    def __init__(self) -> None:
        self.entries: list[tuple[str, UUID]] = []

    def record(self, event: str, task_id: UUID) -> None:
        self.entries.append((event, task_id))

    def of(self, task_id: UUID) -> list[str]:
        return [event for event, subject in self.entries if subject == task_id]


class PermitLedger:
    """The Specification module, reduced to the one fact a preflight reads.

    ``approved`` is what ``materialize`` looks for when a Worker calls back, so
    it is what the collaboration fake below interrogates. ``ensure_approved``
    is re-entrant because the real author is: a replay writes the same permit
    again rather than a second one.
    """

    def __init__(self, journal: Journal) -> None:
        self._journal = journal
        self.approved: set[UUID] = set()
        #: How many upcoming permit writes to refuse — the crash window this
        #: change opens deliberately, with the row written and the permit not.
        self.refusals = 0

    async def ensure_approved(
        self,
        task: TaskView,
        *,
        allowed_paths: tuple[str, ...],
        tests: tuple[str, ...],
        idempotency_key: str,
    ) -> None:
        if self.refusals:
            self.refusals -= 1
            raise PermitRefused("the specification store refused the permit")
        self._journal.record("permit", task.id)
        self.approved.add(task.id)


class JournalPublisher:
    """The task package half of a delivery, recorded rather than uploaded."""

    def __init__(self, journal: Journal) -> None:
        self._journal = journal
        #: Counted rather than named: the task id does not exist until the row
        #: the refusal is meant to strand has been written.
        self.refusals = 0

    async def publish(self, task: TaskView, **kwargs: object) -> PublishedTaskPackage:
        if self.refusals:
            self.refusals -= 1
            raise TaskPublicationUnavailable(
                "S3 operation failed; code: InvalidAccessKeyId, message: The "
                "Access Key Id you provided does not exist in our records."
            )
        self._journal.record("publish", task.id)
        team_name = str(kwargs["team_name"])
        return PublishedTaskPackage(
            team_name, f"teams/{team_name}/shared/tasks/{task.id}", "sha256:verified"
        )


class BridgeLikeCollaboration:
    """A room whose Worker members call back the instant a message lands.

    The callback is the point, and it is why this is not a recorder. The live
    Bridge answers a task assignment by asking the server to start the task,
    and that request's preflight reads the approved specification — so the
    read happens *here*, at send time, and fails the test with the sentence the
    defect deserves rather than leaving a counter to be interpreted later.

    Only Worker recipients are interrogated, because only a Worker has a
    Bridge. A leader is told about its task through the same call and owes no
    permit at all; asserting one would invent an invariant the system does not
    have.
    """

    def __init__(
        self, journal: Journal, permits: PermitLedger, worker_agent_ids: frozenset[UUID]
    ) -> None:
        self._journal = journal
        self._permits = permits
        self._worker_agent_ids = worker_agent_ids
        self.sent: list[SendCollaborationMessageCommand] = []
        #: How many upcoming sends to refuse, the way a room that is not ready
        #: refuses — the other half of a delivery from the publisher's.
        self.refusals = 0

    async def send(
        self, command: SendCollaborationMessageCommand, *, idempotency_key: str
    ) -> None:
        if command.recipient_agent_id in self._worker_agent_ids:
            assert command.task_id in self._permits.approved, (
                "S-1: the room was told about a Worker task before its execution "
                "permit existed. An online Bridge calls back on this message, its "
                "preflight raises SpecificationNotFound, and it refuses without "
                "retrying — the dispatch is lost for good."
            )
        if self.refusals:
            self.refusals -= 1
            raise CollaborationRouteUnavailable("AgentTeams room is not ready")
        self._journal.record("send", command.task_id)
        self.sent.append(command)

    def sent_for(self, task_id: UUID) -> list[SendCollaborationMessageCommand]:
        return [command for command in self.sent if command.task_id == task_id]


class Harness:
    """``Environment``'s world, rewired so the real dispatch path runs through it.

    ``Environment`` assigns through a recorder that neither publishes nor
    sends — exactly the two calls this invariant is about — so the real
    ``TaskOrchestrator`` takes the recorder's place and the decomposer, the
    advancer and (through ``Round``) the two leader use cases are rebuilt on
    top of it. Everything else is the shared harness, unchanged, so a topology
    or directory drift lands here too instead of quietly diverging.
    """

    def __init__(self, **environment_kwargs: object) -> None:
        self.journal = Journal()
        self.environment = Environment(**environment_kwargs)  # type: ignore[arg-type]
        self.permits = PermitLedger(self.journal)
        self.publisher = JournalPublisher(self.journal)
        self.collaboration = BridgeLikeCollaboration(
            self.journal, self.permits, frozenset(self.environment.worker_ids)
        )
        self.orchestrator = TaskOrchestrator(
            self.environment.directory,
            self.environment.topologies,
            self.environment.tasks,
            self.collaboration,
            self.publisher,
            OpenCheckpoints(),
        )
        self.environment.assigner = self.orchestrator  # type: ignore[assignment]
        self.environment.spec_author = self.permits  # type: ignore[assignment]
        self.environment.decomposer = DecomposeRepositoryTask(
            self.environment.directory,
            self.environment.topologies,
            self.environment.tasks,
            self.orchestrator,
            self.permits,
        )
        self.environment.advancer = AdvanceExecutionPlan(
            self.environment.plans,
            self.environment.tasks,
            self.orchestrator,
            self.environment.decomposer,
            leader_lane=self.environment.leader_lane,
        )

    async def repository_task(self):
        return await self.environment.assign_repository_task()

    async def decompose(self, repository_task_id: UUID, *, key: str = "decompose"):
        return await self.environment.decomposer.execute(
            repository_task_id, idempotency_key=key, tests=TESTS, test_paths=TEST_PATHS
        )

    async def only_child(self, repository_task_id: UUID) -> UUID:
        children = await self.environment.tasks.list_by_parent(repository_task_id)
        assert len(children) == 1, f"expected one worker task, found {len(children)}"
        return children[0].id


async def _leader_round(harness: Harness) -> Round:
    """A parked leader-mode round, ready for the leader to plan into."""

    environment = harness.environment
    plan = environment.plan(((0,),), tests={0: TESTS}, test_paths={0: TEST_PATHS})
    await environment.advancer.start(plan, idempotency_key="round-1")
    leader_task_id = await environment.leader_task_id(
        (await environment.plans.list_all())[0].id, 0, 0
    )
    return Round(environment, leader_task_id)


# ---------------------------------------------------------------------------
# The invariant, on all three paths that owe a Worker task a permit
# ---------------------------------------------------------------------------


async def test_a_decomposed_worker_task_is_permitted_before_its_room_is_told() -> None:
    """The live shape, server-planned: decompose, permit, then announce.

    The collaboration fake has already performed the Bridge's callback read by
    the time this returns; the journal states the order that read proves.
    """

    harness = Harness()
    repository_task = await harness.repository_task()

    worker_tasks = await harness.decompose(repository_task.id)

    assert len(worker_tasks) == 1
    assert harness.journal.of(worker_tasks[0].id) == ["permit", "publish", "send"]
    assert len(harness.collaboration.sent_for(worker_tasks[0].id)) == 1


async def test_a_leader_planned_worker_task_is_permitted_before_its_room_is_told() -> None:
    """The same invariant on the leader's own dispatch loop.

    A leader plan writes several worker tasks in one pass, so the order has to
    hold per task rather than across the batch — a loop that permitted all of
    them after announcing all of them would satisfy any global counter.
    """

    harness = Harness(leader_mode_repositories=(0,))
    round_ = await _leader_round(harness)

    receipt = await round_.submit_plan(nodes=2)

    assert len(receipt.worker_task_ids) == 2
    for worker_task_id in receipt.worker_task_ids:
        assert harness.journal.of(worker_task_id) == ["permit", "publish", "send"]


async def test_a_rework_task_is_permitted_before_its_room_is_told() -> None:
    """And on the repair path, which assigns through the same gateway."""

    harness = Harness(leader_mode_repositories=(0,))
    round_ = await _leader_round(harness)
    await round_.submit_plan(nodes=1)
    worker_task_ids = await round_.finish_workers()

    receipt = await round_.review(
        LeaderReviewVerdict.REQUEST_REWORK,
        findings=(
            LeaderReviewFinding(
                worker_task_id=worker_task_ids[0],
                note="The rule table is duplicated.",
                rework_instruction="Read the table from the contract instead.",
            ),
        ),
    )

    assert len(receipt.rework_task_ids) == 1
    assert harness.journal.of(receipt.rework_task_ids[0]) == ["permit", "publish", "send"]


async def test_reverse_proof_the_old_single_call_shape_loses_the_dispatch() -> None:
    """The defect itself, through the door that still opens it.

    ``deliver=True`` on a Worker task is exactly the pre-fix sequence: row and
    announcement in one act, permit nowhere. This asserts the harness can see
    that — a test suite that could not would pass just as happily before the
    split as after it.
    """

    harness = Harness()
    repository_task = await harness.repository_task()

    with pytest.raises(AssertionError, match="before its execution permit"):
        await harness.orchestrator.assign(
            AssignTaskCommand(
                organization_id=harness.environment.organization_id,
                project_id=harness.environment.project_id,
                repository_id=harness.environment.repository_ids[0],
                assigned_by_agent_id=harness.environment.leader_ids[0],
                assignee_agent_id=harness.environment.worker_ids[0],
                title="Implement pricing",
                instruction="Own the repository-level pricing change.",
                acceptance=("Tests pass",),
                parent_task_id=repository_task.id,
            ),
            idempotency_key="pre-fix-shape",
        )


# ---------------------------------------------------------------------------
# The interface the split introduced
# ---------------------------------------------------------------------------


async def test_assign_without_delivery_writes_the_row_and_says_nothing() -> None:
    """The first half alone: a task exists and no room has heard of it."""

    harness = Harness()
    repository_task = await harness.repository_task()

    worker_task = await harness.orchestrator.assign(
        AssignTaskCommand(
            organization_id=harness.environment.organization_id,
            project_id=harness.environment.project_id,
            repository_id=harness.environment.repository_ids[0],
            assigned_by_agent_id=harness.environment.leader_ids[0],
            assignee_agent_id=harness.environment.worker_ids[0],
            title="Implement pricing",
            instruction="Own the repository-level pricing change.",
            acceptance=("Tests pass",),
            parent_task_id=repository_task.id,
        ),
        idempotency_key="split-shape",
        deliver=False,
    )

    assert await harness.environment.tasks.get(worker_task.id) is not None
    assert harness.journal.of(worker_task.id) == []

    harness.permits.approved.add(worker_task.id)
    await harness.orchestrator.deliver_assignment(worker_task.id)

    assert harness.journal.of(worker_task.id) == ["publish", "send"]


async def test_delivering_a_task_that_does_not_exist_is_not_found() -> None:
    """The announcement is about a row; there is no announcing an absence."""

    harness = Harness()

    with pytest.raises(TaskNotFound):
        await harness.orchestrator.deliver_assignment(uuid4())


async def test_an_assignment_with_no_permit_to_write_still_announces_itself() -> None:
    """The default path, unchanged: one call, row and announcement together.

    A leader task has no execution permit — no Bridge wakes a leader — so
    nothing has to happen between the two halves, and the callers that never
    split (``AdvanceExecutionPlan``'s leader task, the CI rework path in
    ``integrations/scm``) keep the exact shape they had. ``deliver`` defaults
    to true for precisely this reason.
    """

    harness = Harness()

    leader_task = await harness.repository_task()

    assert harness.journal.of(leader_task.id) == ["send"]
    assert len(harness.collaboration.sent_for(leader_task.id)) == 1


# ---------------------------------------------------------------------------
# A-10 survives the split — the replay still finishes a stranded dispatch
# ---------------------------------------------------------------------------


async def test_a_failed_package_upload_is_completed_by_the_replay() -> None:
    """Defect A-10's acceptance criterion, restated for the new order.

    The permit now lands *before* the upload rather than after it, so a refused
    upload strands a task that is permitted and unannounced — a strictly better
    place to be stranded than the old one. What must not change is that the
    next press finishes it: the same row, the permit re-written rather than
    duplicated, and exactly one message in the room.
    """

    harness = Harness()
    repository_task = await harness.repository_task()
    harness.publisher.refusals = 1

    with pytest.raises(TaskPublicationUnavailable):
        await harness.decompose(repository_task.id)

    worker_task_id = await harness.only_child(repository_task.id)
    assert harness.journal.of(worker_task_id) == ["permit"]
    assert harness.permits.approved == {worker_task_id}

    worker_tasks = await harness.decompose(repository_task.id)

    assert [task.id for task in worker_tasks] == [worker_task_id]
    assert await harness.only_child(repository_task.id) == worker_task_id
    # The replay re-entered ``ensure_approved`` (idempotent) and then drove the
    # delivery the refusal ate. The room heard once, not twice.
    assert harness.journal.of(worker_task_id) == ["permit", "permit", "publish", "send"]
    assert len(harness.collaboration.sent_for(worker_task_id)) == 1


async def test_a_room_that_refused_the_message_is_completed_by_the_replay() -> None:
    """The other half of a delivery, broken on its own so the test says which.

    The package landed and the room did not. The replay must re-run the whole
    announcement — a second publication under the same key is recognised, so
    re-running it is free, while skipping it would need state nobody keeps.
    """

    harness = Harness()
    repository_task = await harness.repository_task()
    harness.collaboration.refusals = 1

    with pytest.raises(CollaborationRouteUnavailable):
        await harness.decompose(repository_task.id)

    worker_task_id = await harness.only_child(repository_task.id)
    assert harness.journal.of(worker_task_id) == ["permit", "publish"]
    assert harness.collaboration.sent_for(worker_task_id) == []

    await harness.decompose(repository_task.id)

    assert await harness.only_child(repository_task.id) == worker_task_id
    assert harness.journal.of(worker_task_id) == [
        "permit",
        "publish",
        "permit",
        "publish",
        "send",
    ]
    assert len(harness.collaboration.sent_for(worker_task_id)) == 1


# ---------------------------------------------------------------------------
# The crash window the split opens, and what closes it
# ---------------------------------------------------------------------------


async def test_a_refused_permit_leaves_the_room_silent() -> None:
    """The new failure mode, stated and bounded.

    Splitting the act means a permit can now fail with the task row already
    written — a window the old order did not have. It is the right window to
    have: the row is invisible to every agent, because nothing was announced,
    so the round stalls loudly at the caller instead of losing an assignment
    quietly in a Bridge that refused it. The replay then closes it, and this
    also pins the replay branch of ``assign``: finding an existing row must not
    announce it either, or the second attempt would race its own permit.
    """

    harness = Harness()
    repository_task = await harness.repository_task()
    harness.permits.refusals = 1

    with pytest.raises(PermitRefused):
        await harness.decompose(repository_task.id)

    worker_task_id = await harness.only_child(repository_task.id)
    assert harness.journal.of(worker_task_id) == [], "nothing may be said about it yet"
    assert harness.collaboration.sent_for(worker_task_id) == []
    assert worker_task_id not in harness.permits.approved

    worker_tasks = await harness.decompose(repository_task.id)

    assert [task.id for task in worker_tasks] == [worker_task_id]
    assert harness.journal.of(worker_task_id) == ["permit", "publish", "send"]
    assert len(harness.collaboration.sent_for(worker_task_id)) == 1
