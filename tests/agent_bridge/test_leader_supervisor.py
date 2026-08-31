"""A Repository Leader's round, from the message RepoMesh sends to the receipt.

``test_leader_lane`` covers the pieces: the wire models, the decision surface,
the coordination session. This covers the *sequence* — which message wakes the
lane, in what order the three calls happen, what the room is told, and above all
the six ways a round must not run twice.

Three habits carry through, and each is a lesson this line already paid for:

*   **The notices are RepoMesh's, not this file's.** Both bodies are produced by
    running the server's own leader mode (``task_orchestration``'s round
    harness) at import time. A transcript here would keep passing after the
    platform reworded its message, and the Bridge would quietly stop answering
    the two messages it exists to answer — which is exactly how the worker lane
    lost AC-03 for a fortnight.
*   **"Did not happen" is asserted on a list.** ``port.calls`` and
    ``session.asked`` are records, so "no plan was submitted" and "no session was
    spawned" are things a test states rather than mocks.
*   **The phase machine is real.** ``InMemoryLeaderActionPort`` really advances,
    really clamps and really repeats its receipts, so a replay that this lane
    survives is a replay it survives for the reason production would.

The identity is the shared worker one under a v2 ``repository_leader``
enrollment: the same rooms and the same Matrix user as every other Bridge test,
because what makes this member a leader is the role field and nothing else.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from task_orchestration.test_leader_review_lifecycle import start_round

from repomesh.modules.collaboration.contracts import CollaborationMessageKind
from repomesh.modules.collaboration.domain import parse_matrix_task_report
from repomesh_agent_bridge.adapters.memory import (
    InMemoryLeaderActionPort,
    InMemoryRoomPort,
    LeaderActionCall,
    ScriptedCodingSession,
    ScriptedLeaderSession,
)
from repomesh_agent_bridge.cli import _build_leader_runtime
from repomesh_agent_bridge.contracts import (
    ASSIGNMENT_PHASES,
    ENROLLMENT_V2_SCHEMA_VERSION,
    ROLE_REPOSITORY_LEADER,
    ExternalWorkerEnrollment,
    LeaderDocumentInvalid,
)
from repomesh_agent_bridge.inbox import Inbox
from repomesh_agent_bridge.leader_lane import (
    PLAN_NOTICE,
    REVIEW_NOTICE,
    parse_leader_notice,
)
from repomesh_agent_bridge.outbox import NOTE_LANE, observation_txn_id
from repomesh_agent_bridge.ports import LeaderActionRefused, LeaderActionUnavailable
from repomesh_agent_bridge.supervisor import (
    _EXPECTED_PHASE,
    LEADER_DRAFT_REFUSED_NOTE,
    LEADER_LANE_DISABLED_NOTE,
    LEADER_REFUSED_PREFIX,
    LEADER_UNAVAILABLE_NOTE,
    LeaderRuntime,
)

from .conftest import (
    MATRIX_TOKEN_VALUE,
    MATRIX_USER_ID,
    REPOMESH_TOKEN_REF,
    TEAM_ROOM,
    WORKER_ROOM,
    enrollment_wire,
)
from .test_coding_session import _FakeDriver, _make_session
from .test_governed_wakeup import _state
from .test_leader_lane import (
    SESSION_THREAD,
    approve,
    coordination,
    plan_answer,
    planning_package,
    review_evidence,
    rework,
    succeeded,
    valid_plan,
)
from .test_room_scope import _batch, _drive, _event, _supervisor

# ---------------------------------------------------------------------------
# The two notices, composed by RepoMesh
# ---------------------------------------------------------------------------


def _server_notices() -> tuple[UUID, dict[str, str]]:
    """Run one real leader-mode round and keep the two messages it sent.

    Generated rather than transcribed, and generated once: the round is
    in-memory but it is still a whole batch assignment, a plan submission and
    two worker tasks finishing. What comes back is the exact bytes a leader's DM
    room carries, which is the only input this lane has.
    """

    async def run() -> tuple[UUID, dict[str, str]]:
        round_ = await start_round()
        await round_.submit_plan()
        await round_.finish_workers()
        decisions = [
            command
            for command, _ in round_.collaboration.sent
            if command.kind is CollaborationMessageKind.DECISION
        ]
        bodies = {
            PLAN_NOTICE: _only(decisions, "awaiting your repository plan"),
            REVIEW_NOTICE: _only(decisions, "awaiting your review"),
        }
        return round_.leader_task_id, bodies

    return asyncio.run(run())


def _only(decisions: list, subject: str) -> str:
    matching = [command for command in decisions if subject in command.subject]
    assert len(matching) == 1, f"leader mode sends exactly one {subject!r} notice"
    return matching[0].body


LEADER_TASK_ID, NOTICE = _server_notices()
"""The leader task RepoMesh parked, and the body of each notice about it."""


def enveloped(body: str) -> str:
    """The notice as it really reaches a leader's DM room.

    The collaboration messenger sends one JSON document with the text in a
    ``body`` member and prefixes the recipient's Matrix id
    (``collaboration/application.py`` ``_wire_payload`` and
    ``integrations/agentteams/matrix.py``), so every newline in the notice
    arrives as ``\\n`` and every quote backslash-escaped. Live, this is the only
    shape a Bridge ever sees; the plain body is what the platform composed.
    """

    return f"{MATRIX_USER_ID} " + json.dumps(
        {
            "schema": "repomesh.collaboration.v1",
            "message_id": str(uuid4()),
            "correlation_id": str(LEADER_TASK_ID),
            "project_id": str(uuid4()),
            "repository_id": str(uuid4()),
            "task_id": str(LEADER_TASK_ID),
            "sender_agent_id": str(uuid4()),
            "recipient_agent_id": str(uuid4()),
            "kind": CollaborationMessageKind.DECISION.value,
            "subject": "Deliver repository 0: awaiting your decision",
            "body": body,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


# ---------------------------------------------------------------------------
# arrangement
# ---------------------------------------------------------------------------

PLAN_LINE = "[note] Repository plan accepted: revision 1, 2 worker task(s) dispatched."
APPROVE_LINE = (
    "[note] Review round 1 answered approve; RepoMesh now calls this leader task succeeded."
)
REWORK_LINE = (
    "[note] Review round 1 answered request_rework; RepoMesh now calls this leader task "
    "in_progress. 1 rework task(s) created."
)
STALE_PLAN_LINE = (
    "[note] RepoMesh says this round is executing rather than planning, so I submitted nothing."
)
STALE_REVIEW_LINE = (
    "[note] RepoMesh says this round is planning rather than review_due, so I submitted nothing."
)
"""What the room actually reads, spelled out rather than re-derived.

A test that rebuilt these from the same templates and the same renderer would
pass whatever those did, including nothing.
"""


def leader_enrollment() -> ExternalWorkerEnrollment:
    """The shared identity, enrolled as a Repository Leader.

    Built from the same wire payload every other Bridge test uses so the rooms,
    the Matrix id and the worker id are the harness's; the single difference is
    the field that decides everything here.
    """

    return ExternalWorkerEnrollment.from_wire_v2(
        enrollment_wire(
            schemaVersion=ENROLLMENT_V2_SCHEMA_VERSION, role=ROLE_REPOSITORY_LEADER
        )
    )


def leader_port() -> InMemoryLeaderActionPort:
    """The decision surface, keyed to the leader task RepoMesh's notice names.

    The frozen fixture package under the generated round's id: the wording of
    the notice and the shape of the package both come from outside this file,
    and the id is the one thing that has to be the same in both.
    """

    return InMemoryLeaderActionPort(
        replace(planning_package(), leader_task_id=LEADER_TASK_ID),
        review_evidence=review_evidence(),
    )


class RefusingWrites(InMemoryLeaderActionPort):
    """A control plane that answers the read and turns the submission down.

    The scripted refusals of the in-memory port are consumed one per call and
    the read comes first, so "RepoMesh took the question and refused the answer"
    — the case the whole no-retry argument is about — needs saying here. The
    call is still recorded before it fails, which is what makes "exactly one
    attempt" observable.
    """

    def __init__(self, failure: BaseException, **kwargs: object) -> None:
        super().__init__(replace(planning_package(), leader_task_id=LEADER_TASK_ID), **kwargs)  # type: ignore[arg-type]
        self._failure = failure

    async def submit_plan(self, task_id, decision):  # type: ignore[no-untyped-def]
        self.calls.append(LeaderActionCall(action="plan", task_id=task_id))
        raise self._failure


def notice_event(event_id: str = "$notice", *, action: str = PLAN_NOTICE, **overrides):
    """One leader notice as a room event, in its plain shape by default."""

    body = overrides.pop("body", None) or NOTICE[action]
    return _event(event_id, body=body, room_id=overrides.pop("room_id", WORKER_ROOM), **overrides)


def leader_runtime(
    port: InMemoryLeaderActionPort, session: object | None = None
) -> LeaderRuntime:
    return LeaderRuntime(actions=port, session=session or ScriptedLeaderSession())


# ---------------------------------------------------------------------------
# Which message is a notice at all
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("action", [PLAN_NOTICE, REVIEW_NOTICE])
@pytest.mark.parametrize("shape", ["plain", "enveloped"])
def test_both_real_notices_are_recognised_and_name_what_they_ask_for(
    action: str, shape: str
) -> None:
    """The recogniser against the server's own words, in both wire shapes.

    Keying on the route rather than the prose is what lets a reworded notice go
    on waking the lane; keying on the *last segment* of the route is what keeps
    a review from being read as a plan, which would have this member decide the
    round it was asked to judge.
    """

    body = NOTICE[action] if shape == "plain" else enveloped(NOTICE[action])
    notice = parse_leader_notice(body)

    assert notice is not None
    assert (notice.task_id, notice.action) == (LEADER_TASK_ID, action)


def test_the_phases_a_notice_is_meaningful_in_are_the_frozen_ones() -> None:
    """The stale-notice gate reads RepoMesh's ``phase``, so it has to spell it
    the way the freeze does; a typo here would make every notice look stale."""

    assert set(_EXPECTED_PHASE.values()) <= set(ASSIGNMENT_PHASES)
    assert _EXPECTED_PHASE == {PLAN_NOTICE: "planning", REVIEW_NOTICE: "review_due"}


# ---------------------------------------------------------------------------
# The planning half
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shape", ["plain", "enveloped"])
async def test_a_planning_notice_becomes_a_submitted_plan_and_one_line(
    tmp_path: Path, shape: str
) -> None:
    """The whole planning round: read the facts, decide, submit, say so once.

    The coding session is asserted empty for the same reason the governed lane
    asserts it: a notice is a wake-up for the leader lane and handing it to a
    conversational turn as well would have this member answer its own round in
    prose while the round stayed open.
    """

    body = NOTICE[PLAN_NOTICE] if shape == "plain" else enveloped(NOTICE[PLAN_NOTICE])
    room = InMemoryRoomPort(
        _batch(next_batch="s-0"),
        _batch(notice_event(body=body), next_batch="s-1"),
    )
    conversation = ScriptedCodingSession()
    port, session = leader_port(), ScriptedLeaderSession(valid_plan())
    with _state(tmp_path) as state:
        supervisor = _supervisor(
            leader_enrollment(),
            state,
            room,
            conversation,
            leader_runtime=leader_runtime(port, session),
        )

        await _drive(supervisor.serve(), room)

        assert [(call.action, call.task_id) for call in port.calls] == [
            ("fetch", LEADER_TASK_ID),
            ("plan", LEADER_TASK_ID),
        ]
        assert session.asked == [("plan", LEADER_TASK_ID)]
        assert conversation.turns == [], "a notice never reaches the conversation lane"
        assert [message.body for message in room.sent] == [PLAN_LINE]
        assert room.sent[0].txn_id == observation_txn_id("$notice", NOTE_LANE, 0)
        assert port.phase == "executing", "the round moved because RepoMesh accepted the plan"
        (row,) = state.sends_for_trigger("$notice")
        assert (row.lane, row.ordinal, row.kind) == (NOTE_LANE, 0, "note")
        assert state.turn_state(WORKER_ROOM, "$notice", "$notice") == "completed"
        cursor = state.cursor()
        assert cursor is not None and cursor.since_token == "s-1"


async def test_the_room_line_carries_no_path_and_no_secret(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A leader's product is its own; what the room gets is that RepoMesh took it.

    The plan itself — the spec, the DAG, the instructions — never leaves the
    Bridge except towards the control plane, so the line is counters and nothing
    else. The same scan the conversation lane runs: nothing that looks like a
    filesystem, and never the one secret this process resolves.
    """

    caplog.set_level(logging.DEBUG)
    room = InMemoryRoomPort(_batch(next_batch="s-0"), _batch(notice_event(), next_batch="s-1"))
    port = leader_port()
    with _state(tmp_path) as state:
        await _drive(
            _supervisor(
                leader_enrollment(),
                state,
                room,
                ScriptedCodingSession(),
                leader_runtime=leader_runtime(port, ScriptedLeaderSession(valid_plan())),
            ).serve(),
            room,
        )

    body = room.sent[0].body
    assert "\\" not in body and ":/" not in body
    assert "src/" not in body, "not even a repository-relative path the model chose"
    assert MATRIX_TOKEN_VALUE not in caplog.text
    assert parse_matrix_task_report(body) is None, "J-17: the prefix keeps it out of the reports"


# ---------------------------------------------------------------------------
# The review half
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("decision", "expected", "phase"),
    [
        (approve, APPROVE_LINE, "closed"),
        (rework, REWORK_LINE, "executing"),
    ],
)
async def test_a_review_notice_becomes_a_submitted_verdict_and_one_line(
    tmp_path: Path, decision, expected: str, phase: str
) -> None:
    """The verdict half, on both terminal shapes a round can take.

    The rework line names the count because that is the fact the room can act
    on: the round did not close and new worker tasks exist. Neither line carries
    the leader's summary — that is the leader's product and reaches RepoMesh,
    not the room.
    """

    room = InMemoryRoomPort(
        _batch(next_batch="s-0"),
        _batch(notice_event(action=REVIEW_NOTICE), next_batch="s-1"),
    )
    port = leader_port()
    port.worker_tasks_finished()
    session = ScriptedLeaderSession(decision())
    with _state(tmp_path) as state:
        await _drive(
            _supervisor(
                leader_enrollment(),
                state,
                room,
                ScriptedCodingSession(),
                leader_runtime=leader_runtime(port, session),
            ).serve(),
            room,
        )

        assert [call.action for call in port.calls] == ["fetch", "review"]
        assert session.asked == [("review", LEADER_TASK_ID)]
        assert [message.body for message in room.sent] == [expected]
        assert port.phase == phase


async def test_a_review_notice_that_arrives_before_the_evidence_submits_nothing(
    tmp_path: Path,
) -> None:
    """The gate is the phase and not the message.

    A verdict on a round still executing would be a verdict about work the
    leader was never shown, and the reason it cannot happen here is that the
    package is read before the session is ever spawned.
    """

    room = InMemoryRoomPort(
        _batch(next_batch="s-0"),
        _batch(notice_event(action=REVIEW_NOTICE), next_batch="s-1"),
    )
    port, session = leader_port(), ScriptedLeaderSession()
    with _state(tmp_path) as state:
        await _drive(
            _supervisor(
                leader_enrollment(),
                state,
                room,
                ScriptedCodingSession(),
                leader_runtime=leader_runtime(port, session),
            ).serve(),
            room,
        )

    assert [call.action for call in port.calls] == ["fetch"]
    assert session.asked == [], "no session is spawned for a round that is not waiting"
    assert [message.body for message in room.sent] == [STALE_REVIEW_LINE]


# ---------------------------------------------------------------------------
# Deciding once: redelivery, a second notice, and a dead instance
# ---------------------------------------------------------------------------


async def test_a_notice_that_arrives_twice_plans_once_and_says_so_once(
    tmp_path: Path,
) -> None:
    """Redelivery is normal: an uncommitted cursor causes it.

    A duplicated conversation costs a wasted turn. A duplicated plan would cost
    a second set of worker tasks — or, because the server keys a plan by the
    leader task, a refusal the room would have to explain — so the claim that
    keeps turns unique has to cover this path too.
    """

    notice = notice_event()
    room = InMemoryRoomPort(
        _batch(next_batch="s-0"),
        _batch(notice, next_batch="s-1"),
        _batch(notice, next_batch="s-2"),
    )
    port, session = leader_port(), ScriptedLeaderSession(valid_plan())
    with _state(tmp_path) as state:
        await _drive(
            _supervisor(
                leader_enrollment(),
                state,
                room,
                ScriptedCodingSession(),
                leader_runtime=leader_runtime(port, session),
            ).serve(),
            room,
        )

    assert [call.action for call in port.calls] == ["fetch", "plan"]
    assert session.asked == [("plan", LEADER_TASK_ID)]
    assert [message.body for message in room.sent] == [PLAN_LINE]


async def test_a_second_notice_about_a_round_that_moved_on_costs_one_read(
    tmp_path: Path,
) -> None:
    """The same notice under a new event id — a person forwarding it, a resend.

    The turn ledger cannot help here: this is a different trigger and a
    legitimately new turn. What stops it planning twice is that the round is
    read before it is decided, and RepoMesh has already moved past planning.
    """

    room = InMemoryRoomPort(
        _batch(next_batch="s-0"),
        _batch(notice_event("$first"), next_batch="s-1"),
        _batch(notice_event("$again"), next_batch="s-2"),
    )
    port, session = leader_port(), ScriptedLeaderSession(valid_plan())
    with _state(tmp_path) as state:
        await _drive(
            _supervisor(
                leader_enrollment(),
                state,
                room,
                ScriptedCodingSession(),
                leader_runtime=leader_runtime(port, session),
            ).serve(),
            room,
        )

    assert [call.action for call in port.calls] == ["fetch", "plan", "fetch"]
    assert session.asked == [("plan", LEADER_TASK_ID)], "the second notice spawned no session"
    assert [message.body for message in room.sent] == [PLAN_LINE, STALE_PLAN_LINE]


async def test_a_notice_left_in_flight_by_a_dead_instance_does_not_decide_twice(
    tmp_path: Path,
) -> None:
    """A power cut between the submission and the room message.

    What is on disk afterwards is a claim with no settlement, and the next
    instance is *supposed* to run that turn again — the in-process claim set is
    what tells "running right now" from "was running when the machine went
    away", and it did not survive. So the round is asked about a second time,
    and what makes that safe is not a second ledger but RepoMesh: the phase it
    reports is the durable record of a decision this process no longer
    remembers making.
    """

    notice = notice_event()
    batch = _batch(notice, next_batch="s-1")
    with _state(tmp_path) as state:
        inbox = Inbox(state)
        inbox.record_baseline(_batch(next_batch="s-0"))
        (trigger,) = inbox.triggers(
            batch, matrix_user_id=MATRIX_USER_ID, allowed_rooms=(TEAM_ROOM, WORKER_ROOM)
        )
        assert inbox.claim(trigger).granted is True

    port, session = leader_port(), ScriptedLeaderSession()
    await port.submit_plan(LEADER_TASK_ID, valid_plan())  # what the dead instance got done
    port.calls.clear()

    room = InMemoryRoomPort(batch)
    with _state(tmp_path) as state:
        await _drive(
            _supervisor(
                leader_enrollment(),
                state,
                room,
                ScriptedCodingSession(),
                leader_runtime=leader_runtime(port, session),
            ).serve(),
            room,
        )

    assert [call.action for call in port.calls] == ["fetch"], "the replay asked and stopped"
    assert session.asked == []
    assert [message.body for message in room.sent] == [STALE_PLAN_LINE]


async def test_a_settled_notice_replayed_after_a_restart_decides_nothing(
    tmp_path: Path,
) -> None:
    """The ordinary restart: the turn finished, the batch arrives again.

    Two supervisors over one state directory, which is what an operator's
    restart really is. The ledger settled the first turn, so the second instance
    never reaches the control plane at all — not even for the read.
    """

    notice = notice_event()
    port, session = leader_port(), ScriptedLeaderSession(valid_plan())
    first = InMemoryRoomPort(_batch(next_batch="s-0"), _batch(notice, next_batch="s-1"))
    with _state(tmp_path) as state:
        await _drive(
            _supervisor(
                leader_enrollment(),
                state,
                first,
                ScriptedCodingSession(),
                leader_runtime=leader_runtime(port, session),
            ).serve(),
            first,
        )

    port.calls.clear()
    second = InMemoryRoomPort(_batch(notice, next_batch="s-2"))
    with _state(tmp_path) as state:
        await _drive(
            _supervisor(
                leader_enrollment(),
                state,
                second,
                ScriptedCodingSession(),
                leader_runtime=leader_runtime(port, session),
            ).serve(),
            second,
        )

    assert port.calls == []
    assert session.asked == [("plan", LEADER_TASK_ID)]
    assert second.sent == [], "the room heard about this round once, in the first process"


# ---------------------------------------------------------------------------
# What is not a leader round
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("action", [PLAN_NOTICE, REVIEW_NOTICE])
async def test_a_worker_reads_the_same_bytes_as_conversation(
    enrollment: ExternalWorkerEnrollment, tmp_path: Path, action: str
) -> None:
    """The role gate, from the side that must not fire.

    A worker shares the team room with its leader, so it will see these
    messages. Acting on one would mean planning a round addressed to somebody
    else — and the lane is wired here on purpose, so "the port was never
    touched" is a claim about the branch rather than about the assembly.
    """

    room = InMemoryRoomPort(
        _batch(next_batch="s-0"),
        _batch(notice_event(action=action, room_id=TEAM_ROOM), next_batch="s-1"),
    )
    conversation = ScriptedCodingSession()
    port, session = leader_port(), ScriptedLeaderSession()
    with _state(tmp_path) as state:
        await _drive(
            _supervisor(
                enrollment,
                state,
                room,
                conversation,
                leader_runtime=leader_runtime(port, session),
            ).serve(),
            room,
        )

    assert port.calls == [] and session.asked == []
    assert [turn.prompt for turn in conversation.turns] == [NOTICE[action]]


@pytest.mark.parametrize(
    "body",
    [
        "please get the pricing round planned today",
        # A leader task named without either decision endpoint: a mention.
        f"see /api/v1/agent-actions/leader/assignments/{LEADER_TASK_ID} when you can",
        # Both endpoints at once: two askings in one message is not one wake-up.
        NOTICE[PLAN_NOTICE] + "\n" + NOTICE[REVIEW_NOTICE],
        # Two leader tasks in one notice: no honest way to pick one of them.
        NOTICE[PLAN_NOTICE].replace(str(LEADER_TASK_ID), str(uuid4()), 1),
    ],
)
async def test_a_body_that_is_not_one_notice_is_conversation(
    tmp_path: Path, body: str
) -> None:
    """Nearly-a-notice is not a notice.

    Each of these would be a round decided on a guess, and the cost of guessing
    wrong is a plan submitted for the wrong task or a verdict on a round nobody
    asked about. Anything that is not exactly one leader task and exactly one
    endpoint goes to the coding session, which is where a sentence belongs.
    """

    room = InMemoryRoomPort(
        _batch(next_batch="s-0"),
        _batch(notice_event(body=body), next_batch="s-1"),
    )
    conversation = ScriptedCodingSession()
    port, session = leader_port(), ScriptedLeaderSession()
    with _state(tmp_path) as state:
        await _drive(
            _supervisor(
                leader_enrollment(),
                state,
                room,
                conversation,
                leader_runtime=leader_runtime(port, session),
            ).serve(),
            room,
        )

    assert port.calls == [] and session.asked == []
    assert len(conversation.turns) == 1


async def test_a_leader_without_a_lane_says_so_instead_of_going_quiet(
    tmp_path: Path,
) -> None:
    """A leader brought up with the conversation stand-in is a deployment.

    What would be broken is silence: RepoMesh waits for a plan, the room sees
    nothing, and whoever is watching cannot tell a thinking leader from an
    absent one. The coding session is not asked either — a notice is a notice
    whether or not this instance can serve it, and answering it in prose would
    look like a round that had been decided.
    """

    room = InMemoryRoomPort(_batch(next_batch="s-0"), _batch(notice_event(), next_batch="s-1"))
    conversation = ScriptedCodingSession()
    with _state(tmp_path) as state:
        await _drive(
            _supervisor(leader_enrollment(), state, room, conversation).serve(), room
        )

        assert [message.body for message in room.sent] == [f"[note] {LEADER_LANE_DISABLED_NOTE}"]
        assert conversation.turns == []
        assert state.turn_state(WORKER_ROOM, "$notice", "$notice") == "failed"


# ---------------------------------------------------------------------------
# The three ways a round does not reach RepoMesh
# ---------------------------------------------------------------------------


async def test_a_draft_the_bridge_refuses_is_never_posted_and_is_narrated(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Adjudication B2-2, seen from the round: refused here, not by a 409.

    The room is told the round produced nothing and the reason stays in the log,
    because the sentence that says what was wrong quotes the document the model
    wrote. What the room must be able to trust is the negative: nothing was
    submitted.
    """

    caplog.set_level(logging.WARNING)
    room = InMemoryRoomPort(_batch(next_batch="s-0"), _batch(notice_event(), next_batch="s-1"))
    port = leader_port()
    session = ScriptedLeaderSession(
        LeaderDocumentInvalid("the session's plan is outside its bounds: node 'x' is off-roster")
    )
    with _state(tmp_path) as state:
        await _drive(
            _supervisor(
                leader_enrollment(),
                state,
                room,
                ScriptedCodingSession(),
                leader_runtime=leader_runtime(port, session),
            ).serve(),
            room,
        )

        assert [call.action for call in port.calls] == ["fetch"], "nothing was submitted"
        assert [message.body for message in room.sent] == [f"[note] {LEADER_DRAFT_REFUSED_NOTE}"]
        assert state.turn_state(WORKER_ROOM, "$notice", "$notice") == "failed"
    assert "off-roster" in caplog.text
    assert "off-roster" not in room.sent[0].body


async def test_a_real_session_that_answered_in_prose_is_refused_the_same_way(
    tmp_path: Path,
) -> None:
    """The two halves joined: the production coordination session in the lane.

    Everywhere else here the session is scripted, which pins the supervisor's
    behaviour and says nothing about what a real one raises. This drives the
    same round through ``LeaderCoordinationSession`` over a fake driver, so the
    refusal the supervisor handles is the one the real reader produces.
    """

    room = InMemoryRoomPort(_batch(next_batch="s-0"), _batch(notice_event(), next_batch="s-1"))
    port = leader_port()
    session, _ = await coordination(tmp_path, succeeded("I think we should split this in two."))
    with _state(tmp_path / "bridge") as state:
        await _drive(
            _supervisor(
                leader_enrollment(),
                state,
                room,
                ScriptedCodingSession(),
                leader_runtime=leader_runtime(port, session),
            ).serve(),
            room,
        )

    assert [call.action for call in port.calls] == ["fetch"]
    assert [message.body for message in room.sent] == [f"[note] {LEADER_DRAFT_REFUSED_NOTE}"]


async def test_a_real_session_plans_the_round_end_to_end(tmp_path: Path) -> None:
    """And the positive: one notice in, one plan submitted, one line out.

    The decision here is assembled by the production reader — schema, envelope
    clamp and provenance included — which is what makes the happy path above
    more than a claim about a double.
    """

    room = InMemoryRoomPort(_batch(next_batch="s-0"), _batch(notice_event(), next_batch="s-1"))
    port = leader_port()
    session, driver = await coordination(tmp_path, succeeded(plan_answer()))
    with _state(tmp_path / "bridge") as state:
        await _drive(
            _supervisor(
                leader_enrollment(),
                state,
                room,
                ScriptedCodingSession(),
                leader_runtime=leader_runtime(port, session),
            ).serve(),
            room,
        )

    assert [call.action for call in port.calls] == ["fetch", "plan"]
    assert [message.body for message in room.sent] == [PLAN_LINE]
    # D-8 again, at the one place the two lanes meet: the prompt the round put
    # in front of codex still names nothing on this machine.
    prompt = driver.requests[0].prompt
    assert str(tmp_path) not in prompt and "\\" not in prompt
    assert SESSION_THREAD not in room.sent[0].body


async def test_a_refused_submission_is_repeated_in_repomeshs_own_words(
    tmp_path: Path,
) -> None:
    """The one message that carries the control plane's text, and why.

    "This round already has a plan" and "you are not its assignee" are different
    problems for whoever is watching, and a canned line would send them to a log
    they do not have. It is safe to repeat because RepoMesh chose the words
    about a decision it made.
    """

    refusal = "this leader task already has an accepted plan"
    room = InMemoryRoomPort(_batch(next_batch="s-0"), _batch(notice_event(), next_batch="s-1"))
    port = RefusingWrites(LeaderActionRefused(refusal, code="phase_conflict"))
    with _state(tmp_path) as state:
        await _drive(
            _supervisor(
                leader_enrollment(),
                state,
                room,
                ScriptedCodingSession(),
                leader_runtime=leader_runtime(port, ScriptedLeaderSession(valid_plan())),
            ).serve(),
            room,
        )

        assert [call.action for call in port.calls] == ["fetch", "plan"], "one attempt, no retry"
        assert [message.body for message in room.sent] == [
            f"[note] {LEADER_REFUSED_PREFIX}{refusal}"
        ]
        assert state.turn_state(WORKER_ROOM, "$notice", "$notice") == "failed"


async def test_a_control_plane_that_could_not_be_asked_is_told_flatly_and_not_retried(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """One attempt, one canned line, and no automatic second try.

    The retry that is not offered is the point. A submission RepoMesh may have
    received is not safe for a machine to repeat; the server's own idempotency
    is what makes a *person* asking again safe, and that is the recovery the
    line invites.
    """

    caplog.set_level(logging.WARNING)
    room = InMemoryRoomPort(_batch(next_batch="s-0"), _batch(notice_event(), next_batch="s-1"))
    port = RefusingWrites(
        LeaderActionUnavailable("RepoMesh could not be reached: ConnectError")
    )
    with _state(tmp_path) as state:
        await _drive(
            _supervisor(
                leader_enrollment(),
                state,
                room,
                ScriptedCodingSession(),
                leader_runtime=leader_runtime(port, ScriptedLeaderSession(valid_plan())),
            ).serve(),
            room,
        )

    assert len([call for call in port.calls if call.action == "plan"]) == 1
    assert [message.body for message in room.sent] == [f"[note] {LEADER_UNAVAILABLE_NOTE}"]
    assert "ConnectError" in caplog.text
    assert "ConnectError" not in room.sent[0].body


# ---------------------------------------------------------------------------
# What the composition root builds for a leader
# ---------------------------------------------------------------------------


async def test_a_leader_enrollment_gets_a_lane_over_the_session_it_already_has(
    tmp_path: Path,
) -> None:
    """One codex stack, two readings (B2-1), assembled once by the CLI.

    The session handed to the lane is the *same object* the conversation track
    serves with, which is why gating it once — ``ensure_ready`` here, in ``run``
    on the composition root's own order — readies both readings at once.

    The credential is resolved per call and not at build time, which is why this
    can assert an adapter exists without the secret being anywhere near it yet.
    """

    session, _ = _make_session(tmp_path, _FakeDriver(succeeded(plan_answer())))
    runtime = _build_leader_runtime(leader_enrollment(), session=session)

    assert runtime is not None
    assert runtime.close is not None
    await session.ensure_ready()
    decision = await runtime.session.plan(planning_package())
    assert decision.worker_tasks, "the lane's session is the one that was handed over"
    await runtime.close()
    await session.close()


def test_a_stand_in_session_leaves_the_leader_lane_off(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``--inert`` is a legitimate way to bring a member up in its rooms.

    The honest answer is then a lane that is off and says so when a notice
    arrives, rather than an invocation refused or — far worse — a lane that
    looks assembled and cannot decide anything.
    """

    caplog.set_level(logging.WARNING)
    assert _build_leader_runtime(leader_enrollment(), session=ScriptedCodingSession()) is None
    assert "cannot plan or review" in caplog.text


def test_a_worker_enrollment_never_gets_a_leader_lane(
    enrollment: ExternalWorkerEnrollment,
) -> None:
    """AC-02 from the other direction: the role decides, and only the role."""

    assert _build_leader_runtime(enrollment, session=ScriptedCodingSession()) is None


def test_a_leader_without_a_repomesh_credential_is_refused_at_assembly() -> None:
    """Every leader action is authenticated as this member, so an enrollment
    that cannot authenticate one has no lane to build."""

    from repomesh_agent_bridge.contracts import BridgeStartupError

    payload = enrollment_wire(
        schemaVersion=ENROLLMENT_V2_SCHEMA_VERSION,
        role=ROLE_REPOSITORY_LEADER,
        credentialRefs={"matrix": "env:REPOMESH_BRIDGE_MATRIX_TOKEN"},
    )
    assert REPOMESH_TOKEN_REF not in json.dumps(payload)
    with pytest.raises(BridgeStartupError, match="credentialRefs.repomesh"):
        _build_leader_runtime(
            ExternalWorkerEnrollment.from_wire_v2(payload), session=ScriptedCodingSession()
        )
