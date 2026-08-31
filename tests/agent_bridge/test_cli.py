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
from uuid import UUID, uuid4

import pytest

from repomesh_agent_bridge.adapters.coding_session import DriverCodingSession
from repomesh_agent_bridge.adapters.memory import (
    InertCodingSession,
    InMemoryGovernedTaskPort,
    InMemoryWorkerBindingPort,
)
from repomesh_agent_bridge.adapters.restricted_process import RestrictedProcessFactory
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
from repomesh_agent_bridge.ports import GovernedStartReceipt
from repomesh_agent_bridge.runner_consumer import GovernedRuntime
from repomesh_runner.drivers.app_server import AppServerDriver

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


# -- PR 4: real session assembly (H-6) ------------------------------------


def test_run_without_inert_refuses_a_profile_without_a_real_adapter(
    written_enrollment, capsys, default_state_home
) -> None:
    """kimi and claude-code have no real adapter this build; refusing beats a
    silent downgrade to the inert stand-in, which would look like it can code."""

    payload = enrollment_wire(codingProfile="kimi")

    code = main(
        ["run", "--enrollment", str(written_enrollment(payload))],
        binding_port_factory=lambda _: WireBindingPort(binding_wire()),
    )

    assert code == EXIT_STARTUP_REFUSED
    err = capsys.readouterr().err
    assert "kimi" in err
    assert "--inert" in err, "the refusal names the deliberate way to ask for the stand-in"


def test_run_with_inert_serves_any_profile(written_enrollment, default_state_home) -> None:
    """``--inert`` keeps PR 3's behaviour: it assembles the stand-in for any
    profile, so a run that reaches the instance lock proves assembly did not
    refuse the profile the way the default path would."""

    payload = enrollment_wire(codingProfile="claude-code")
    held = InstanceLock(instance_lock_path(_worker_uuid()))
    held.acquire()
    try:
        code = main(
            ["run", "--inert", "--enrollment", str(written_enrollment(payload))],
            binding_port_factory=lambda _: WireBindingPort(binding_wire()),
        )
    finally:
        held.release()

    assert code == EXIT_ALREADY_RUNNING, "assembly accepted the profile and reached the lock"


def test_run_exits_startup_refused_when_the_cli_is_missing(
    written_enrollment, capsys, default_state_home, tmp_path
) -> None:
    """A codex enrollment whose binary cannot be found is a startup refusal that
    names the CLI, mapped to exit 2 exactly like every other refusal to serve."""

    def missing_codex(enrollment: ExternalWorkerEnrollment) -> DriverCodingSession:
        factory = RestrictedProcessFactory()
        return DriverCodingSession(
            AppServerDriver(factory),
            factory,
            session_dir=tmp_path / "session",
            worker_name=enrollment.worker_name,
            resolve_binary=lambda names: None,
        )

    code = main(
        ["run", "--enrollment", str(written_enrollment())],
        binding_port_factory=lambda _: WireBindingPort(binding_wire()),
        coding_session_factory=missing_codex,
    )

    assert code == EXIT_STARTUP_REFUSED
    assert "codex" in capsys.readouterr().err


# -- PR 5: governed execution is one switch (J-10) -------------------------


class RecordingGovernedFactory:
    """Stands in for the control plane and the Runner loop, and counts.

    Neither half can be built for real in a test — one opens an HTTP client
    against RepoMesh, the other would lease work — so what these tests assert is
    that the CLI decided to build them, which is exactly the decision
    ``--workspace-root`` exists to make.
    """

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, enrollment: ExternalWorkerEnrollment) -> GovernedRuntime:
        del enrollment
        self.calls += 1
        return GovernedRuntime(
            task_port=InMemoryGovernedTaskPort(
                GovernedStartReceipt(run_id=uuid4(), task_id=uuid4())
            ),
            build_consumer=lambda state: _NeverServes(),
        )


class _NeverServes:
    async def serve(self) -> None:
        raise AssertionError("these tests stop before anything serves")


def _inert_session(enrollment: ExternalWorkerEnrollment) -> InertCodingSession:
    return InertCodingSession(worker_name=enrollment.worker_name)


def test_governed_execution_and_the_inert_stand_in_cannot_both_be_asked_for(
    written_enrollment, tmp_path, capsys, default_state_home
) -> None:
    """One command line asking for two incompatible things is refused, not guessed.

    ``--inert`` is the deliberate way to bring a Bridge up with something that
    codes nothing; ``--workspace-root`` is the deliberate way to make it execute
    governed runs. Picking a winner would either start runs against a stand-in or
    silently drop a flag an operator typed.
    """

    code = main(
        [
            "run",
            "--inert",
            "--workspace-root",
            str(tmp_path),
            "--enrollment",
            str(written_enrollment()),
        ],
        binding_port_factory=lambda _: WireBindingPort(binding_wire()),
    )

    err = capsys.readouterr().err
    assert code == EXIT_STARTUP_REFUSED
    assert "--workspace-root" in err and "--inert" in err


def test_a_workspace_root_that_does_not_exist_is_refused_at_startup(
    written_enrollment, tmp_path, capsys, default_state_home
) -> None:
    """The platform prepares worktrees *under* this directory.

    Discovering it is missing at the first lease means a task already dispatched
    to a worker that cannot run it, so the check is at startup where the only
    cost is a restart.
    """

    missing = tmp_path / "no-such-directory"

    code = main(
        [
            "run",
            "--workspace-root",
            str(missing),
            "--enrollment",
            str(written_enrollment()),
        ],
        binding_port_factory=lambda _: WireBindingPort(binding_wire()),
    )

    assert code == EXIT_STARTUP_REFUSED
    assert str(missing) in capsys.readouterr().err


def test_governed_execution_is_refused_for_a_profile_with_no_real_adapter(
    written_enrollment, tmp_path, capsys, default_state_home
) -> None:
    """H-6 again, on the execution side and independently of the session's copy.

    A test may substitute the coding session; it must not thereby acquire a
    governed runtime the real assembly would have refused, so the profile is
    checked where the runtime is built as well as where the session is.
    """

    payload = enrollment_wire(codingProfile="kimi")

    code = main(
        [
            "run",
            "--workspace-root",
            str(tmp_path),
            "--enrollment",
            str(written_enrollment(payload)),
        ],
        binding_port_factory=lambda _: WireBindingPort(binding_wire()),
        coding_session_factory=_inert_session,
    )

    assert code == EXIT_STARTUP_REFUSED
    assert "kimi" in capsys.readouterr().err


def test_governed_execution_without_a_repomesh_credential_is_refused(
    written_enrollment, tmp_path, capsys, default_state_home
) -> None:
    """The lease, the run events and the start action are all authenticated as
    this worker, and RepoMesh scopes the lease to the token that names it — so an
    enrollment with no ``repomesh`` reference cannot execute anything, whatever
    the preflight port thought it needed."""

    payload = enrollment_wire(credentialRefs={"matrix": "env:MATRIX"})

    code = main(
        [
            "run",
            "--workspace-root",
            str(tmp_path),
            "--enrollment",
            str(written_enrollment(payload)),
        ],
        binding_port_factory=lambda _: WireBindingPort(binding_wire()),
        coding_session_factory=_inert_session,
    )

    assert code == EXIT_STARTUP_REFUSED
    assert "credentialRefs.repomesh" in capsys.readouterr().err


def test_a_workspace_root_builds_the_governed_runtime_the_agent_is_handed(
    written_enrollment, tmp_path, default_state_home
) -> None:
    """The wiring assertion, read off the seam rather than off a private field.

    The instance claim is taken inside ``run``, so an invocation that reaches
    ``EXIT_ALREADY_RUNNING`` is one whose agent was fully assembled — and the
    factory's counter says the governed half was part of that assembly.
    """

    governed = RecordingGovernedFactory()
    held = InstanceLock(instance_lock_path(_worker_uuid()))
    held.acquire()
    try:
        code = main(
            [
                "run",
                "--workspace-root",
                str(tmp_path),
                "--enrollment",
                str(written_enrollment()),
            ],
            binding_port_factory=lambda _: WireBindingPort(binding_wire()),
            coding_session_factory=_inert_session,
            governed_runtime_factory=governed,
        )
    finally:
        held.release()

    assert code == EXIT_ALREADY_RUNNING
    assert governed.calls == 1


def test_a_run_without_a_workspace_root_builds_no_governed_runtime_at_all(
    written_enrollment, default_state_home
) -> None:
    """The default is conversation-only, and it is an absence rather than a flag.

    Nothing is constructed, so there is no control plane to reach and no second
    loop to start — which is what makes "this instance cannot start governed
    runs" a true thing for the supervisor to say.
    """

    governed = RecordingGovernedFactory()
    held = InstanceLock(instance_lock_path(_worker_uuid()))
    held.acquire()
    try:
        code = main(
            ["run", "--enrollment", str(written_enrollment())],
            binding_port_factory=lambda _: WireBindingPort(binding_wire()),
            coding_session_factory=_inert_session,
            governed_runtime_factory=governed,
        )
    finally:
        held.release()

    assert code == EXIT_ALREADY_RUNNING
    assert governed.calls == 0


def test_check_reports_that_it_wires_no_governed_execution(written_enrollment, capsys) -> None:
    """``check`` gained a third absence to report and none of the behaviour."""

    code = main(
        ["check", "--enrollment", str(written_enrollment())],
        binding_port_factory=lambda _: WireBindingPort(binding_wire()),
    )

    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "governed execution: not wired" in out


def _worker_uuid() -> UUID:
    return UUID(WORKER_AGENT_ID)
