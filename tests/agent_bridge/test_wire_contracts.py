"""The Bridge's wire models against the frozen ``contracts/agent-bridge/v1``.

Two different things are checked here and they are worth keeping apart.

*Round-trip*: what ``from_wire`` accepts, ``to_wire`` reproduces. That is what
lets the server side eventually replace its hand-written observation fixtures
with a real ``to_wire()`` payload without the fixtures quietly drifting.

*Enum agreement*: the enrollment schema's ``codingProfile`` values are real
Runner profile ids. The direction is schema ⊆ Runner and never the reverse — the
Runner legitimately carries profiles a Bridge must not drive, and the validation
``mock`` profile is exactly that.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from repomesh_agent_bridge.contracts import (
    CODING_PROFILES,
    ROOM_OBSERVATION_SCHEMA_VERSION,
    BindingRefused,
    BridgeStartupError,
    EnrollmentInvalid,
    ExternalWorkerEnrollment,
    RoomObservation,
    SessionNotReady,
    WorkerBinding,
)
from repomesh_agent_bridge.ports import RoomTransportError
from repomesh_runner.profiles import PROFILES

from .conftest import TEAM_ROOM, WORKER_ROOM, binding_wire, enrollment_wire

CONTRACT_ROOT = Path(__file__).parents[2] / "contracts" / "agent-bridge" / "v1"


def _schema(name: str) -> dict[str, object]:
    return json.loads((CONTRACT_ROOT / name).read_text(encoding="utf-8"))


def _observation_wire(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schemaVersion": ROOM_OBSERVATION_SCHEMA_VERSION,
        "observationId": "00000000-0000-0000-0000-000000000003",
        "emittedAt": "2026-08-26T12:00:00+00:00",
        "workerName": "pricing-codex-worker",
        "roomId": TEAM_ROOM,
        "kind": "run_started",
        "body": "Started run 3 of task pricing-42.",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# The refusal families, and the line between them
# ---------------------------------------------------------------------------


def test_a_runtime_that_is_not_ready_is_a_startup_refusal_like_every_other() -> None:
    """One exit code for "this instance did not start", however it failed to.

    A missing CLI, an unresolvable credential and a binding RepoMesh will not
    confirm are the same fact to whoever is supervising this process: nothing is
    serving, and retrying the command will not change that until a human does
    something. The subclass exists so a test or an operator can say *which*,
    never so a caller can branch differently.
    """

    assert issubclass(SessionNotReady, BridgeStartupError)
    assert not issubclass(SessionNotReady, BindingRefused), (
        "the coding runtime and the control plane fail for unrelated reasons"
    )


def test_a_room_transport_failure_is_not_a_startup_refusal() -> None:
    """Deliberately two families, joined only at the composition root.

    A homeserver that stops answering three hours in has not stopped anything
    from starting, and the supervisor's backoff is built on being able to catch
    the transport family without also catching every way a start can be refused.
    """

    assert not issubclass(RoomTransportError, BridgeStartupError)
    assert not issubclass(BridgeStartupError, RoomTransportError)


# ---------------------------------------------------------------------------
# codingProfile enum: schema ⊆ Runner
# ---------------------------------------------------------------------------


def test_every_schema_coding_profile_is_a_real_runner_profile() -> None:
    schema_enum = _schema("external-worker-enrollment.schema.json")["properties"]["codingProfile"][
        "enum"
    ]
    runner_ids = {profile.id for profile in PROFILES}

    assert set(schema_enum) <= runner_ids, "the Bridge may only name profiles the Runner can drive"


def test_the_implementation_spells_the_schema_enum_exactly() -> None:
    schema_enum = _schema("external-worker-enrollment.schema.json")["properties"]["codingProfile"][
        "enum"
    ]

    assert list(CODING_PROFILES) == list(schema_enum)


def test_the_runner_may_carry_profiles_the_bridge_refuses() -> None:
    """Pins the direction of the subset rather than leaving it to a comment."""

    runner_ids = {profile.id for profile in PROFILES}

    assert "mock" in runner_ids
    assert "mock" not in CODING_PROFILES


# ---------------------------------------------------------------------------
# Round-trips
# ---------------------------------------------------------------------------


def test_an_enrollment_round_trips() -> None:
    payload = enrollment_wire(displayName="Pricing Codex")

    assert ExternalWorkerEnrollment.from_wire(payload).to_wire() == payload


def test_an_enrollment_without_optionals_round_trips() -> None:
    payload = enrollment_wire()

    assert ExternalWorkerEnrollment.from_wire(payload).to_wire() == payload


def test_a_binding_round_trips_the_exact_body_repomesh_serves() -> None:
    payload = binding_wire()

    assert WorkerBinding.from_wire(payload).to_wire() == payload


def test_an_observation_round_trips_with_every_optional_present() -> None:
    payload = _observation_wire(
        kind="test_completed",
        taskId="00000000-0000-0000-0000-000000000004",
        runId="00000000-0000-0000-0000-000000000005",
        phase="verifying",
        toolName="pytest",
        changedFiles=["src/pricing/rules.py"],
        testCommand="pytest -q",
        testExitCode=0,
        commitSha="0123abc",
        questionId="00000000-0000-0000-0000-000000000006",
    )

    assert RoomObservation.from_wire(payload).to_wire() == payload


def test_an_observation_normalises_explicit_nulls_to_absent() -> None:
    """The schema allows both spellings; a room payload should carry neither."""

    with_nulls = _observation_wire(taskId=None, runId=None, phase=None, changedFiles=None)

    assert RoomObservation.from_wire(with_nulls).to_wire() == _observation_wire()


def test_an_observation_keeps_a_zero_exit_code() -> None:
    """A passing test suite is ``0``, which is falsey and must survive anyway."""

    payload = _observation_wire(kind="test_completed", testCommand="pytest -q", testExitCode=0)

    assert RoomObservation.from_wire(payload).to_wire()["testExitCode"] == 0


def test_an_observation_can_be_built_in_python_and_serialised() -> None:
    observation = RoomObservation(
        observation_id=UUID("00000000-0000-0000-0000-000000000003"),
        emitted_at=datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
        worker_name="pricing-codex-worker",
        room_id=WORKER_ROOM,
        kind="note",
        body="Bridge is up.",
    )

    assert observation.to_wire() == _observation_wire(
        roomId=WORKER_ROOM, kind="note", body="Bridge is up."
    )


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(_observation_wire(kind="thinking"), id="kind-not-in-enum"),
        pytest.param(_observation_wire(phase="daydreaming"), id="phase-not-in-enum"),
        pytest.param(_observation_wire(roomId="pricing"), id="room-id-not-matrix"),
        pytest.param(_observation_wire(body=""), id="empty-body"),
        pytest.param(_observation_wire(testExitCode=True), id="bool-is-not-an-exit-code"),
        pytest.param(_observation_wire(commitSha="ZZZZZZZ"), id="not-a-sha"),
        pytest.param(_observation_wire(transcript="everything the model said"), id="unknown-field"),
    ],
)
def test_a_malformed_observation_is_refused(payload: dict[str, object]) -> None:
    with pytest.raises(EnrollmentInvalid):
        RoomObservation.from_wire(payload)


# ---------------------------------------------------------------------------
# The enrollment reader is the schema, in code
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(enrollment_wire(organizationId="not-a-uuid"), id="organization-id"),
        pytest.param(enrollment_wire(workerName=""), id="empty-worker-name"),
        pytest.param(enrollment_wire(workerName="w" * 101), id="over-long-worker-name"),
        pytest.param(enrollment_wire(matrixHomeserverUrl="ftp://matrix"), id="non-http-homeserver"),
        pytest.param(enrollment_wire(allowedRoomIds=["#public:matrix"]), id="alias-not-room-id"),
        pytest.param(enrollment_wire(allowedRoomIds=[f"!r{n}:m.org" for n in range(51)]), id="51"),
        pytest.param(enrollment_wire(credentialRefs={"matrix": ""}), id="empty-credential-ref"),
        pytest.param(
            enrollment_wire(credentialRefs={"matrix": "env:M", "vault": "x"}),
            id="unknown-credential-slot",
        ),
        pytest.param(enrollment_wire(codingProfile="mock"), id="runner-only-profile"),
        pytest.param([], id="not-an-object"),
    ],
)
def test_a_malformed_enrollment_is_refused(payload: object) -> None:
    with pytest.raises(EnrollmentInvalid):
        ExternalWorkerEnrollment.from_wire(payload)


def test_a_display_name_defaults_to_the_worker_name() -> None:
    assert ExternalWorkerEnrollment.from_wire(enrollment_wire()).display == "pricing-codex-worker"
    assert (
        ExternalWorkerEnrollment.from_wire(enrollment_wire(displayName="Pricing Codex")).display
        == "Pricing Codex"
    )
