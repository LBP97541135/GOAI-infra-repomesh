"""Waking a governed run up from a room, and everything that must not happen.

The half this file covers is the *wake-up*: a mention that names a task reaches
RepoMesh, and what comes back reaches the room. The other half — narrating the
run RepoMesh then performs — is a later consumer of the anchor written here.

Two claims run through nearly every test, because they are the ones a room-native
trigger could most easily break:

*   **A room is not a permission system.** Nothing here checks who sent the
    message, whether the task exists, or whether this worker is its assignee.
    Those are RepoMesh's answers, and the tests assert that the Bridge relays
    them rather than reproducing them.
*   **A start action is never repeated by a machine.** A redelivered command
    produces one call, a control plane that could not be reached produces one
    call, and the only retry offered anywhere is a person mentioning the worker
    again.

The harness is ``test_room_scope``'s and is imported from it rather than copied:
a real SQLite file under ``tmp_path``, the real inbox and outbox, and doubles
only at the seams that would otherwise need a homeserver, a coding CLI or a
control plane. A second copy of that scaffolding would be a second thing to keep
true.
"""

import contextlib
import logging
import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from repomesh.modules.collaboration.domain import parse_matrix_task_report
from repomesh_agent_bridge.adapters.memory import (
    InMemoryGovernedTaskPort,
    InMemoryRoomPort,
    ScriptedCodingSession,
)
from repomesh_agent_bridge.contracts import ExternalWorkerEnrollment, RoomObservation
from repomesh_agent_bridge.inbox import Inbox
from repomesh_agent_bridge.outbox import (
    NOTE_LANE,
    RUN_LANE,
    Outbox,
    observation_txn_id,
    render,
)
from repomesh_agent_bridge.ports import (
    GovernedStartReceipt,
    GovernedTaskRefused,
    GovernedTaskUnavailable,
    RoomEvent,
)
from repomesh_agent_bridge.state import (
    SCHEMA_VERSION,
    BridgeState,
    OutboxRow,
    StateRefused,
    open_state,
    state_path,
)
from repomesh_agent_bridge.supervisor import (
    GOVERNANCE_DISABLED_NOTE,
    GOVERNANCE_REFUSED_PREFIX,
    GOVERNANCE_UNAVAILABLE_NOTE,
    RUN_ACCEPTED_BODY,
    governed_task_id,
)

from .conftest import MATRIX_USER_ID, TEAM_ROOM, WORKER_AGENT_ID, WORKER_NAME, WORKER_ROOM
from .test_room_scope import _batch, _drive, _event, _supervisor

WORKER_UUID = UUID(WORKER_AGENT_ID)
TASK_ID = UUID("11111111-2222-3333-4444-555555555555")
RUN_ID = UUID("99999999-8888-7777-6666-555555555555")
RECEIPT = GovernedStartReceipt(run_id=RUN_ID, task_id=TASK_ID)

ACCEPTED_BODY = f"[accepted] {RUN_ACCEPTED_BODY} (run {RUN_ID})"
"""What the room actually reads, spelled out rather than re-derived.

A test that rebuilt this from the same constants and the same renderer would
pass whatever those did, including nothing. The run id is in it because that is
the handle nobody in the room otherwise has.
"""


@contextlib.contextmanager
def _state(directory: Path) -> Iterator[BridgeState]:
    state = open_state(state_path(WORKER_UUID, directory), worker_agent_id=WORKER_UUID)
    try:
        yield state
    finally:
        state.close()


def _command(event_id: str = "$command", *, body: str | None = None, **overrides: Any) -> RoomEvent:
    return _event(event_id, body=body or f"@worker start task {TASK_ID}", **overrides)


def _observation(body: str = "on it", *, kind: str = "note", **overrides: Any) -> RoomObservation:
    return RoomObservation(
        observation_id=uuid4(),
        emitted_at=datetime(2026, 1, 1, tzinfo=UTC),
        worker_name=WORKER_NAME,
        room_id=TEAM_ROOM,
        kind=kind,
        body=body,
        **overrides,
    )


def _accepted_observation() -> RoomObservation:
    """The receipt in the shape the supervisor emits it."""

    return _observation(RUN_ACCEPTED_BODY, kind="run_accepted", task_id=TASK_ID, run_id=RUN_ID)


# ---------------------------------------------------------------------------
# The grammar: three words and a uuid, and nothing that merely resembles them
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        f"@worker start task {TASK_ID}",
        f"@worker Start Task {TASK_ID}".upper(),
        f"hey @worker, could you START task {TASK_ID} please?",
        f"@worker\nstart  task\t{TASK_ID}\nthanks",
    ],
)
def test_a_mention_that_names_a_task_is_read_as_a_command_however_it_is_written(
    body: str,
) -> None:
    """Case and surrounding text are a person typing, not a different request."""

    assert governed_task_id(body) == TASK_ID


@pytest.mark.parametrize(
    "body",
    [
        "what is 2+2",
        "done",
        "start task 11111111-2222-3333-4444-55555555555",
        "start task 11111111-2222-3333-4444-5555555555zz",
        "start task 111111112222333344445555555555555",
        f"start the task {TASK_ID}",
        f"restart task {TASK_ID}",
        "please start task soon",
    ],
)
def test_anything_that_is_not_the_command_is_an_ordinary_sentence(body: str) -> None:
    """A near miss is a conversation, never a guess.

    A mistyped id, a missing word, an extra prefix: each of these is somebody
    talking, and the cost of reading one as a command is a workspace and a run
    that nobody asked for. The parser would rather answer a question.
    """

    assert governed_task_id(body) is None


def test_the_first_task_named_wins_when_a_message_names_two() -> None:
    other = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")

    assert governed_task_id(f"start task {TASK_ID} then start task {other}") == TASK_ID


async def test_an_ordinary_mention_never_reaches_the_control_plane(
    enrollment: ExternalWorkerEnrollment, tmp_path: Path
) -> None:
    """The conversation path is exactly what it was, and the port is untouched.

    Asserted on the two lists rather than on a mock: a plain question produces a
    turn and a ``[note]``, and the control plane records no call at all.
    """

    room = InMemoryRoomPort(
        _batch(next_batch="s-0"),
        _batch(_event("$one", body="@worker what is 2+2"), next_batch="s-1"),
    )
    session = ScriptedCodingSession()
    governed = InMemoryGovernedTaskPort(RECEIPT)
    with _state(tmp_path) as state:
        supervisor = _supervisor(enrollment, state, room, session, governed_task=governed)

        await _drive(supervisor.serve(), room)

    assert governed.calls == [], "a question is not a wake-up"
    assert [turn.prompt for turn in session.turns] == ["@worker what is 2+2"]


# ---------------------------------------------------------------------------
# The receipt
# ---------------------------------------------------------------------------


async def test_a_command_starts_one_run_and_the_room_is_given_its_receipt(
    enrollment: ExternalWorkerEnrollment, tmp_path: Path
) -> None:
    """The whole happy path: one call, one message, one anchor, cursor committed.

    The anchor is the load-bearing part. RepoMesh knows the task, the worker and
    the run and knows nothing about Matrix, so without this row a Bridge that
    restarts mid-run has no way to tell which conversation asked for it.
    """

    room = InMemoryRoomPort(_batch(next_batch="s-0"), _batch(_command(), next_batch="s-1"))
    session = ScriptedCodingSession()
    governed = InMemoryGovernedTaskPort(RECEIPT)
    with _state(tmp_path) as state:
        supervisor = _supervisor(enrollment, state, room, session, governed_task=governed)

        await _drive(supervisor.serve(), room)

        assert [(call.task_id, call.worker_agent_id) for call in governed.calls] == [
            (TASK_ID, WORKER_UUID)
        ]
        assert session.turns == [], "a governed command never reaches the coding session"
        assert [message.body for message in room.sent] == [ACCEPTED_BODY]
        assert room.sent[0].txn_id == observation_txn_id("$command", RUN_LANE, 0)

        anchor = state.anchor_for_run(RUN_ID)
        assert anchor is not None
        assert (anchor.task_id, anchor.room_id, anchor.trigger_event_id) == (
            TASK_ID,
            TEAM_ROOM,
            "$command",
        )
        assert anchor.thread_root_id is None
        (row,) = state.sends_for_trigger("$command")
        assert (row.lane, row.ordinal, row.kind) == (RUN_LANE, 0, "run_accepted")
        assert state.turn_state(TEAM_ROOM, "$command", "$command") == "completed"
        cursor = state.cursor()
        assert cursor is not None and cursor.since_token == "s-1"


async def test_a_command_inside_a_thread_anchors_the_run_to_that_thread(
    enrollment: ExternalWorkerEnrollment, tmp_path: Path
) -> None:
    """A run's narration belongs where it was asked for.

    The thread root travels into the anchor so a later consumer replies under
    the conversation rather than at the top of the room, which for anyone else
    in that room is the difference between an answer and an interruption.
    """

    room = InMemoryRoomPort(
        _batch(next_batch="s-0"),
        _batch(_command(room_id=WORKER_ROOM, thread_root_id="$root"), next_batch="s-1"),
    )
    governed = InMemoryGovernedTaskPort(RECEIPT)
    with _state(tmp_path) as state:
        supervisor = _supervisor(
            enrollment, state, room, ScriptedCodingSession(), governed_task=governed
        )

        await _drive(supervisor.serve(), room)

        anchor = state.anchor_for_run(RUN_ID)
        assert anchor is not None
        assert (anchor.room_id, anchor.thread_root_id) == (WORKER_ROOM, "$root")
    assert room.sent[0].thread_root_id == "$root"


# ---------------------------------------------------------------------------
# The three ways it does not happen
# ---------------------------------------------------------------------------


async def test_a_refusal_is_repeated_to_the_room_in_repomesh_s_own_words(
    enrollment: ExternalWorkerEnrollment, tmp_path: Path
) -> None:
    """The one message that carries the control plane's text, and why.

    "You are not the assignee" and "there is no such task" are different
    problems for the person who asked, and a canned line would send them to read
    a log they do not have. It is safe to repeat because RepoMesh chose the
    words about a decision it made — no path, no status code, no credential.

    Nothing is anchored, because nothing started.
    """

    refusal = "worker is not assigned to this task"
    room = InMemoryRoomPort(_batch(next_batch="s-0"), _batch(_command(), next_batch="s-1"))
    governed = InMemoryGovernedTaskPort(GovernedTaskRefused(refusal))
    with _state(tmp_path) as state:
        supervisor = _supervisor(
            enrollment, state, room, ScriptedCodingSession(), governed_task=governed
        )

        await _drive(supervisor.serve(), room)

        assert [message.body for message in room.sent] == [
            f"[note] {GOVERNANCE_REFUSED_PREFIX}{refusal}"
        ]
        assert state.anchor_for_run(RUN_ID) is None
        assert state.turn_state(TEAM_ROOM, "$command", "$command") == "failed", (
            "the turn is settled: a refusal is an answer, not an abandonment"
        )
        (row,) = state.sends_for_trigger("$command")
        assert row.lane == NOTE_LANE, "a line this module wrote is not the run's own sequence"


async def test_a_control_plane_that_cannot_be_reached_is_told_flatly_and_never_retried(
    enrollment: ExternalWorkerEnrollment, tmp_path: Path, caplog
) -> None:
    """One call, one canned line, and no automatic second attempt.

    The retry that is not offered here is the point. A start action RepoMesh may
    have received is not safe for a machine to repeat — the room would be asking
    for the same work twice from one sentence — so the recovery on offer is a
    person reading the line and mentioning the worker again.
    """

    caplog.set_level(logging.WARNING)
    room = InMemoryRoomPort(_batch(next_batch="s-0"), _batch(_command(), next_batch="s-1"))
    governed = InMemoryGovernedTaskPort(
        GovernedTaskUnavailable("RepoMesh could not be reached: ConnectError")
    )
    with _state(tmp_path) as state:
        supervisor = _supervisor(
            enrollment, state, room, ScriptedCodingSession(), governed_task=governed
        )

        await _drive(supervisor.serve(), room)

        assert len(governed.calls) == 1, "the start action is attempted exactly once"
        assert [message.body for message in room.sent] == [f"[note] {GOVERNANCE_UNAVAILABLE_NOTE}"]
        assert state.anchor_for_run(RUN_ID) is None
    assert "ConnectError" in caplog.text, "the detail is the operator's, not the room's"
    assert "ConnectError" not in room.sent[0].body


async def test_an_instance_without_a_control_plane_says_so_instead_of_going_quiet(
    enrollment: ExternalWorkerEnrollment, tmp_path: Path
) -> None:
    """A conversation-only Bridge is a deployment, not a broken one.

    What would be broken is silence: somebody asks for a run, nothing happens,
    and the room is left to guess whether the worker is thinking or gone. The
    coding session is not asked either — a command is a command whether or not
    this instance can serve it, and handing it to a CLI would run ungoverned the
    work RepoMesh was supposed to govern.
    """

    room = InMemoryRoomPort(_batch(next_batch="s-0"), _batch(_command(), next_batch="s-1"))
    session = ScriptedCodingSession()
    with _state(tmp_path) as state:
        supervisor = _supervisor(enrollment, state, room, session)

        await _drive(supervisor.serve(), room)

        assert [message.body for message in room.sent] == [f"[note] {GOVERNANCE_DISABLED_NOTE}"]
        assert session.turns == []
        assert state.turn_state(TEAM_ROOM, "$command", "$command") == "failed"


# ---------------------------------------------------------------------------
# Redelivery
# ---------------------------------------------------------------------------


async def test_a_command_that_arrives_twice_starts_one_run_and_says_so_once(
    enrollment: ExternalWorkerEnrollment, tmp_path: Path
) -> None:
    """Redelivery is normal: an uncommitted cursor causes it.

    A duplicated *conversation* costs a wasted turn. A duplicated command would
    cost a second worktree and a second run, so the claim that keeps turns
    unique has to cover this path too — and the room hears about the run once.
    """

    command = _command()
    room = InMemoryRoomPort(
        _batch(next_batch="s-0"),
        _batch(command, next_batch="s-1"),
        _batch(command, next_batch="s-2"),
    )
    governed = InMemoryGovernedTaskPort(RECEIPT)
    with _state(tmp_path) as state:
        supervisor = _supervisor(
            enrollment, state, room, ScriptedCodingSession(), governed_task=governed
        )

        await _drive(supervisor.serve(), room)

    assert len(governed.calls) == 1
    assert [message.body for message in room.sent] == [ACCEPTED_BODY]


async def test_a_command_left_in_flight_by_a_dead_instance_lands_on_the_same_run(
    enrollment: ExternalWorkerEnrollment, tmp_path: Path
) -> None:
    """A power cut between starting the run and telling the room.

    What is on disk afterwards is a claim with no settlement and an anchor, and
    the next instance is *supposed* to run that turn again — the in-process claim
    set is what tells "running right now" from "was running when the machine went
    away", and it did not survive. So the start action is asked a second time,
    and the answer that makes this safe comes from RepoMesh: a start for a task
    whose run has not finished returns that run's own receipt. The anchor is
    rewritten with identical values, which is a no-op, and the room hears about
    the run once.
    """

    command = _command()
    batch = _batch(command, next_batch="s-1")
    with _state(tmp_path) as state:
        inbox = Inbox(state)
        inbox.record_baseline(_batch(next_batch="s-0"))
        (trigger,) = inbox.triggers(
            batch, matrix_user_id=MATRIX_USER_ID, allowed_rooms=(TEAM_ROOM, WORKER_ROOM)
        )
        assert inbox.claim(trigger).granted is True
        state.record_anchor(
            run_id=RUN_ID,
            task_id=TASK_ID,
            room_id=TEAM_ROOM,
            thread_root_id=None,
            trigger_event_id="$command",
        )

    room = InMemoryRoomPort(batch)
    governed = InMemoryGovernedTaskPort(RECEIPT)
    with _state(tmp_path) as state:
        supervisor = _supervisor(
            enrollment, state, room, ScriptedCodingSession(), governed_task=governed
        )

        await _drive(supervisor.serve(), room)

        anchor = state.anchor_for_run(RUN_ID)
        assert anchor is not None and anchor.trigger_event_id == "$command"
        assert len(governed.calls) == 1, "the replay asked, because nothing said it had"
        assert [message.body for message in room.sent] == [ACCEPTED_BODY]
        assert room.sent[0].txn_id == observation_txn_id("$command", RUN_LANE, 0), (
            "the name is derived from the mention, so the resend is one the homeserver "
            "collapses rather than a second message"
        )


# ---------------------------------------------------------------------------
# What the state file now holds
# ---------------------------------------------------------------------------


def test_a_state_file_written_by_schema_two_is_refused_rather_than_read(tmp_path: Path) -> None:
    """The bump is only a safeguard if a file that says ``2`` is turned away.

    ``2`` is what an operator's machine holds today: an outbox with two lanes
    and no run anchors. Opening one with this build would leave a governed run
    with nowhere to be recorded, and the tempting alternative — drop it and
    start clean — would take the rooms' conversation context with it.
    """

    path = state_path(WORKER_UUID, tmp_path)
    open_state(path, worker_agent_id=WORKER_UUID).close()
    with sqlite3.connect(path) as raw:
        raw.execute("UPDATE bridge_meta SET value = '2' WHERE key = 'schema_version'")
    raw.close()

    assert SCHEMA_VERSION == "3"
    with pytest.raises(StateRefused):
        open_state(path, worker_agent_id=WORKER_UUID)


def test_anchoring_one_run_twice_with_the_same_facts_is_a_no_op(tmp_path: Path) -> None:
    """Which is what a replay is: RepoMesh hands back the run it already started."""

    with _state(tmp_path) as state:
        for _ in range(2):
            state.record_anchor(
                run_id=RUN_ID,
                task_id=TASK_ID,
                room_id=TEAM_ROOM,
                thread_root_id=None,
                trigger_event_id="$command",
            )

        anchor = state.anchor_for_run(RUN_ID)
        assert anchor is not None and anchor.trigger_event_id == "$command"


def test_anchoring_one_run_to_a_second_conversation_is_refused(tmp_path: Path) -> None:
    """A run belongs to one conversation, and two answers to "which" is a bug.

    Merging would be the worse option: the narration would follow whichever
    write landed last, and the room that asked for the run would stop hearing
    about it for reasons nothing recorded.
    """

    with _state(tmp_path) as state:
        state.record_anchor(
            run_id=RUN_ID,
            task_id=TASK_ID,
            room_id=TEAM_ROOM,
            thread_root_id=None,
            trigger_event_id="$command",
        )

        with pytest.raises(StateRefused):
            state.record_anchor(
                run_id=RUN_ID,
                task_id=TASK_ID,
                room_id=WORKER_ROOM,
                thread_root_id=None,
                trigger_event_id="$somewhere-else",
            )

        anchor = state.anchor_for_run(RUN_ID)
        assert anchor is not None and anchor.room_id == TEAM_ROOM


def test_a_run_nobody_started_here_has_no_anchor(tmp_path: Path) -> None:
    with _state(tmp_path) as state:
        assert state.anchor_for_run(uuid4()) is None


def test_appending_the_same_lifecycle_position_twice_writes_one_message(tmp_path: Path) -> None:
    """The property the later run consumer is built on.

    Its messages arrive one at a time over minutes, so it names its own
    ordinals; naming one twice with the same message has to be the no-op a
    replay needs, and the room must see one line.
    """

    with _state(tmp_path) as state:
        outbox = Outbox(state, worker_agent_id=WORKER_UUID)
        appended = {
            "room_id": TEAM_ROOM,
            "thread_root_id": None,
            "trigger_event_id": "$command",
            "observation": _observation("the run finished"),
            "lane": RUN_LANE,
            "ordinal": 3,
        }

        outbox.enqueue_at(**appended)
        outbox.enqueue_at(**appended)

        rows = state.sends_for_trigger("$command")
        assert [(row.lane, row.ordinal) for row in rows] == [(RUN_LANE, 3)]
        assert rows[0].txn_id == observation_txn_id("$command", RUN_LANE, 3)


def test_appending_a_different_message_to_a_taken_position_is_refused(tmp_path: Path) -> None:
    """``INSERT OR IGNORE`` alone would drop it in silence.

    That is the exact failure the deterministic naming exists to prevent, and a
    caller assigning its own ordinals is the one place it could come back — so
    the collision is raised at the moment it happens rather than discovered
    later as a message the room never got.
    """

    with _state(tmp_path) as state:
        outbox = Outbox(state, worker_agent_id=WORKER_UUID)
        outbox.enqueue_at(
            room_id=TEAM_ROOM,
            thread_root_id=None,
            trigger_event_id="$command",
            observation=_observation("the run finished"),
            lane=RUN_LANE,
            ordinal=3,
        )

        with pytest.raises(StateRefused):
            outbox.enqueue_at(
                room_id=TEAM_ROOM,
                thread_root_id=None,
                trigger_event_id="$command",
                observation=_observation("the run failed"),
                lane=RUN_LANE,
                ordinal=3,
            )

        (row,) = state.sends_for_trigger("$command")
        assert row.body == "[note] the run finished"


def test_a_stored_intent_keeps_the_moment_it_was_first_written(tmp_path: Path) -> None:
    """A replay under a later clock is the same message, not a different one.

    One ``observationId`` must never be seen carrying two ``emittedAt`` values,
    so the emission time is the stored one and is deliberately not part of what
    makes two intents at one position the same intent.
    """

    with _state(tmp_path) as state:
        first = OutboxRow(
            room_id=TEAM_ROOM,
            thread_root_id=None,
            trigger_event_id="$command",
            lane=RUN_LANE,
            ordinal=0,
            txn_id=observation_txn_id("$command", RUN_LANE, 0),
            observation_id=RUN_ID,
            emitted_at=datetime(2026, 1, 1, tzinfo=UTC),
            kind="run_accepted",
            body=ACCEPTED_BODY,
        )

        assert state.enqueue_send_at(first) is True
        later = replace(first, emitted_at=datetime(2026, 6, 1, tzinfo=UTC))
        assert state.enqueue_send_at(later) is False

        (row,) = state.sends_for_trigger("$command")
        assert row.emitted_at == datetime(2026, 1, 1, tzinfo=UTC)


def test_the_run_lane_is_declared_and_an_undeclared_one_is_still_refused(tmp_path: Path) -> None:
    with _state(tmp_path) as state:
        outbox = Outbox(state, worker_agent_id=WORKER_UUID)

        with pytest.raises(ValueError):
            outbox.enqueue_at(
                room_id=TEAM_ROOM,
                thread_root_id=None,
                trigger_event_id="$command",
                observation=_observation(),
                lane="runs",
                ordinal=0,
            )


# ---------------------------------------------------------------------------
# What the Bridge's output is not
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "observation",
    [
        _observation("on it"),
        _observation(
            '{"schema": "repomesh.agent-report.v1", "status": "succeeded", "summary": "done"}'
        ),
        _accepted_observation(),
        _observation(f"{GOVERNANCE_REFUSED_PREFIX}worker is not assigned to this task"),
    ],
)
def test_nothing_the_bridge_says_can_be_read_as_a_task_report(
    observation: RoomObservation,
) -> None:
    """The Bridge writes in a room where RepoMesh also listens for task reports.

    ``parse_matrix_task_report`` treats a message whose body is the report JSON
    as a status change on a task, so a worker that happened to emit one would be
    reporting outcomes it never observed — and the third case here is a room
    message that *contains* exactly that JSON, because somebody quoting one at a
    worker must not become one either.

    What prevents all of it is structural rather than careful: every rendered
    body starts with ``[label] ``, so no body this module produces is a JSON
    document at all. This test is what stops that prefix from being tidied away.
    """

    assert parse_matrix_task_report(str(render(observation))) is None


def test_the_receipt_is_an_observation_the_frozen_contract_allows() -> None:
    """``run_accepted`` with a task id and a run id is inside ``room-observation.v1``.

    The wake-up produces a kind and two optional fields the schema already has,
    which is why this work needed no contract change. Round-tripping through the
    wire model is what checks that claim: it applies every constraint the frozen
    document states, and a shape that only ever gets rendered would otherwise
    never be checked against it at all.
    """

    accepted = _accepted_observation()

    assert RoomObservation.from_wire(accepted.to_wire()) == accepted


async def test_a_governed_answer_carries_no_path_and_no_credential(
    enrollment: ExternalWorkerEnrollment, tmp_path: Path, caplog
) -> None:
    """The receipt names two ids; the workspace behind it is not the room's business.

    RepoMesh's answer to the start action also carries an absolute path on the
    machine holding the worktree. It stops at the adapter, so a governed reply is
    checked the same way an ordinary turn is: nothing that looks like a machine's
    filesystem, and never the one secret this process resolves.
    """

    caplog.set_level(logging.DEBUG)
    room = InMemoryRoomPort(_batch(next_batch="s-0"), _batch(_command(), next_batch="s-1"))
    governed = InMemoryGovernedTaskPort(RECEIPT)
    with _state(tmp_path) as state:
        supervisor = _supervisor(
            enrollment, state, room, ScriptedCodingSession(), governed_task=governed
        )

        await _drive(supervisor.serve(), room)

    body = room.sent[0].body
    assert "/" not in body and "\\" not in body
    assert str(tmp_path) not in caplog.text
