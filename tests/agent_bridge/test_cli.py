"""``repomesh-agent-bridge`` through ``main(argv, ...)``.

No subprocess: the console script's whole job is to turn arguments into a
``RoomNativeAgent`` call and an exit code, and both are visible from here.
``binding_port_factory`` is the one seam these tests use — it is a real parameter
of ``main``, not a patched global, so a test cannot accidentally verify a
production path that no longer exists.

``check`` is the subject of most of it, because ``check`` is where the contract's
"joins nothing, spawns nothing, prints no secret" promises are observable.
"""

import json
from uuid import UUID

import pytest

from repomesh_agent_bridge.adapters.memory import InMemoryWorkerBindingPort
from repomesh_agent_bridge.cli import (
    EXIT_ALREADY_RUNNING,
    EXIT_OK,
    EXIT_STARTUP_REFUSED,
    main,
)
from repomesh_agent_bridge.contracts import (
    BindingUnavailable,
    ExternalWorkerEnrollment,
    WorkerBinding,
)
from repomesh_agent_bridge.instance_lock import InstanceLock, instance_lock_path

from .conftest import (
    REPOMESH_TOKEN_REF,
    REPOMESH_TOKEN_VALUE,
    REPOMESH_TOKEN_VAR,
    TEAM_ROOM,
    WORKER_AGENT_ID,
    WORKER_NAME,
    WORKER_ROOM,
    WireBindingPort,
    binding_wire,
    enrollment_wire,
)


@pytest.fixture
def written_enrollment(tmp_path):
    def write(payload: object = None, *, name: str = "enrollment.json"):
        path = tmp_path / name
        path.write_text(
            json.dumps(enrollment_wire() if payload is None else payload), encoding="utf-8"
        )
        return path

    return write


class RecordingFactory:
    """Counts how many ports were built, which is how "no network" is asserted here.

    ``main`` builds the port before it decides what to do with it, so a factory
    that was never called is proof the command stopped in stage 1 — where the
    contract says a malformed enrollment must stop.
    """

    def __init__(self, port: object) -> None:
        self.port = port
        self.calls = 0

    def __call__(self, enrollment: ExternalWorkerEnrollment) -> object:
        self.calls += 1
        return self.port


def test_check_reports_the_confirmed_binding_and_exits_zero(written_enrollment, capsys) -> None:
    factory = RecordingFactory(WireBindingPort(binding_wire()))

    code = main(["check", "--enrollment", str(written_enrollment())], binding_port_factory=factory)

    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert WORKER_NAME in out
    assert WORKER_AGENT_ID in out
    assert TEAM_ROOM in out
    assert WORKER_ROOM in out
    assert "containerManaged: false" in out
    assert factory.port.calls == 1


def test_check_prints_credential_slots_but_never_a_locator_or_a_value(
    written_enrollment, capsys, monkeypatch
) -> None:
    """The reference is non-secret by contract and still not stdout's business.

    A locator can name a private path or an internal keyring entry; the slot
    name answers the only diagnostic question ("was a repomesh credential
    configured at all") without naming anything.
    """

    monkeypatch.setenv(REPOMESH_TOKEN_VAR, REPOMESH_TOKEN_VALUE)
    port = InMemoryWorkerBindingPort(
        WorkerBinding.from_wire(binding_wire()), requires_credential=True
    )

    code = main(
        ["check", "--enrollment", str(written_enrollment())], binding_port_factory=lambda _: port
    )

    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "credentialRefs: matrix, repomesh" in out
    assert REPOMESH_TOKEN_VALUE not in out
    assert REPOMESH_TOKEN_REF not in out
    assert port.credentials == [REPOMESH_TOKEN_VALUE], "the value goes to the port and nowhere else"


def test_check_starts_no_matrix_sync_and_spawns_nothing(written_enrollment, capsys) -> None:
    code = main(
        ["check", "--enrollment", str(written_enrollment())],
        binding_port_factory=lambda _: WireBindingPort(binding_wire()),
    )

    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "matrix sync: not started" in out
    assert "coding session: not spawned" in out


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(enrollment_wire(codingProfile="mock"), id="runner-only-profile"),
        pytest.param(enrollment_wire(codingProfile="cursor"), id="unknown-profile"),
        pytest.param(enrollment_wire(matrixUserId="pricing-codex-worker"), id="bare-matrix-user"),
        pytest.param(enrollment_wire(allowedRoomIds=[]), id="no-rooms"),
        pytest.param(enrollment_wire(allowedRoomIds=[TEAM_ROOM, TEAM_ROOM]), id="duplicate-rooms"),
        pytest.param(enrollment_wire(repomeshEndpoint="not-a-url"), id="bad-endpoint"),
        pytest.param(enrollment_wire(schemaVersion="repomesh.agent-bridge.enrollment.v2"), id="v2"),
        pytest.param(enrollment_wire(credentialRefs={}), id="no-matrix-credential"),
        pytest.param({**enrollment_wire(), "matrixToken": "hunter2"}, id="unknown-field"),
        pytest.param({k: v for k, v in enrollment_wire().items() if k != "teamName"}, id="no-team"),
    ],
)
def test_a_local_enrollment_error_exits_before_any_port_is_built(
    written_enrollment, payload: dict[str, object]
) -> None:
    factory = RecordingFactory(WireBindingPort(binding_wire()))

    code = main(
        ["check", "--enrollment", str(written_enrollment(payload))], binding_port_factory=factory
    )

    assert code == EXIT_STARTUP_REFUSED
    assert factory.calls == 0, "stage 1 rejects malformed local configuration for free"


def test_an_unreadable_enrollment_file_exits_before_any_port_is_built(tmp_path, capsys) -> None:
    factory = RecordingFactory(WireBindingPort(binding_wire()))

    code = main(
        ["check", "--enrollment", str(tmp_path / "absent.json")], binding_port_factory=factory
    )

    assert code == EXIT_STARTUP_REFUSED
    assert factory.calls == 0
    assert "error:" in capsys.readouterr().err


def test_an_enrollment_file_that_is_not_json_exits_before_any_port_is_built(
    tmp_path, written_enrollment
) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    factory = RecordingFactory(WireBindingPort(binding_wire()))

    code = main(["check", "--enrollment", str(path)], binding_port_factory=factory)

    assert code == EXIT_STARTUP_REFUSED
    assert factory.calls == 0


def test_a_refused_preflight_exits_startup_refused(written_enrollment) -> None:
    code = main(
        ["check", "--enrollment", str(written_enrollment())],
        binding_port_factory=lambda _: WireBindingPort(binding_wire(workerName="somebody-else")),
    )

    assert code == EXIT_STARTUP_REFUSED


def test_an_unavailable_control_plane_exits_startup_refused(written_enrollment) -> None:
    """The CLI is not the retry policy. The adapter already spent its attempts."""

    port = InMemoryWorkerBindingPort(failure=BindingUnavailable("RepoMesh answered 503"))

    code = main(
        ["check", "--enrollment", str(written_enrollment())], binding_port_factory=lambda _: port
    )

    assert code == EXIT_STARTUP_REFUSED


def test_run_refuses_a_second_instance_for_the_same_worker(
    written_enrollment, tmp_path, default_state_home
) -> None:
    """Same arrangement as the ``check`` test below; opposite answer, on purpose."""

    factory = RecordingFactory(WireBindingPort(binding_wire()))
    held = InstanceLock(instance_lock_path(_worker_uuid()))
    held.acquire()
    try:
        code = main(
            ["run", "--enrollment", str(written_enrollment())], binding_port_factory=factory
        )
    finally:
        held.release()

    assert code == EXIT_ALREADY_RUNNING
    assert factory.port.calls == 0, "the claim is taken before preflight"


def test_check_still_works_while_another_instance_holds_the_worker(
    written_enrollment, default_state_home
) -> None:
    """A diagnostic that cannot run while the thing it diagnoses is alive is useless."""

    held = InstanceLock(instance_lock_path(_worker_uuid()))
    held.acquire()
    try:
        code = main(
            ["check", "--enrollment", str(written_enrollment())],
            binding_port_factory=lambda _: WireBindingPort(binding_wire()),
        )
    finally:
        held.release()

    assert code == EXIT_OK


def test_run_honours_an_explicit_state_directory(written_enrollment, tmp_path) -> None:
    state_dir = tmp_path / "elsewhere"
    factory = RecordingFactory(WireBindingPort(binding_wire()))
    held = InstanceLock(instance_lock_path(_worker_uuid(), state_dir))
    held.acquire()
    try:
        code = main(
            [
                "run",
                "--enrollment",
                str(written_enrollment()),
                "--state-dir",
                str(state_dir),
            ],
            binding_port_factory=factory,
        )
    finally:
        held.release()

    assert code == EXIT_ALREADY_RUNNING
    assert factory.port.calls == 0


def test_a_subcommand_is_required() -> None:
    with pytest.raises(SystemExit):
        main([])


def _worker_uuid() -> UUID:
    return UUID(WORKER_AGENT_ID)
