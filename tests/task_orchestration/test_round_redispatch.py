"""Defect A-13: dispatch is a one-shot event, not a convergent state.

Adjudicated root cause, verbatim: 派工是一次性事件、不是可收敛的状态——agent 那一个
回合没干成,控制台就永远停在 running,没有任何一层会重试、报警或让人在 GUI 上重发。

Three live shapes, one root (all found on 5533, 2026-08-12):

1. The mention was consumed by a container that then died, so the replacement
   container's fresh Matrix session cannot see it.
2. The mention arrived and the turn ran, but the package pull was refused; the
   turns ended and the tasks stayed ``running``.
3. A leader DM was delivered before its recipient's container existed, and
   ``SendCollaborationMessage.send`` short-circuits on ``DELIVERED`` so no
   replay ever re-sends it.

All three need the same thing: a way to make the *telling* happen again. These
tests drive the real ``SendCollaborationMessage`` and the real
``TaskOrchestrator`` — nothing here reaches a network, a Matrix homeserver or
MinIO; the messenger is a fake that records what a real one would have PUT.
"""

import json
from uuid import uuid4

import pytest

from repomesh.integrations.agentteams.task_publishing import AgentTeamsTaskPublisher
from repomesh.modules.collaboration import (
    InMemoryCollaborationMessageStore,
    SendCollaborationMessage,
)
from repomesh.modules.identity_access import PolicyAuthorizationGateway
from repomesh.modules.project.contracts import CheckpointGateDecision
from repomesh.modules.task_orchestration import (
    AssignTaskCommand,
    InMemoryExecutionPlanStore,
    TaskOrchestrator,
    TaskStatus,
)
from repomesh.modules.task_orchestration.application import (
    RedispatchRound,
    _dispatch_message_key,
)
from repomesh.modules.task_orchestration.contracts import PublishedTaskPackage
from repomesh.modules.task_orchestration.domain import (
    ExecutionPlan,
    PlannedRepositoryTask,
    RoundNotDispatchable,
    TaskConflict,
    TaskNotFound,
)

from .test_plan_execution import Environment

ASSIGN_KEY = "materialize-1:b0:repo"


class RecordingMessenger:
    """Records what a real Matrix client would have PUT, and PUTs nothing."""

    def __init__(self) -> None:
        self.deliveries: list[tuple[str, dict, str]] = []

    async def send_task(self, room_id: str, body: str, *, transaction_id: str, **kwargs) -> str:
        self.deliveries.append((room_id, json.loads(body), transaction_id))
        return f"$event-{len(self.deliveries)}"


class RecordingTaskPublisher:
    def __init__(self) -> None:
        self.publications: list[tuple[object, dict]] = []

    async def publish(self, task, **kwargs) -> PublishedTaskPackage:
        self.publications.append((task, kwargs))
        return PublishedTaskPackage(
            kwargs["team_name"],
            f"teams/{kwargs['team_name']}/shared/tasks/{task.id}",
            "sha256:verified",
        )


class OpenCheckpoints:
    async def operational_gate(self, project_id) -> CheckpointGateDecision:
        return CheckpointGateDecision(True, "project_active")

    async def evaluate(self, *args, **kwargs) -> CheckpointGateDecision:
        return CheckpointGateDecision(True, "open")


class MatrixDedupingMessenger(RecordingMessenger):
    """A messenger that behaves the way a real homeserver actually behaves.

    ``PUT /rooms/{id}/send/m.room.message/{txnId}`` is idempotent *at the
    homeserver*: a transaction id it has already seen returns the original
    event id and puts nothing new in the room. ``RecordingMessenger`` does not
    model that, so a test using it would report a re-send as delivered even
    where the room saw nothing — which is exactly the illusion the defect lived
    inside. This one models it, and is what the reverse-proof needs.
    """

    def __init__(self) -> None:
        super().__init__()
        self.by_transaction: dict[str, str] = {}

    async def send_task(self, room_id: str, body: str, *, transaction_id: str, **kwargs) -> str:
        if transaction_id in self.by_transaction:
            # Swallowed. The caller gets a 200 and an event id; the room gets
            # nothing. This is the silent half of the defect.
            return self.by_transaction[transaction_id]
        event_id = await super().send_task(room_id, body, transaction_id=transaction_id, **kwargs)
        self.by_transaction[transaction_id] = event_id
        return event_id


def _flow(messenger):
    """A real ``TaskOrchestrator`` over a real ``SendCollaborationMessage``.

    ``Environment`` already builds the directory and a topology whose teams
    have Matrix rooms; only the collaboration half is added here, because the
    room message is the thing under test and a recording assigner would hide
    it.
    """

    env = Environment()
    collaboration = SendCollaborationMessage(
        env.directory,
        env.topologies,
        PolicyAuthorizationGateway(),
        InMemoryCollaborationMessageStore(),
        messenger,
    )
    orchestrator = TaskOrchestrator(
        env.directory,
        env.topologies,
        env.tasks,
        collaboration,
        RecordingTaskPublisher(),
        OpenCheckpoints(),
    )
    return env, orchestrator


async def _assigned_worker_task(messenger=None, *, key: str = ASSIGN_KEY):
    """One dispatched Worker task, with everything real except the wires."""

    messenger = messenger or MatrixDedupingMessenger()
    env, orchestrator = _flow(messenger)
    task = await orchestrator.assign(
        AssignTaskCommand(
            organization_id=env.organization_id,
            project_id=env.project_id,
            repository_id=env.repository_ids[0],
            assigned_by_agent_id=env.leader_ids[0],
            assignee_agent_id=env.worker_ids[0],
            title="Implement pricing",
            instruction="Own the repository-level pricing change.",
            acceptance=("Tests pass",),
        ),
        idempotency_key=key,
    )
    return orchestrator, messenger, task, env.project_id


# ---------------------------------------------------------------------------
# The key derivation — the whole capability rests on this one string
# ---------------------------------------------------------------------------


def test_an_ordinary_dispatch_keeps_the_key_it_always_had() -> None:
    """No attempt token, no change. Every existing caller is untouched."""

    assert _dispatch_message_key("k", None) == "k:message"


def test_each_attempt_derives_its_own_key() -> None:
    """Two presses, two keys — and therefore two Matrix transaction ids."""

    first = _dispatch_message_key("k", "press-1")
    second = _dispatch_message_key("k", "press-2")
    assert first != second
    assert first != _dispatch_message_key("k", None)
    # The same press twice is the same key, which is what makes one request
    # idempotent while two requests are not.
    assert first == _dispatch_message_key("k", "press-1")


# ---------------------------------------------------------------------------
# The capability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redispatch_puts_a_new_event_in_the_room() -> None:
    """The point of the whole change: the worker is told again, for real."""

    orchestrator, messenger, task, _ = await _assigned_worker_task(MatrixDedupingMessenger())
    assert len(messenger.deliveries) == 1

    await orchestrator.redispatch(task.id, attempt="press-1")

    assert len(messenger.deliveries) == 2, (
        "a re-dispatch that does not reach the room is the defect, not the fix"
    )
    first_txn, second_txn = messenger.deliveries[0][2], messenger.deliveries[1][2]
    assert first_txn != second_txn
    assert second_txn.startswith(f"{ASSIGN_KEY}:message:redispatch:")
    # Same room, same task: a repeat of the telling, not a different message.
    assert messenger.deliveries[0][0] == messenger.deliveries[1][0]
    assert messenger.deliveries[0][1]["task_id"] == messenger.deliveries[1][1]["task_id"]


@pytest.mark.asyncio
async def test_redispatch_does_not_create_a_second_task() -> None:
    """A duplicate notification is the honest cost; a duplicate task is not."""

    orchestrator, _messenger, task, project_id = await _assigned_worker_task(
        MatrixDedupingMessenger()
    )
    before = await orchestrator.list_project_tasks(project_id)

    returned = await orchestrator.redispatch(task.id, attempt="press-1")

    after = await orchestrator.list_project_tasks(project_id)
    assert len(after) == len(before)
    assert returned.id == task.id
    assert returned.status is TaskStatus.ASSIGNED, "re-dispatch must not move the task"


@pytest.mark.asyncio
async def test_one_request_pressed_twice_posts_once() -> None:
    """Idempotent per request key — a double-click is not two mentions."""

    orchestrator, messenger, task, _ = await _assigned_worker_task(MatrixDedupingMessenger())

    await orchestrator.redispatch(task.id, attempt="press-1")
    await orchestrator.redispatch(task.id, attempt="press-1")

    assert len(messenger.deliveries) == 2, (
        "the second call under the same attempt must replay, not re-post"
    )


@pytest.mark.asyncio
async def test_redispatch_republishes_under_the_original_publication_key() -> None:
    """Not a detail — see the real-publisher proof below for why it is forced."""

    orchestrator, _messenger, task, _ = await _assigned_worker_task(MatrixDedupingMessenger())
    publisher = orchestrator._publisher  # noqa: SLF001 - the recording fake

    await orchestrator.redispatch(task.id, attempt="press-1")

    keys = [call[1]["idempotency_key"] for call in publisher.publications]
    assert keys == [f"{ASSIGN_KEY}:publication", f"{ASSIGN_KEY}:publication"]


@pytest.mark.asyncio
async def test_a_finished_task_is_not_dispatched_again() -> None:
    """Re-telling a Worker that already reported would be a lie about the work."""

    orchestrator, messenger, task, _ = await _assigned_worker_task(MatrixDedupingMessenger())
    stored = await orchestrator._tasks.get(task.id)  # noqa: SLF001
    await orchestrator._tasks.update(  # noqa: SLF001
        stored.report(TaskStatus.SUCCEEDED, "done"), expected_version=stored.version
    )
    posted = len(messenger.deliveries)

    with pytest.raises(TaskConflict) as raised:
        await orchestrator.redispatch(task.id, attempt="press-1")

    assert "already finished" in str(raised.value)
    assert len(messenger.deliveries) == posted


# ---------------------------------------------------------------------------
# Reverse-proof 1 — stash the derivation, watch the dedup swallow the re-send
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reverse_proof_without_the_derivation_the_resend_is_swallowed() -> None:
    """The pre-fix behaviour, reproduced deliberately.

    ``dispatch_attempt=None`` is the derivation stashed: the re-send is built
    with the key the first dispatch used. Two independent layers then eat it —
    ``SendCollaborationMessage.send`` finds a DELIVERED message under that key
    and returns without calling the messenger, and even if it did call, the
    homeserver has seen that transaction id.

    This is the defect itself, and the assertion is that the room learns
    nothing. It is why versioning the key is the fix rather than an
    optimisation: no amount of pressing a button wired this way would have
    woken specimen 3's leader.
    """

    orchestrator, messenger, task, _ = await _assigned_worker_task(MatrixDedupingMessenger())
    stored = await orchestrator._tasks.get(task.id)  # noqa: SLF001
    assert len(messenger.deliveries) == 1

    # Exactly what redispatch does, minus the one thing that makes it work.
    await orchestrator._deliver_assignment(  # noqa: SLF001
        stored, ASSIGN_KEY, dispatch_attempt=None
    )

    assert len(messenger.deliveries) == 1, (
        "without an attempt token the re-send is deduplicated away — the room "
        "sees nothing, which is defect A-13"
    )

    # And with it, the same call reaches the room.
    await orchestrator._deliver_assignment(  # noqa: SLF001
        stored, ASSIGN_KEY, dispatch_attempt="press-1"
    )
    assert len(messenger.deliveries) == 2


@pytest.mark.asyncio
async def test_reverse_proof_the_homeserver_alone_would_swallow_a_reused_txn_id() -> None:
    """Isolate the *second* dedup layer, so neither is mistaken for the other.

    Bypassing ``send`` entirely and PUTting the same transaction id twice shows
    the trap that survives even if the DELIVERED guard were removed: the
    transaction id is used verbatim in the Matrix PUT
    (``AgentTeamsMatrixClient.send_task``), so a re-send under the old key
    would still put nothing in the room. Two layers, one derivation that
    defeats both.
    """

    messenger = MatrixDedupingMessenger()
    first = await messenger.send_task("!room:local", "{}", transaction_id="k:message")
    second = await messenger.send_task("!room:local", "{}", transaction_id="k:message")

    assert first == second
    assert len(messenger.deliveries) == 1

    third = await messenger.send_task(
        "!room:local", "{}", transaction_id=_dispatch_message_key("k", "press-1")
    )
    assert third != first
    assert len(messenger.deliveries) == 2


# ---------------------------------------------------------------------------
# Reverse-proof 2 — the guard that was versioned still holds on the replay path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reverse_proof_ordinary_replay_still_does_not_re_send() -> None:
    """The regression this change could most easily have caused, tested for.

    ``AdvanceExecutionPlan._resume`` deliberately re-runs a whole batch —
    healthy or not — and relies on each keyed write recognising itself. Its
    re-run of ``assign`` reaches ``_deliver_assignment`` with no attempt token,
    so the DELIVERED short-circuit must still hold or every replay would spam
    every room. Nothing about that path was weakened: it was routed around.
    """

    orchestrator, messenger, task, project_id = await _assigned_worker_task(
        MatrixDedupingMessenger()
    )
    assert len(messenger.deliveries) == 1
    command = AssignTaskCommand(
        organization_id=(await orchestrator._tasks.get(task.id)).organization_id,  # noqa: SLF001
        project_id=project_id,
        repository_id=task.repository_id,
        assigned_by_agent_id=task.assigned_by_agent_id,
        assignee_agent_id=task.assignee_agent_id,
        title="Implement pricing",
        instruction="Own the repository-level pricing change.",
        acceptance=("Tests pass",),
    )

    replayed = await orchestrator.assign(command, idempotency_key=ASSIGN_KEY)

    assert replayed.id == task.id, "a replay must find its row, not write another"
    assert len(messenger.deliveries) == 1, (
        "a replay that re-posts is room spam; the DELIVERED guard still holds"
    )


# ---------------------------------------------------------------------------
# Why the publication key may not be versioned — proved on the real publisher
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_republication_under_a_new_key_is_refused_by_the_store(tmp_path) -> None:
    """The constraint that shapes the whole design, read off the real code.

    ``AgentTeamsTaskPublisher`` bakes ``idempotency_key`` into ``meta.json``
    and hashes ``meta.json`` into the package's content hash, then refuses a
    path whose stored hash differs. So versioning the publication key the way
    the message key is versioned would turn every re-dispatch into a
    ``ValueError`` — and e8014fd deliberately leaves ``ValueError``
    untranslated precisely because retrying cannot fix it. Hence: the original
    key for the package, the attempt token for the message only.

    Filesystem channel, in ``tmp_path``. No network, no MinIO.
    """

    orchestrator, _messenger, task, _ = await _assigned_worker_task(MatrixDedupingMessenger())
    view = (await orchestrator._tasks.get(task.id)).to_view()  # noqa: SLF001
    publisher = AgentTeamsTaskPublisher(tmp_path)
    published = await publisher.publish(
        view,
        team_name="team-0",
        room_id="!team-0:matrix.local",
        assignee_resource_name="native-worker-1",
        idempotency_key=f"{ASSIGN_KEY}:publication",
    )

    # Same key: recognised, same content hash, nothing rewritten.
    again = await publisher.publish(
        view,
        team_name="team-0",
        room_id="!team-0:matrix.local",
        assignee_resource_name="native-worker-1",
        idempotency_key=f"{ASSIGN_KEY}:publication",
    )
    assert again.content_hash == published.content_hash

    # A versioned key: a different package, and the store says no.
    with pytest.raises(ValueError, match="conflicts with existing content"):
        await publisher.publish(
            view,
            team_name="team-0",
            room_id="!team-0:matrix.local",
            assignee_resource_name="native-worker-1",
            idempotency_key=f"{ASSIGN_KEY}:publication:redispatch:press-1",
        )


# ---------------------------------------------------------------------------
# The round-level use case
# ---------------------------------------------------------------------------


async def _round(orchestrator, project_id, leader_task_id, *, batches=None):
    plans = InMemoryExecutionPlanStore()
    task = await orchestrator._tasks.get(leader_task_id)  # noqa: SLF001
    plan = ExecutionPlan(
        organization_id=task.organization_id,
        project_id=project_id,
        created_by_agent_id=task.assigned_by_agent_id,
        batches=batches
        or (
            (
                PlannedRepositoryTask(
                    repository_id=task.repository_id,
                    title=task.title,
                    instruction=task.instruction,
                    acceptance=task.acceptance,
                    leader_task_id=leader_task_id,
                ),
            ),
        ),
    )
    await plans.add(plan, idempotency_key="round-1")
    return plans, plan


@pytest.mark.asyncio
async def test_a_round_dispatches_its_leader_and_its_workers() -> None:
    """Specimen 3 is a leader, specimens 1 and 2 are workers. Both must be told."""

    messenger = MatrixDedupingMessenger()
    env, orchestrator = _flow(messenger)
    leader_task = await orchestrator.assign(
        AssignTaskCommand(
            organization_id=env.organization_id,
            project_id=env.project_id,
            repository_id=env.repository_ids[0],
            assigned_by_agent_id=env.organization_leader_id,
            assignee_agent_id=env.leader_ids[0],
            title="Implement pricing",
            instruction="Own the repository-level pricing change.",
            acceptance=("Tests pass",),
        ),
        idempotency_key="round-1:b0:leader",
    )
    worker_task = await orchestrator.assign(
        AssignTaskCommand(
            organization_id=env.organization_id,
            project_id=env.project_id,
            repository_id=env.repository_ids[0],
            parent_task_id=leader_task.id,
            assigned_by_agent_id=env.leader_ids[0],
            assignee_agent_id=env.worker_ids[0],
            title="Implement pricing",
            instruction="Own the repository-level pricing change.",
            acceptance=("Tests pass",),
        ),
        idempotency_key="round-1:b0:worker",
    )
    plans, plan = await _round(orchestrator, env.project_id, leader_task.id)
    posted = len(messenger.deliveries)

    receipt = await RedispatchRound(plans, orchestrator._tasks, orchestrator).execute(  # noqa: SLF001
        plan.id, attempt="press-1"
    )

    assert set(receipt.task_ids) == {leader_task.id, worker_task.id}
    assert receipt.settled_task_ids == ()
    assert receipt.attempt == "press-1"
    assert len(messenger.deliveries) == posted + 2


@pytest.mark.asyncio
async def test_a_settled_task_is_reported_not_re_told() -> None:
    """Honest accounting: the receipt says what was left alone and why."""

    orchestrator, messenger, task, project_id = await _assigned_worker_task(
        MatrixDedupingMessenger(), key="round-1:b0:leader"
    )
    stored = await orchestrator._tasks.get(task.id)  # noqa: SLF001
    await orchestrator._tasks.update(  # noqa: SLF001
        stored.report(TaskStatus.SUCCEEDED, "done"), expected_version=stored.version
    )
    plans, plan = await _round(orchestrator, project_id, task.id)

    with pytest.raises(RoundNotDispatchable) as raised:
        await RedispatchRound(plans, orchestrator._tasks, orchestrator).execute(  # noqa: SLF001
            plan.id, attempt="press-1"
        )

    assert "already finished" in str(raised.value)
    assert len(messenger.deliveries) == 1


@pytest.mark.asyncio
async def test_a_round_whose_tasks_were_never_written_says_so() -> None:
    """A-10's shape: a plan row exists and nothing under it does.

    The refusal must not read as "wait and press again" — the button that
    finishes this round is materialize, and the sentence names it.
    """

    orchestrator, _messenger, task, project_id = await _assigned_worker_task(
        MatrixDedupingMessenger()
    )
    stored = await orchestrator._tasks.get(task.id)  # noqa: SLF001
    plans = InMemoryExecutionPlanStore()
    plan = ExecutionPlan(
        organization_id=stored.organization_id,
        project_id=project_id,
        created_by_agent_id=stored.assigned_by_agent_id,
        batches=(
            (
                PlannedRepositoryTask(
                    repository_id=stored.repository_id,
                    title="Implement pricing",
                    instruction="Own it.",
                    acceptance=("Tests pass",),
                ),
            ),
        ),
    )
    await plans.add(plan, idempotency_key="round-1")

    with pytest.raises(RoundNotDispatchable) as raised:
        await RedispatchRound(plans, orchestrator._tasks, orchestrator).execute(  # noqa: SLF001
            plan.id, attempt="press-1"
        )

    assert "materialize" in str(raised.value)


@pytest.mark.asyncio
async def test_an_unknown_round_is_not_found() -> None:
    orchestrator, _messenger, _task, _project_id = await _assigned_worker_task(
        MatrixDedupingMessenger()
    )

    with pytest.raises(TaskNotFound):
        await RedispatchRound(
            InMemoryExecutionPlanStore(),
            orchestrator._tasks,  # noqa: SLF001
            orchestrator,
        ).execute(uuid4(), attempt="press-1")
