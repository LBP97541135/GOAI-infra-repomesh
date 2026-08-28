"""What actually happens when the Bridge executes a run it was asked for.

The wake-up half — a mention reaching RepoMesh and a receipt reaching the room —
is ``test_governed_wakeup``. This is the other half: RepoMesh leases the run back
to the Bridge, the Runner's own driver chain executes it, the Runner's own
governance gates decide whether it succeeded, and the outcome is narrated into
the thread that asked.

Three habits carry through, and the first is the point of the file:

*   **The governance gates are exercised, never described.** A run that writes
    outside its allowlist and a run whose tests fail are both driven through a
    real ``DriverExecutor`` over a real git repository, and the assertion is the
    repository afterwards: HEAD did not move. A test that stubbed the executor
    would be checking that this module can spell ``changed_path_denied``.
*   **Nothing the model said reaches a successful record.** The scripted drivers
    return a summary a model would be proud of, and the terminal message for a
    successful run is asserted not to contain it. That is the "four-layer false
    green" this line exists to kill, stated as an assertion.
*   **"Did not happen" is a counter or an empty table**, never a mock: an
    unanchored run leaves ``sends_for_trigger`` empty, a redelivered task leaves
    ``driver.requests`` at one.
"""

import asyncio
import contextlib
import logging
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from repomesh_agent_bridge.adapters.coding_session import (
    executable_path,
    session_environment,
)
from repomesh_agent_bridge.adapters.memory import (
    InMemoryGovernedTaskPort,
    InMemoryRoomPort,
    ScriptedCodingSession,
)
from repomesh_agent_bridge.adapters.restricted_process import prepare_session_dirs
from repomesh_agent_bridge.application import RoomNativeAgent
from repomesh_agent_bridge.contracts import ExternalWorkerEnrollment
from repomesh_agent_bridge.outbox import RUN_LANE, Outbox, observation_txn_id
from repomesh_agent_bridge.ports import GovernedStartReceipt, RoomRefused
from repomesh_agent_bridge.runner_consumer import (
    GOVERNED_CODEX_CONFIG,
    RUN_CRASHED_BODY,
    RUN_STARTED_BODY,
    TEST_COMPLETED_BODY,
    GovernedDriver,
    GovernedRunConsumer,
    GovernedRuntime,
    NarratingExecutor,
    ToolActionTally,
    WorkspaceNotWritable,
    _translated,
    _write_governed_codex_config,
    runner_state_root,
)
from repomesh_agent_bridge.state import BridgeState, open_state, state_path
from repomesh_agent_bridge.supervisor import GOVERNANCE_DISABLED_NOTE
from repomesh_runner.contracts import (
    ContextBundleRef,
    RepositoryCheckout,
    RunnerExecutionResult,
    RunnerPermissionMode,
    RunnerPermissions,
    RunnerResultStatus,
    RunnerTask,
    WorkspaceAssignment,
)
from repomesh_runner.drivers.base import (
    DriverEvent,
    DriverEventKind,
    DriverFamily,
    DriverRequest,
    DriverResult,
    DriverResultStatus,
    PermissionDecision,
)
from repomesh_runner.executor import AllowlistPermissionPolicy, DriverExecutor
from repomesh_runner.main import Shutdown
from repomesh_runner.main import serve as serve_runner
from repomesh_runner.profiles import get_profile
from repomesh_runner.state_store import TaskLedger

from .conftest import TEAM_ROOM, WORKER_AGENT_ID, WORKER_NAME, WireBindingPort, binding_wire
from .test_room_scope import _batch, _drive, _event

WORKER_UUID = UUID(WORKER_AGENT_ID)
TASK_ID = UUID("11111111-2222-3333-4444-555555555555")
RUN_ID = UUID("99999999-8888-7777-6666-555555555555")
TRIGGER = "$command"
NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

MODEL_PROSE = "I refactored the pricing module and everything looks great."
"""What a scripted driver claims it achieved.

Distinctive on purpose: the successful-run assertions check this exact sentence
is absent from the governance record, which only means something if the sentence
could not plausibly have come from anywhere else.
"""


# ---------------------------------------------------------------------------
# arrangement
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _state(directory: Path) -> Iterator[BridgeState]:
    state = open_state(state_path(WORKER_UUID, directory), worker_agent_id=WORKER_UUID)
    try:
        yield state
    finally:
        state.close()


def _anchor(state: BridgeState, *, trigger_event_id: str = TRIGGER) -> None:
    state.record_anchor(
        run_id=RUN_ID,
        task_id=TASK_ID,
        room_id=TEAM_ROOM,
        thread_root_id=None,
        trigger_event_id=trigger_event_id,
    )


def _task(**overrides: object) -> RunnerTask:
    values: dict[str, object] = {
        "organization_id": uuid4(),
        "project_id": uuid4(),
        "task_id": TASK_ID,
        "run_id": RUN_ID,
        "correlation_id": uuid4(),
        "attempt": 1,
        "adapter_id": "codex",
        "instruction": "raise the pricing floor",
        "repository": RepositoryCheckout(uuid4(), "https://example.com/repo.git", "main"),
        "context_bundle": ContextBundleRef(
            uuid4(), 1, "s3://bundle/manifest", "sha256:" + "0" * 64
        ),
        "permissions": RunnerPermissions(mode=RunnerPermissionMode.ACCEPT_EDITS),
        "idempotency_key": "run-99-attempt-1",
        "issued_at": NOW,
        "worker_agent_id": WORKER_UUID,
    }
    values.update(overrides)
    return RunnerTask(**values)  # type: ignore[arg-type]


def _assigned(workspace: Path) -> WorkspaceAssignment:
    return WorkspaceAssignment(workspace_id="ws-1", path=str(workspace), base_sha="abc1234")


def shell_command(body: str) -> str:
    """A shell-runnable test command that behaves the same on Windows and POSIX."""

    return f'"{sys.executable}" -c "{body}"'


def _git(workspace: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=workspace, check=True, capture_output=True, text=True
    ).stdout.strip()


def _git_workspace(root: Path, name: str = "ws") -> Path:
    """A repository with one commit, so "HEAD did not move" is assertable."""

    workspace = root / name
    workspace.mkdir(parents=True)
    _git(workspace, "init")
    (workspace / "README.md").write_text("base\n", encoding="utf-8")
    _git(workspace, "add", "-A")
    _git(
        workspace,
        "-c",
        "user.name=RepoMesh Test",
        "-c",
        "user.email=test@repomesh.local",
        "commit",
        "-m",
        "base",
    )
    return workspace


def _head(workspace: Path) -> str:
    return _git(workspace, "rev-parse", "HEAD")


class ScriptedDriver:
    """A driver that writes the files a run "produced" and emits its events.

    Real enough for the evidence gates, which read the workspace and never the
    driver: what the run changed is what is on disk when ``execute`` returns.
    """

    def __init__(
        self,
        *,
        writes: Sequence[tuple[str, str]] = (),
        events: Sequence[DriverEvent] = (),
        status: DriverResultStatus = DriverResultStatus.SUCCEEDED,
        summary: str = MODEL_PROSE,
        diagnostics: str = "",
    ) -> None:
        self._writes = tuple(writes)
        self._events = tuple(events)
        self._status = status
        self._summary = summary if status is DriverResultStatus.SUCCEEDED else ""
        self._diagnostics = diagnostics
        self.requests: list[DriverRequest] = []

    @property
    def family(self) -> DriverFamily:
        return DriverFamily.APP_SERVER

    async def execute(self, request, profile, observer) -> DriverResult:  # type: ignore[no-untyped-def]
        self.requests.append(request)
        for name, text in self._writes:
            target = request.workspace / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        for event in self._events:
            observer(event)
        return DriverResult(
            status=self._status,
            summary=self._summary,
            diagnostics=self._diagnostics,
            native_session_id="thread-1",
        )


class ExplodingDriver:
    """A driver that dies rather than returning a result."""

    @property
    def family(self) -> DriverFamily:
        return DriverFamily.APP_SERVER

    async def execute(self, request, profile, observer) -> DriverResult:  # type: ignore[no-untyped-def]
        raise OSError("the coding CLI could not be launched")


def _tool_events(*, used: int = 0, denied: int = 0) -> tuple[DriverEvent, ...]:
    return (
        *(DriverEvent(kind=DriverEventKind.TOOL_USE, payload={}) for _ in range(used)),
        *(
            DriverEvent(
                kind=DriverEventKind.PERMISSION_REQUEST,
                payload={"decision": PermissionDecision.DENY.value},
            )
            for _ in range(denied)
        ),
    )


def _narrating(
    state: BridgeState,
    driver: object,
    *,
    workspace_root: Path,
    prepare_workspace: object | None = None,
    tally: ToolActionTally | None = None,
) -> NarratingExecutor:
    """The production executor stack, with only the CLI process scripted.

    Everything between the leased task and the evidence is real: the environment
    wrapper, ``DriverExecutor``, the permission policy built from the task, the
    git evidence collection and the commit rule.
    """

    counters = tally or ToolActionTally()
    return NarratingExecutor(
        DriverExecutor(
            drivers={
                DriverFamily.APP_SERVER: GovernedDriver(
                    driver, environment={"PATH": "/nowhere"}, tally=counters
                )
            },
            workspace_root=workspace_root,
            binary_resolver=lambda names: f"C:/fake/{names[0]}.exe",
        ),
        state=state,
        outbox=Outbox(state, worker_agent_id=WORKER_UUID),
        worker_agent_id=WORKER_UUID,
        worker_name=WORKER_NAME,
        tally=counters,
        # Truthy by default because the label is a precondition now: production's
        # preparer reports whether it stuck, and a test double that said "no"
        # would refuse every run in this file for a reason none of them is about.
        prepare_workspace=prepare_workspace or (lambda path: True),
    )


class ScriptedSource:
    """Yields the scripted tasks, then stops the loop rather than blocking."""

    def __init__(self, script: Sequence[RunnerTask], shutdown: Shutdown) -> None:
        self._script = list(script)
        self._shutdown = shutdown
        self.closed = False

    async def next_task(self) -> RunnerTask | None:
        if not self._script:
            self._shutdown.set()
            return None
        return self._script.pop(0)

    async def aclose(self) -> None:
        self.closed = True


class QuietSource:
    """A source that never has work, the way a real long poll waits."""

    def __init__(self) -> None:
        self.closed = False

    async def next_task(self) -> RunnerTask | None:
        await asyncio.Event().wait()
        raise AssertionError("a quiet source is only ever left by cancellation")

    async def aclose(self) -> None:
        self.closed = True


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[object] = []
        self.closed = False

    async def publish(self, event: object, *, idempotency_key: str) -> None:
        self.events.append(event)

    async def aclose(self) -> None:
        self.closed = True


async def _run_once(
    state: BridgeState, executor: NarratingExecutor, *script: RunnerTask, ledger_dir: Path
) -> RecordingSink:
    """Drive the *real* serve loop over a scripted lease, once."""

    del state  # the executor already holds it; named here for call-site symmetry
    shutdown = Shutdown()
    sink = RecordingSink()
    await serve_runner(
        source=ScriptedSource(script, shutdown),
        sink=sink,
        executor=executor,
        ledger=TaskLedger(ledger_dir),
        shutdown=shutdown,
    )
    return sink


def _run_lane(state: BridgeState, trigger_event_id: str = TRIGGER) -> list[tuple[int, str, str]]:
    return [
        (row.ordinal, row.kind, row.body)
        for row in state.sends_for_trigger(trigger_event_id)
        if row.lane == RUN_LANE
    ]


# ---------------------------------------------------------------------------
# The gates: what the Runner refuses, and what the room is told about it
# ---------------------------------------------------------------------------


@needs_git
async def test_a_run_that_writes_outside_its_allowlist_fails_and_commits_nothing(
    tmp_path: Path,
) -> None:
    """Criterion 3, all the way through: the allowlist is enforced on *evidence*.

    The permission callback is cooperative and a CLI that ignored it would still
    have written the file, so the gate that matters is the one that reads git
    afterwards. It is the Runner's, reused rather than restated, and the proof
    that it ran is the repository: HEAD is where it was, so nothing this run did
    was recorded as work.
    """

    root = tmp_path / "worktrees"
    workspace = _git_workspace(root)
    before = _head(workspace)
    driver = ScriptedDriver(writes=(("forbidden.py", "x = 1\n"),))
    task = _task(
        permissions=RunnerPermissions(
            mode=RunnerPermissionMode.ACCEPT_EDITS, allowed_paths=("src/**",)
        ),
        workspace=_assigned(workspace),
    )

    with _state(tmp_path) as state:
        _anchor(state)
        result = await _run_and_execute(state, driver, root, task, tmp_path)

        assert result.status is RunnerResultStatus.FAILED
        assert result.summary == "changed_path_denied: forbidden.py"
        assert result.commit_sha is None
        assert _head(workspace) == before, "a denied change is never committed"

        (started, terminal) = _run_lane(state)
        assert started[:2] == (1, "run_started")
        assert terminal[:2] == (3, "run_failed")
        assert "changed_path_denied: forbidden.py" in terminal[2]


@needs_git
async def test_a_run_whose_tests_fail_is_a_failure_however_the_model_finished(
    tmp_path: Path,
) -> None:
    """Criterion 4: the task's own verification outranks the agent's claim.

    The driver reports success and says so in prose. The test command exits 1, so
    the run failed, nothing was committed, and the room is told which command and
    which exit code decided it — that pair is the whole reason the middle message
    exists.
    """

    root = tmp_path / "worktrees"
    workspace = _git_workspace(root)
    before = _head(workspace)
    failing = shell_command("raise SystemExit(1)")
    driver = ScriptedDriver(writes=(("src/pricing.py", "rate = 2\n"),))
    task = _task(workspace=_assigned(workspace), test_commands=(failing,))

    with _state(tmp_path) as state:
        _anchor(state)
        result = await _run_and_execute(state, driver, root, task, tmp_path)

        assert result.status is RunnerResultStatus.FAILED
        assert result.summary.startswith("test_command_failed:")
        assert result.commit_sha is None
        assert _head(workspace) == before, "a run whose tests fail commits nothing"

        started, tests, terminal = _run_lane(state)
        assert started[:2] == (1, "run_started")
        assert tests[:2] == (2, "test_completed")
        assert TEST_COMPLETED_BODY in tests[2]
        assert failing in tests[2] and "exit 1" in tests[2]
        assert terminal[:2] == (3, "run_failed")
        assert "tests failed (exit 1)" in terminal[2]


@needs_git
async def test_a_succeeding_run_is_recorded_from_evidence_and_never_from_prose(
    tmp_path: Path,
) -> None:
    """The truthfulness criterion, and the one this whole line exists for.

    Everything in the terminal message was *observed*: git's account of what
    changed, the commit that was made, the exit codes the task's own commands
    returned, and how many tool actions the policy saw. The model's own closing
    sentence is asserted absent, because a governance record that repeats it is
    the agent certifying its own work.
    """

    root = tmp_path / "worktrees"
    workspace = _git_workspace(root)
    driver = ScriptedDriver(
        writes=(("src/pricing.py", "rate = 2\n"),),
        events=_tool_events(used=3, denied=1),
    )
    task = _task(
        workspace=_assigned(workspace),
        test_commands=(shell_command("raise SystemExit(0)"),),
    )

    with _state(tmp_path) as state:
        _anchor(state)
        result = await _run_and_execute(state, driver, root, task, tmp_path)

        assert result.status is RunnerResultStatus.SUCCEEDED
        assert result.commit_sha == _head(workspace).lower()
        assert result.changed_files == ("src/pricing.py",)

        started, tests, terminal = _run_lane(state)
        assert [entry[:2] for entry in (started, tests, terminal)] == [
            (1, "run_started"),
            (2, "test_completed"),
            (3, "run_completed"),
        ]
        assert RUN_STARTED_BODY in started[2]
        assert "exit 0" in tests[2]
        assert "1 file(s) changed" in terminal[2]
        assert result.commit_sha[:12] in terminal[2]
        assert "tests passed" in terminal[2]
        assert "3 tool action(s), 1 denied" in terminal[2]
        assert MODEL_PROSE not in terminal[2], (
            "a successful governance record carries no sentence the model wrote"
        )


async def test_a_run_that_was_refused_every_tool_it_asked_for_still_says_so(
    tmp_path: Path,
) -> None:
    """The one shape "when N>0" would have swallowed.

    A run whose every request was refused has no tool actions to count and is
    exactly the run a room must not read as one that simply chose to change
    nothing, so the denials are reported on their own.
    """

    root = tmp_path / "worktrees"
    root.mkdir()
    driver = ScriptedDriver(events=_tool_events(denied=2))
    with _state(tmp_path) as state:
        _anchor(state)
        await _run_and_execute(state, driver, root, _task(), tmp_path)

        (_, terminal) = _run_lane(state)
        assert "0 tool action(s), 2 denied" in terminal[2]


async def test_a_run_that_could_not_be_carried_out_says_so_and_still_raises(
    tmp_path: Path,
) -> None:
    """A driver that dies is the room's business too, and the loop's.

    The room gets one canned line — no stderr, no path, no command — and the
    exception carries on to the serve loop, whose witness is the only thing that
    knows whether the key should be burned.
    """

    root = tmp_path / "worktrees"
    root.mkdir()
    with _state(tmp_path) as state:
        _anchor(state)
        executor = _narrating(state, ExplodingDriver(), workspace_root=root)

        with pytest.raises(OSError, match="could not be launched"):
            await executor.execute(_task())

        started, terminal = _run_lane(state)
        assert started[:2] == (1, "run_started")
        assert terminal[:2] == (3, "run_failed")
        assert RUN_CRASHED_BODY in terminal[2]


async def test_a_drivers_own_diagnostics_never_reach_the_room(tmp_path: Path) -> None:
    """The frozen contract bans unsanitized stderr from a room, bounded or not.

    A driver-level failure puts the CLI's stderr where the gate reasons live
    (``_to_runner_result`` maps ``diagnostics`` into ``summary``), and 200
    characters of a traceback is still a traceback. The room learns the run
    failed and where the words are; the operator's log keeps them.
    """

    root = tmp_path / "worktrees"
    root.mkdir()
    driver = ScriptedDriver(
        status=DriverResultStatus.FAILED,
        diagnostics="Traceback: boom at C:\\Users\\operator\\stack.py",
    )
    with _state(tmp_path) as state:
        _anchor(state)
        await _run_and_execute(state, driver, root, _task(), tmp_path)

        (_, terminal) = _run_lane(state)
        assert terminal[:2] == (3, "run_failed")
        assert "Traceback" not in terminal[2]
        assert "operator" not in terminal[2]
        assert "log has the details" in terminal[2]


# ---------------------------------------------------------------------------
# Redelivery, and the runs nobody in a room asked for
# ---------------------------------------------------------------------------


async def test_the_same_lease_delivered_twice_executes_once_and_narrates_once(
    tmp_path: Path,
) -> None:
    """Criterion 5: the ledger is the local half of at-most-once execution.

    Both layers are visible here at the same time and they are different layers.
    The ledger stops the second execution — real side effects in a real
    workspace are what it exists to prevent — and the outbox's derived names
    would have stopped a second *message* even if it had not, because a replayed
    lifecycle position is a no-op by construction.
    """

    root = tmp_path / "worktrees"
    root.mkdir()
    driver = ScriptedDriver()
    with _state(tmp_path) as state:
        _anchor(state)
        await _run_once(
            state,
            _narrating(state, driver, workspace_root=root),
            _task(),
            _task(),
            ledger_dir=tmp_path / "ledger",
        )

        assert len(driver.requests) == 1, "the second lease is skipped, not re-run"
        assert [entry[:2] for entry in _run_lane(state)] == [
            (1, "run_started"),
            (3, "run_completed"),
        ]


async def test_a_run_nobody_started_from_a_room_is_executed_in_silence(
    tmp_path: Path,
) -> None:
    """No anchor means no thread, and a message with nowhere to go is not owed.

    A run RepoMesh dispatched on its own was never asked for in a Matrix room.
    Its structured truth still leaves through the event sink; what does not
    happen is a lifecycle appearing in a conversation that never mentioned it.
    """

    root = tmp_path / "worktrees"
    root.mkdir()
    driver = ScriptedDriver()
    with _state(tmp_path) as state:
        result = await _run_and_execute(state, driver, root, _task(), tmp_path)

        assert result.status is RunnerResultStatus.SUCCEEDED
        assert len(driver.requests) == 1, "an unanchored run still runs"
        assert state.sends_for_trigger(TRIGGER) == ()
        assert state.pending_sends() == ()


# ---------------------------------------------------------------------------
# The two seams the Runner cannot supply for itself
# ---------------------------------------------------------------------------


async def test_the_governed_driver_hands_the_child_exactly_the_six_allowed_keys(
    tmp_path: Path, monkeypatch
) -> None:
    """Criterion 6 (J-12): ``DriverExecutor`` builds an empty environment.

    The restricted factory never merges ``os.environ`` either — deliberately, so
    nothing of the operator's leaks — which together mean a governed codex would
    spawn with no ``SystemRoot``, no ``PATH`` and no ``CODEX_HOME`` and die
    before saying anything. The wrapper supplies the same six keys the
    conversation track uses, from the same function, pointed at the same worker's
    session directories.
    """

    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    monkeypatch.setenv("windir", r"C:\Windows")
    monkeypatch.setenv("REPOMESH_BRIDGE_TOKEN", "s3cret-runner-control-token")
    dirs = prepare_session_dirs(tmp_path / "session")
    driver = ScriptedDriver()
    wrapper = GovernedDriver(
        driver,
        environment=session_environment(
            dirs, executable_path(str(tmp_path / "node/node.exe"), str(tmp_path / "npm/codex.cmd"))
        ),
        tally=ToolActionTally(),
    )

    await wrapper.execute(
        DriverRequest(
            executable="codex",
            workspace=dirs.workspace,
            prompt="do the work",
            permission_policy=_AlwaysAllow(),
        ),
        get_profile("codex"),
        lambda event: None,
    )

    environment = driver.requests[0].environment
    assert set(environment) == {"SystemRoot", "windir", "CODEX_HOME", "TMP", "TEMP", "PATH"}
    assert environment["CODEX_HOME"] == str(dirs.codex_home)
    assert environment["TMP"] == environment["TEMP"] == str(dirs.tmp)
    assert "node" in environment["PATH"] and "npm" in environment["PATH"]
    assert "REPOMESH_BRIDGE_TOKEN" not in environment
    assert wrapper.family is DriverFamily.APP_SERVER


async def test_the_platform_worktree_is_relabelled_once_and_only_when_assigned(
    tmp_path: Path,
) -> None:
    """Criterion 7 (J-13): the restricted child must be able to write its worktree.

    Every CLI this Bridge launches runs on a Low-integrity token, and the
    worktree RepoMesh prepared carries the default Medium label, so without this
    the agent reads its repository and changes nothing in it. A task with no
    assignment is left alone: it runs in the executor's own fallback directory,
    which this process created and already owns.
    """

    root = tmp_path / "worktrees"
    workspace = root / "ws"
    workspace.mkdir(parents=True)
    labelled: list[Path] = []

    def label(path: Path) -> bool:
        labelled.append(path)
        return True

    with _state(tmp_path) as state:
        executor = _narrating(
            state,
            ScriptedDriver(),
            workspace_root=root,
            prepare_workspace=label,
        )

        await executor.execute(_task(workspace=_assigned(workspace)))
        assert labelled == [workspace]

        await executor.execute(_task(idempotency_key="run-99-attempt-2"))
        assert labelled == [workspace], "a task with no assignment names no worktree"


# ---------------------------------------------------------------------------
# Criterion 8: two loops in one process, and one way out of both
# ---------------------------------------------------------------------------


async def test_a_consumer_that_dies_takes_the_room_loop_down_and_unwinds_cleanly(
    enrollment: ExternalWorkerEnrollment, tmp_path: Path, matrix_token: str
) -> None:
    """Half a Bridge is the failure that looks alive, so there is no half.

    A Bridge that went on syncing a room while it had silently stopped executing
    the runs that room asked for would answer mentions and swallow every task, so
    the consumer's death ends the run. What the caller then sees is the
    consumer's own exception — not the task group's wrapper around it — because
    the CLI's exit mapping is written in exception types, and ``pytest.raises``
    of the leaf type is what pins that.

    Driven by hand rather than through ``_drive``: the crash has to land after
    the room loop has committed a position, so the test waits for the scripted
    homeserver to go quiet and only then tells the consumer to die.
    """

    room = InMemoryRoomPort(_batch(next_batch="s-0"))
    session = ScriptedCodingSession()
    consumer = _ConsumerThatDies()

    task = asyncio.create_task(
        _agent(tmp_path=tmp_path, room=room, session=session, consumer=consumer).run(enrollment)
    )
    await asyncio.wait_for(room.idle.wait(), timeout=5)
    consumer.stop.set()
    with pytest.raises(_ConsumerDied):
        await asyncio.wait_for(task, timeout=5)

    assert consumer.served
    assert room.closed and session.closed, "the exit stack unwound both seams in reverse"
    with _state(tmp_path) as state:
        cursor = state.cursor()
        assert cursor is not None and cursor.since_token == "s-0", (
            "the state file was closed cleanly and reopens where the room loop left it"
        )


async def test_a_homeserver_that_refuses_the_sync_stops_the_consumer_too(
    enrollment: ExternalWorkerEnrollment, tmp_path: Path, matrix_token: str
) -> None:
    """The other direction, with a real consumer so the close is real.

    A revoked token ends the run, and the run ending has to reach the loop that
    holds two HTTP clients: they are closed on the way out because they were
    opened in a ``finally``'s reach, not because anything remembered to.
    """

    room = InMemoryRoomPort(
        _batch(next_batch="s-0"),
        RoomRefused("the homeserver refused GET /_matrix/client/v3/sync with 401"),
    )
    source, sink = QuietSource(), RecordingSink()
    consumer = GovernedRunConsumer(
        source=source,
        sink=sink,
        executor=_StubExecutor(),
        ledger=TaskLedger(tmp_path / "ledger"),
    )

    ended = await _drive(
        _agent(
            tmp_path=tmp_path,
            room=room,
            session=ScriptedCodingSession(),
            consumer=consumer,
        ).run(enrollment),
        room,
    )

    assert isinstance(ended, RoomRefused)
    assert source.closed and sink.closed, "the consumer handed both clients back"
    assert room.closed


async def test_a_governed_instance_answers_a_command_and_says_so_in_its_ready_line(
    enrollment: ExternalWorkerEnrollment, tmp_path: Path, matrix_token: str, caplog
) -> None:
    """The two halves arrive together, and the log says which arrangement this is.

    ``governed=on`` is the one line an operator has to tell a conversation-only
    Bridge from one that will execute work; the receipt in the room is the proof
    that the wake-up port was actually wired into the supervisor.
    """

    caplog.set_level(logging.INFO)
    room = InMemoryRoomPort(
        _batch(next_batch="s-0"),
        _batch(_event(TRIGGER, body=f"@worker start task {TASK_ID}"), next_batch="s-1"),
    )
    governed_port = InMemoryGovernedTaskPort(GovernedStartReceipt(run_id=RUN_ID, task_id=TASK_ID))

    await _drive(
        _agent(
            tmp_path=tmp_path,
            room=room,
            session=ScriptedCodingSession(),
            consumer=_IdleConsumer(),
            task_port=governed_port,
        ).run(enrollment),
        room,
    )

    assert "governed=on" in caplog.text
    assert len(governed_port.calls) == 1
    assert room.sent[0].txn_id == observation_txn_id(TRIGGER, RUN_LANE, 0)


async def test_a_conversation_only_instance_starts_no_consumer_and_says_so(
    enrollment: ExternalWorkerEnrollment, tmp_path: Path, matrix_token: str, caplog
) -> None:
    """The default arrangement, asserted as the absence it is.

    No consumer task exists, the supervisor holds no control plane, and somebody
    who asks for a run is told so rather than left to guess.
    """

    caplog.set_level(logging.INFO)
    before = asyncio.all_tasks()
    room = InMemoryRoomPort(
        _batch(next_batch="s-0"),
        _batch(_event(TRIGGER, body=f"@worker start task {TASK_ID}"), next_batch="s-1"),
    )

    await _drive(
        _agent(tmp_path=tmp_path, room=room, session=ScriptedCodingSession()).run(enrollment),
        room,
    )

    assert "governed=off" in caplog.text
    assert [message.body for message in room.sent] == [f"[note] {GOVERNANCE_DISABLED_NOTE}"]
    assert not (asyncio.all_tasks() - before), "no second loop was started"


# ---------------------------------------------------------------------------
# The two vocabularies that meet at the lease
# ---------------------------------------------------------------------------

LIVE_GRANT = RunnerPermissions(
    mode=RunnerPermissionMode.ACCEPT_EDITS,
    allowed_tools=(
        "read",
        "edit",
        "test",
        "repomesh.start_assigned_task",
        "context7.resolve-library-id",
        "context7.query-docs",
    ),
    disallowed_tools=("git-push", "github.contents.write", "github.pull_requests.merge"),
    allowed_paths=("src/**", "scripts/**", "scripts/run_tests.py"),
    denied_paths=(".git/**", ".github/workflows/**"),
)
"""The permissions RepoMesh actually dispatched on 2026-08-28.

Copied from the run's own dispatch row rather than invented, because the defect
this pins was invisible to every grant a test had written for itself: the words
are RepoMesh's real capability vocabulary, and none of them is a name codex uses.
"""


def _decide(task: RunnerTask, workspace: Path, tool_name: str, tool_input: dict[str, object]):
    """What the *real* policy says about a tool, for the task as executed."""

    return AllowlistPermissionPolicy(task.permissions, workspace).decide(tool_name, tool_input)


def test_a_codex_grant_reaches_the_policy_in_codexs_own_words(tmp_path: Path) -> None:
    """Criterion: a governed codex may do what its grant already permitted.

    The live failure, replayed. RepoMesh grants ``edit`` and ``test``; codex asks
    under ``fileChange`` and ``commandExecution``; ``AllowlistPermissionPolicy``
    compares the two by set membership and refuses everything — four requests,
    four denials, nothing written, and a run that failed on tests its agent had
    never been allowed to affect.

    The assertions are made against the real policy over the real dispatched
    grant, so this passes only while the two vocabularies genuinely meet. The
    "before" half is asserted too: a translation that stopped being necessary
    would make this test pass for the wrong reason.
    """

    workspace = tmp_path / "ws"
    workspace.mkdir()
    inside = {"type": "fileChange", "changes": [{"path": str(workspace / "src" / "pricing.py")}]}
    outside = {"type": "fileChange", "changes": [{"path": str(workspace / "secrets" / "id.pem")}]}
    command = {"type": "commandExecution", "command": ["python", "scripts/run_tests.py"]}

    raw = _task(permissions=LIVE_GRANT, workspace=_assigned(workspace))
    assert _decide(raw, workspace, "fileChange", inside) is PermissionDecision.DENY
    assert _decide(raw, workspace, "commandExecution", command) is PermissionDecision.DENY

    translated = _translated(raw)
    assert _decide(translated, workspace, "fileChange", inside) is PermissionDecision.ALLOW
    assert _decide(translated, workspace, "commandExecution", command) is PermissionDecision.ALLOW
    assert _decide(translated, workspace, "fileChange", outside) is PermissionDecision.DENY, (
        "the path allowlist decides before the tool name does, and still must"
    )


def test_the_translation_adds_codex_words_and_takes_nothing_away(tmp_path: Path) -> None:
    """It is a translation: every granted word survives, and only codex gains."""

    del tmp_path
    translated = _translated(_task(permissions=LIVE_GRANT))
    tools = translated.permissions.allowed_tools

    assert set(LIVE_GRANT.allowed_tools) <= set(tools)
    assert set(tools) - set(LIVE_GRANT.allowed_tools) == {"fileChange", "commandExecution"}
    assert len(tools) == len(set(tools)), "RunnerPermissions rejects duplicates outright"
    twice = _translated(translated).permissions.allowed_tools
    assert twice == tools, "translating an already-translated task is a no-op"


def test_a_grant_the_translation_has_no_business_touching_is_left_alone() -> None:
    """Three tasks it must pass through: another runtime's, an unscoped one, and
    one whose grant carries neither of the capabilities that map."""

    other_runtime = _task(adapter_id="claude-code", permissions=LIVE_GRANT)
    assert _translated(other_runtime) is other_runtime

    unscoped = _task(permissions=RunnerPermissions(mode=RunnerPermissionMode.ACCEPT_EDITS))
    assert _translated(unscoped) is unscoped, (
        "an empty allowlist means no tool-name rule at all; filling it would scope "
        "a task nobody scoped"
    )

    read_only = _task(
        permissions=RunnerPermissions(
            mode=RunnerPermissionMode.ACCEPT_EDITS, allowed_tools=("read",)
        )
    )
    assert _translated(read_only) is read_only


async def test_every_permission_decision_reaches_the_operators_log(
    tmp_path: Path, caplog
) -> None:
    """Criterion: a denial is explainable without reading codex's rollout files.

    The room is told how many actions were refused and deliberately not which —
    a room is not a transcript — and the driver's own event carries the tool name
    but never what the tool was pointed at. So the machine's log is the only
    place the question "why did this run do nothing" can be answered, and this
    pins that it is answered there.
    """

    workspace = tmp_path / "ws"
    workspace.mkdir()
    task = _translated(_task(permissions=LIVE_GRANT, workspace=_assigned(workspace)))
    wrapper = GovernedDriver(
        (driver := _AskingDriver()), environment={"PATH": "/nowhere"}, tally=ToolActionTally()
    )

    with caplog.at_level(logging.INFO, logger="repomesh_agent_bridge.runner_consumer"):
        await wrapper.execute(
            DriverRequest(
                executable="codex",
                workspace=workspace,
                prompt="do the work",
                permission_policy=AllowlistPermissionPolicy(task.permissions, workspace),
            ),
            get_profile("codex"),
            lambda event: None,
        )

    lines = [record.getMessage() for record in caplog.records]
    assert any("permission deny" in line and "id.pem" in line for line in lines), (
        "a denial names what was refused"
    )
    assert any("permission allow" in line and "pricing.py" in line for line in lines)
    assert driver.decisions == [PermissionDecision.ALLOW, PermissionDecision.DENY], (
        "the wrapper reports the policy's verdict and never its own"
    )


# ---------------------------------------------------------------------------
# A worktree the child cannot write is a run that cannot happen
# ---------------------------------------------------------------------------


async def test_a_worktree_that_cannot_be_labelled_ends_the_run_before_the_cli_starts(
    tmp_path: Path,
) -> None:
    """Criterion (D-4): the label is a precondition, not a diagnostic.

    Writing a mandatory label needs ``WRITE_OWNER``, which an inherited
    ``Modify`` grant does not carry, so a workspace root outside the operator's
    own profile keeps its Medium label and the Low-integrity child can write
    nothing in it. Measured live: the run went ahead anyway, changed nothing, and
    the room was told its tests had failed — a true sentence about a workspace the
    agent had never been able to touch.

    Three things are asserted, and the third is the reason for the other two: the
    driver is never reached, the room hears the existing "could not be carried
    out" line rather than a new word in a frozen schema, and the exception
    escapes so the Runner's own failure path reports it to the control plane.
    """

    root = tmp_path / "worktrees"
    workspace = root / "ws"
    workspace.mkdir(parents=True)
    driver = ScriptedDriver(writes=(("src/pricing.py", "changed\n"),))

    with _state(tmp_path) as state:
        _anchor(state)
        executor = _narrating(
            state,
            driver,
            workspace_root=root,
            prepare_workspace=lambda path: False,
        )

        with pytest.raises(WorkspaceNotWritable):
            await executor.execute(_task(workspace=_assigned(workspace)))

        assert driver.requests == [], "no CLI is launched against a workspace it cannot write"
        started, terminal = _run_lane(state)
        assert started[:2] == (1, "run_started") and RUN_STARTED_BODY in started[2]
        assert terminal[:2] == (3, "run_failed") and RUN_CRASHED_BODY in terminal[2]


async def test_a_run_nobody_started_from_a_room_still_needs_a_writable_worktree(
    tmp_path: Path,
) -> None:
    """The silent path is silent about narration, not about preconditions."""

    root = tmp_path / "worktrees"
    workspace = root / "ws"
    workspace.mkdir(parents=True)
    driver = ScriptedDriver()

    with _state(tmp_path) as state:
        executor = _narrating(
            state, driver, workspace_root=root, prepare_workspace=lambda path: False
        )
        with pytest.raises(WorkspaceNotWritable):
            await executor.execute(_task(workspace=_assigned(workspace)))
        assert driver.requests == []


# ---------------------------------------------------------------------------
# codex's own configuration for a governed run
# ---------------------------------------------------------------------------


def test_a_governed_codex_home_is_configured_to_ask_before_it_acts(tmp_path: Path) -> None:
    """Criterion (D-3): drop codex's nested sandbox, keep its approvals.

    codex's Windows filesystem sandbox builds a restricted token, which a process
    already running at Low integrity cannot do, so every ``apply_patch`` died
    inside the helper before it reached an approval. Turning that sandbox off is
    sound because it was never the boundary — the restricted token and the single
    Low-labelled worktree are — but it is only sound *while codex still asks*,
    because the approval callback is where this Bridge's decisions are made and
    where the conversation track's deny-all is enforced.

    So the asking half is asserted as an invariant, and it has already been lost
    once: ``sandbox_mode = "danger-full-access"`` removes the sandbox and with it
    every approval, measured live at five governed tool actions and four
    conversation ones with not one request between them. The sandbox is therefore
    disabled at its *backend* while the mode stays one that has to ask.
    """

    settings = tomllib.loads(GOVERNED_CODEX_CONFIG)
    features = settings["features"]
    assert features["experimental_windows_sandbox"] is False
    assert features["elevated_windows_sandbox"] is False
    assert settings["sandbox_mode"] != "danger-full-access", (
        "a sandbox with nothing to escalate out of is a codex that never asks"
    )
    # codex 0.149.1's own list, quoted from the error it raised at a value that
    # was not on it: untrusted, on-failure, on-request, granular, never — and
    # "untrusted" parses but is refused at startup. Only "never" stops the
    # approvals outright, and an unparseable value is worse than either: codex
    # logs one line to stderr and silently falls back to its defaults.
    assert settings["approval_policy"] in {"on-failure", "on-request", "granular"}

    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    _write_governed_codex_config(codex_home)
    assert (codex_home / "config.toml").read_text(encoding="utf-8") == GOVERNED_CODEX_CONFIG

    # codex-home is Low-labelled so the CLI can keep its own state there, which
    # means a Low process can edit this file; a Bridge that rewrote it blindly
    # would erase the evidence that one had.
    (codex_home / "config.toml").write_text("sandbox_mode = 'read-only'\n", encoding="utf-8")
    _write_governed_codex_config(codex_home)
    assert (codex_home / "config.toml").read_text(encoding="utf-8") == GOVERNED_CODEX_CONFIG


# ---------------------------------------------------------------------------
# Where this worker's Runner ledger lives
# ---------------------------------------------------------------------------


def test_the_runner_ledger_is_per_worker_and_sits_beside_the_rest_of_its_state(
    tmp_path: Path,
) -> None:
    """A ledger shared between workers would suppress one worker's task with
    another's finished key, so it carries the same worker dimension the state
    file and the instance lock already carry."""

    other = UUID("00000000-0000-0000-0000-0000000000bb")

    mine = runner_state_root(WORKER_UUID, tmp_path)
    theirs = runner_state_root(other, tmp_path)

    assert mine != theirs
    assert mine.parent == tmp_path / "runner"
    assert mine.parent.parent == state_path(WORKER_UUID, tmp_path).parent.parent


# ---------------------------------------------------------------------------
# local doubles and helpers
# ---------------------------------------------------------------------------


class _AlwaysAllow:
    def decide(self, tool_name: str, tool_input: object) -> PermissionDecision:
        return PermissionDecision.ALLOW


class _AskingDriver:
    """A driver that asks its request's policy about two files and remembers the
    answers, the way codex asks over the app-server protocol."""

    def __init__(self) -> None:
        self.decisions: list[PermissionDecision] = []

    @property
    def family(self) -> DriverFamily:
        return DriverFamily.APP_SERVER

    async def execute(self, request, profile, observer) -> DriverResult:  # type: ignore[no-untyped-def]
        for target in ("src/pricing.py", "secrets/id.pem"):
            self.decisions.append(
                request.permission_policy.decide(
                    "fileChange",
                    {"type": "fileChange", "changes": [{"path": str(request.workspace / target)}]},
                )
            )
        return DriverResult(
            status=DriverResultStatus.SUCCEEDED, summary="", diagnostics="", native_session_id=None
        )


class _StubExecutor:
    async def execute(self, task: RunnerTask) -> RunnerExecutionResult:
        raise AssertionError("this consumer is never given work")


class _ConsumerDied(RuntimeError):
    """The Runner loop stopped for a reason of its own."""


class _ConsumerThatDies:
    """Dies when the test says so, so the crash lands in a known window."""

    def __init__(self) -> None:
        self.stop = asyncio.Event()
        self.served = False

    async def serve(self) -> None:
        self.served = True
        await self.stop.wait()
        raise _ConsumerDied("the runner loop died")


class _IdleConsumer:
    """A consumer with nothing to do, which is a real deployment."""

    def __init__(self) -> None:
        self.served = False

    async def serve(self) -> None:
        self.served = True
        await asyncio.Event().wait()


def _agent(
    *,
    tmp_path: Path,
    room: InMemoryRoomPort,
    session: ScriptedCodingSession,
    consumer: object | None = None,
    task_port: object | None = None,
) -> RoomNativeAgent:
    governed = (
        None
        if consumer is None
        else GovernedRuntime(
            task_port=task_port or InMemoryGovernedTaskPort(
                GovernedStartReceipt(run_id=RUN_ID, task_id=TASK_ID)
            ),
            build_consumer=lambda state: consumer,  # type: ignore[arg-type,return-value]
        )
    )
    return RoomNativeAgent(
        binding_port=WireBindingPort(binding_wire()),
        room_port=room,
        coding_session=session,
        state_dir=tmp_path,
        governed=governed,  # type: ignore[arg-type]
    )


async def _run_and_execute(
    state: BridgeState,
    driver: object,
    workspace_root: Path,
    task: RunnerTask,
    tmp_path: Path,
) -> RunnerExecutionResult:
    """One leased task through the real stack, returning what the Runner decided."""

    return await _narrating(state, driver, workspace_root=workspace_root).execute(task)
