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
)
from repomesh_agent_bridge.application import RoomNativeAgent
from repomesh_agent_bridge.contracts import (
    BindingRefused,
    BindingUnavailable,
    CredentialRefs,
    EnrollmentInvalid,
    ExternalWorkerEnrollment,
    WorkerBinding,
)
from repomesh_agent_bridge.instance_lock import InstanceAlreadyRunning

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
    session: InertCodingSession | None = None,
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
