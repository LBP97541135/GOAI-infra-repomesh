"""``RoomNativeAgent.run`` — the Bridge's only application interface.

Every test here goes through ``run``. That is not a stylistic preference: the
whole design claim of PR 2 is that startup order, the instance claim and the
process lifecycle are properties of the interface rather than of some helper, so
a test that reached past ``run`` to a private function would be verifying a
different claim than the one being made.

Two facts recur and are asserted the same way each time:

* "no network call happened" is ``port.calls == 0`` — a double that counts, not a
  patched socket layer;
* "this was refused" is the exception *type*. Refusals are one type with many
  messages; a test that branched on the message would freeze the wording and
  prove nothing about behaviour.
"""

import asyncio
from dataclasses import replace

import pytest

from repomesh_agent_bridge.adapters.memory import (
    InertCodingSession,
    InMemoryRoomPort,
    InMemoryWorkerBindingPort,
    ScriptedCodingSession,
)
from repomesh_agent_bridge.application import RoomNativeAgent, _single_failure
from repomesh_agent_bridge.contracts import (
    BindingRefused,
    BindingUnavailable,
    BridgeStartupError,
    CredentialRefs,
    EnrollmentInvalid,
    ExternalWorkerEnrollment,
    SessionNotReady,
    WorkerBinding,
)
from repomesh_agent_bridge.instance_lock import InstanceAlreadyRunning
from repomesh_agent_bridge.ports import RoomRefused
from repomesh_agent_bridge.state import state_path

from .conftest import (
    REPOMESH_TOKEN_REF,
    REPOMESH_TOKEN_VALUE,
    REPOMESH_TOKEN_VAR,
    TEAM_ROOM,
    WORKER_ROOM,
    WireBindingPort,
    binding_wire,
)

OTHER_ROOM = "!another-team:matrix.example.org"


def _agent(
    binding_port: object,
    *,
    tmp_path: object,
    room: InMemoryRoomPort | None = None,
    session: object | None = None,
    resolve_credential: object | None = None,
) -> RoomNativeAgent:
    extra = {} if resolve_credential is None else {"resolve_credential": resolve_credential}
    return RoomNativeAgent(
        binding_port=binding_port,
        room_port=room or InMemoryRoomPort(),
        coding_session=session or InertCodingSession(),
        state_dir=tmp_path,
        **extra,
    )


# ---------------------------------------------------------------------------
# Stage 1: decided without a socket
# ---------------------------------------------------------------------------


async def test_an_unknown_coding_profile_is_refused_with_no_network_call(
    enrollment: ExternalWorkerEnrollment, binding: WorkerBinding, tmp_path
) -> None:
    """The Runner's validation ``mock`` profile is a real id and still refused.

    The enrollment schema's enum is a subset of the Runner's profiles on
    purpose, and ``run`` enforces the subset itself rather than trusting that
    the enrollment came through the file reader.
    """

    port = InMemoryWorkerBindingPort(binding)

    with pytest.raises(EnrollmentInvalid):
        await _agent(port, tmp_path=tmp_path).run(replace(enrollment, coding_profile="mock"))

    assert port.calls == 0


async def test_a_missing_repomesh_credential_is_refused_with_no_network_call(
    enrollment: ExternalWorkerEnrollment, binding: WorkerBinding, tmp_path
) -> None:
    """An authenticated preflight with no credential reference never dials out."""

    port = InMemoryWorkerBindingPort(binding, requires_credential=True)
    without_token = replace(enrollment, credential_refs=CredentialRefs(matrix="env:MATRIX"))

    with pytest.raises(EnrollmentInvalid):
        await _agent(port, tmp_path=tmp_path).run(without_token)

    assert port.calls == 0


async def test_an_unresolvable_credential_is_refused_with_no_network_call(
    enrollment: ExternalWorkerEnrollment, binding: WorkerBinding, tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv(REPOMESH_TOKEN_VAR, raising=False)
    port = InMemoryWorkerBindingPort(binding, requires_credential=True)

    with pytest.raises(EnrollmentInvalid) as refusal:
        await _agent(port, tmp_path=tmp_path).run(enrollment)

    assert port.calls == 0
    assert REPOMESH_TOKEN_VAR in str(refusal.value), "an operator needs to know which variable"


async def test_an_empty_credential_reference_is_refused_with_no_network_call(
    enrollment: ExternalWorkerEnrollment, binding: WorkerBinding, tmp_path
) -> None:
    port = InMemoryWorkerBindingPort(binding, requires_credential=True)
    blank = replace(
        enrollment, credential_refs=CredentialRefs(matrix="   ", repomesh=REPOMESH_TOKEN_REF)
    )

    with pytest.raises(EnrollmentInvalid):
        await _agent(port, tmp_path=tmp_path).run(blank)

    assert port.calls == 0


async def test_the_resolved_credential_reaches_the_port_and_never_the_message(
    enrollment: ExternalWorkerEnrollment, tmp_path, monkeypatch
) -> None:
    """Stage 1 resolves the secret; stage 2 is the only thing that sees it."""

    monkeypatch.setenv(REPOMESH_TOKEN_VAR, REPOMESH_TOKEN_VALUE)
    port = InMemoryWorkerBindingPort(
        failure=BindingUnavailable("RepoMesh preflight answered 503"), requires_credential=True
    )

    with pytest.raises(BindingUnavailable) as unavailable:
        await _agent(port, tmp_path=tmp_path).run(enrollment)

    assert port.credentials == [REPOMESH_TOKEN_VALUE]
    assert REPOMESH_TOKEN_VALUE not in str(unavailable.value)


# ---------------------------------------------------------------------------
# Stage 2: RepoMesh preflight decides, and disagreement is refusal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "disagreement",
    [
        pytest.param({"workerAgentId": "00000000-0000-0000-0000-0000000000ff"}, id="worker-id"),
        pytest.param({"workerName": "some-other-worker"}, id="worker-name"),
        pytest.param({"matrixUserId": "@someone-else:matrix.example.org"}, id="matrix-user"),
        pytest.param({"teamName": "another-repo-team"}, id="team-name"),
        pytest.param({"organizationId": "00000000-0000-0000-0000-0000000000aa"}, id="org-id"),
        pytest.param({"containerManaged": True}, id="container-managed-true"),
        pytest.param({"containerManaged": 0}, id="container-managed-zero"),
        pytest.param({"schemaVersion": "repomesh.agent-bridge.binding.v2"}, id="schema-version"),
        pytest.param({"allowedRoomIds": [OTHER_ROOM]}, id="no-shared-room"),
    ],
)
async def test_preflight_refuses_a_binding_that_disagrees_with_the_enrollment(
    enrollment: ExternalWorkerEnrollment, tmp_path, disagreement: dict[str, object]
) -> None:
    """One refusal type for every way preflight can say no.

    Worker identity, team, container management, schema version and room
    ownership all reach the caller as ``BindingRefused``: the answer is the same
    in each case — there is no usable binding and retrying will not create one.
    """

    port = WireBindingPort(binding_wire(**disagreement))
    room = InMemoryRoomPort()

    with pytest.raises(BindingRefused):
        await _agent(port, tmp_path=tmp_path, room=room).run(enrollment)

    assert port.calls == 1
    assert room.started_rooms == (), "preflight must be strictly ahead of Matrix sync"


async def test_a_refused_preflight_leaves_the_worker_claimable(
    enrollment: ExternalWorkerEnrollment, binding: WorkerBinding, tmp_path, matrix_token: str
) -> None:
    """A failed start must not hold the worker's lock for the process lifetime."""

    with pytest.raises(BindingRefused):
        await _agent(
            WireBindingPort(binding_wire(workerName="other")), tmp_path=tmp_path
        ).run(enrollment)

    room = InMemoryRoomPort()
    task = asyncio.create_task(
        _agent(InMemoryWorkerBindingPort(binding), tmp_path=tmp_path, room=room).run(enrollment)
    )
    await asyncio.wait_for(room.ready.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_only_rooms_both_sides_confirm_are_joined(
    enrollment: ExternalWorkerEnrollment, tmp_path, matrix_token: str
) -> None:
    """RepoMesh owns room authority; the enrollment can only narrow it."""

    port = WireBindingPort(binding_wire(allowedRoomIds=[WORKER_ROOM, OTHER_ROOM]))
    room = InMemoryRoomPort()
    task = asyncio.create_task(_agent(port, tmp_path=tmp_path, room=room).run(enrollment))
    await asyncio.wait_for(room.ready.wait(), timeout=2)

    assert room.started_rooms == (WORKER_ROOM,)
    assert OTHER_ROOM not in room.started_rooms

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------------------
# The startup gate on the coding runtime
# ---------------------------------------------------------------------------


async def test_a_runtime_that_cannot_serve_is_refused_before_any_state_exists(
    enrollment: ExternalWorkerEnrollment,
    binding: WorkerBinding,
    tmp_path,
    matrix_token: str,
) -> None:
    """The whole point of the gate, stated as the four things that did not happen.

    A CLI that is missing or logged out is the failure this closes, and the
    damage it does is entirely in what a started Bridge would have gone on to
    do: adopt the room's backlog as already read and answer everything after it
    with a note nobody can act on. So the assertions are absences — no database
    for a worker that never served, no Matrix connection, no claim still held —
    and the type, which is the family the CLI turns into exit 2.
    """

    port = InMemoryWorkerBindingPort(binding)
    room = InMemoryRoomPort()
    session = ScriptedCodingSession(not_ready=SessionNotReady("codex is not installed"))

    with pytest.raises(SessionNotReady) as refused:
        await _agent(port, tmp_path=tmp_path, room=room, session=session).run(enrollment)

    assert isinstance(refused.value, BridgeStartupError), "the CLI maps this family to exit 2"
    assert port.calls == 1, "the gate is after preflight, not instead of it"
    assert session.ready_calls == 1
    assert room.calls == [], "a runtime that was turned away is never seen by a room"
    assert not state_path(enrollment.worker_agent_id, tmp_path).exists()


async def test_the_worker_stays_claimable_after_its_runtime_was_turned_away(
    enrollment: ExternalWorkerEnrollment,
    binding: WorkerBinding,
    tmp_path,
    matrix_token: str,
) -> None:
    """A refused gate must not hold the lock for the lifetime of the process.

    Asserted the way the refused-preflight case is: by starting a second Bridge
    for the same worker afterwards and watching it reach Matrix, not by reading
    a flag off the lock.
    """

    with pytest.raises(SessionNotReady):
        await _agent(
            InMemoryWorkerBindingPort(binding),
            tmp_path=tmp_path,
            session=ScriptedCodingSession(not_ready=SessionNotReady("codex is not installed")),
        ).run(enrollment)

    room = InMemoryRoomPort()
    task = asyncio.create_task(
        _agent(InMemoryWorkerBindingPort(binding), tmp_path=tmp_path, room=room).run(enrollment)
    )
    await asyncio.wait_for(room.ready.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_a_refused_preflight_never_reaches_the_runtime_gate(
    enrollment: ExternalWorkerEnrollment, tmp_path, matrix_token: str
) -> None:
    """The other side of the ordering, pinned from the cheap end.

    Probing a local CLI costs a process launch on an operator's machine, and a
    worker RepoMesh will not bind has no business paying it. The counter is the
    evidence: the gate was never asked.
    """

    session = ScriptedCodingSession()

    with pytest.raises(BindingRefused):
        await _agent(
            WireBindingPort(binding_wire(workerName="other")), tmp_path=tmp_path, session=session
        ).run(enrollment)

    assert session.ready_calls == 0


async def test_a_ready_runtime_is_asked_once_and_then_left_alone(
    enrollment: ExternalWorkerEnrollment,
    binding: WorkerBinding,
    tmp_path,
    matrix_token: str,
) -> None:
    """The gate is startup, not a per-round health check.

    Re-probing every round would spend a process launch per sync on a machine
    the operator is also using, and would give the loop a second way to stop
    that the recovery story does not account for.
    """

    room = InMemoryRoomPort()
    session = ScriptedCodingSession()
    task = asyncio.create_task(
        _agent(
            InMemoryWorkerBindingPort(binding), tmp_path=tmp_path, room=room, session=session
        ).run(enrollment)
    )
    await asyncio.wait_for(room.idle.wait(), timeout=5)

    assert session.ready_calls == 1
    assert room.calls[0] == "start", "and the room was reached only after the gate"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def test_run_blocks_until_cancelled_and_then_unwinds_cleanly(
    enrollment: ExternalWorkerEnrollment, binding: WorkerBinding, tmp_path, matrix_token: str
) -> None:
    """Start, stay up, and on cancellation close every seam and leave nothing behind."""

    before = asyncio.all_tasks()
    room = InMemoryRoomPort()
    session = InertCodingSession()
    task = asyncio.create_task(
        _agent(
            InMemoryWorkerBindingPort(binding), tmp_path=tmp_path, room=room, session=session
        ).run(enrollment)
    )

    await asyncio.wait_for(room.ready.wait(), timeout=2)
    assert room.started_rooms == (TEAM_ROOM, WORKER_ROOM)
    assert room.user_id == enrollment.matrix_user_id
    assert room.homeserver_url == enrollment.matrix_homeserver_url
    assert not task.done(), "run blocks until it is cancelled"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert room.closed
    assert session.closed
    assert not (asyncio.all_tasks() - before), "the supervisor starts no background task"


def test_one_failure_out_of_the_dual_loop_arrives_as_itself_and_two_do_not() -> None:
    """The seam between a task group and this package's failure vocabulary.

    ``run`` serves the room loop and the Runner loop as peers in an
    ``asyncio.TaskGroup``, which reports through an ``ExceptionGroup``. The CLI
    above it maps failures onto exit codes *by type* — ``RoomTransportError``,
    the startup-refusal family — and a group is neither, so an instance whose
    homeserver revoked its token would stop printing one line and start printing
    a traceback.

    One failure is the overwhelmingly common case, because the second loop is
    cancelled rather than failed and contributes nothing to the group; nesting is
    unwrapped too, since a group may arrive wrapped in another. Two genuine
    failures stay a group: collapsing that would mean choosing which of two real
    reasons to report.
    """

    refusal = RoomRefused("the homeserver refused GET /_matrix/client/v3/sync with 401")
    other = RuntimeError("the runner loop died")

    assert _single_failure(BaseExceptionGroup("g", [refusal])) is refusal
    assert (
        _single_failure(BaseExceptionGroup("outer", [BaseExceptionGroup("inner", [refusal])]))
        is refusal
    )
    both = BaseExceptionGroup("g", [refusal, other])
    assert _single_failure(both) is both


async def test_a_second_instance_for_the_same_worker_fails_before_preflight(
    enrollment: ExternalWorkerEnrollment, binding: WorkerBinding, tmp_path, matrix_token: str
) -> None:
    """Two real lock handles, no patched lock function.

    The claim is taken after local validation and before preflight, so the
    second instance is also proof of the ordering: it refuses without ever
    asking the control plane anything.
    """

    room = InMemoryRoomPort()
    first = asyncio.create_task(
        _agent(InMemoryWorkerBindingPort(binding), tmp_path=tmp_path, room=room).run(enrollment)
    )
    await asyncio.wait_for(room.ready.wait(), timeout=2)

    second_port = InMemoryWorkerBindingPort(binding)
    with pytest.raises(InstanceAlreadyRunning):
        # Bounded on purpose: a regression that dropped the claim would make the
        # second instance start and block forever, and a hanging suite reports
        # nothing. The timeout turns that regression into a failure.
        await asyncio.wait_for(_agent(second_port, tmp_path=tmp_path).run(enrollment), timeout=5)

    assert second_port.calls == 0

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first


async def test_a_different_worker_may_run_alongside(
    enrollment: ExternalWorkerEnrollment, tmp_path, matrix_token: str
) -> None:
    """The claim is per worker identity, not per machine or per state directory."""

    other_id = "00000000-0000-0000-0000-0000000000bb"
    other = ExternalWorkerEnrollment.from_wire(
        {**enrollment.to_wire(), "workerAgentId": other_id, "workerName": "other-worker"}
    )
    first_room, second_room = InMemoryRoomPort(), InMemoryRoomPort()
    first = asyncio.create_task(
        _agent(
            WireBindingPort(binding_wire()), tmp_path=tmp_path, room=first_room
        ).run(enrollment)
    )
    second = asyncio.create_task(
        _agent(
            WireBindingPort(binding_wire(workerAgentId=other_id, workerName="other-worker")),
            tmp_path=tmp_path,
            room=second_room,
        ).run(other)
    )

    await asyncio.wait_for(first_room.ready.wait(), timeout=2)
    await asyncio.wait_for(second_room.ready.wait(), timeout=2)

    for task in (first, second):
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
