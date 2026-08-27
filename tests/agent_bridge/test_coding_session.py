"""Contract tests for the real codex coding-session adapter (PR 4, W3).

Two kinds of double appear here, on purpose:

* A **scripted fake process** replays codex's JSON-RPC frames through the *real*
  ``AppServerDriver``, so the wire-level contracts — resume becomes
  ``thread/resume``, a tool request is answered ``denied``, no THINKING or tool
  frame reaches an observation, a cancelled turn reaps its process — are checked
  against the driver the Bridge actually ships, not a re-implementation of it.
  Its shape mirrors ``tests/runner/test_app_server_driver.py`` but is written out
  here rather than imported: this suite does not reach across into the runner's
  fixtures.
* A **fake driver** returns one chosen ``DriverResult`` so every one of the five
  terminal statuses maps deterministically, including INPUT_REQUIRED, which a
  deny-all policy can never provoke through the real driver.

The startup gate is exercised with a fake probe factory whose ``probe`` and
handshake spawn are scripted, so the three refusals — missing binary, isolation
not proven, codex not logged in — are reached without a real restricted spawn.
"""

import asyncio
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import pytest

from repomesh_agent_bridge.adapters.coding_session import (
    _BLOCKED_NOTE,
    _DENIAL_DISCLOSURE,
    _EMPTY_SUCCESS_NOTE,
    DriverCodingSession,
    _DenyAllPolicy,
    session_root,
)
from repomesh_agent_bridge.adapters.restricted_process import IsolationCheck, IsolationReport
from repomesh_agent_bridge.contracts import SessionNotReady
from repomesh_agent_bridge.ports import TurnRequest
from repomesh_runner.drivers.app_server import AppServerDriver
from repomesh_runner.drivers.base import (
    DriverEvent,
    DriverEventKind,
    DriverFamily,
    DriverResult,
    DriverResultStatus,
    PermissionDecision,
)

# -- shared identities ----------------------------------------------------

ROOM = "!team-pricing:matrix.example.org"
THREAD = "thread-root-1"
TRIGGER = "$mention-1"
WORKER = "pricing-codex-worker"
THREAD_ID = "019fc762-d677-7cd3-80e6-cd25db68a7a7"
TURN_ID = "019fc762-da53-7750-b01a-6862c23e6382"
TRANSCRIPT = "C:\\Users\\dev\\.codex\\rollout-019fc762.jsonl"
PAUSE = 0.02


def _turn(*, prompt: str = "refactor the parser", native: str | None = None) -> TurnRequest:
    return TurnRequest(
        room_id=ROOM,
        thread_id=THREAD,
        trigger_event_id=TRIGGER,
        prompt=prompt,
        native_session_id=native,
    )


# -- scripted fake process (self-contained; not imported from the runner) --


class _FakeProcess:
    """Emits scripted stdout lines, records stdin, mirrors a ProcessHandle.

    Lines may be a dict/str/bytes (emitted), a float (async delay) or a callable
    of the stdin frames so far. Shapes match the runner's own fake exactly so the
    driver cannot tell them apart.
    """

    def __init__(self, script=(), *, exit_code: int = 0, stderr: str = "") -> None:
        self.script = list(script)
        self.exit_code = exit_code
        self._stderr = stderr
        self.stdin_frames: list[bytes] = []
        self.stdin_closed = False
        self.terminated = False
        self._exhausted = asyncio.Event()

    @property
    def pid(self) -> int:
        return 4242

    def write_stdin(self, data: bytes) -> None:
        if self.stdin_closed:
            raise RuntimeError("stdin is not writable")
        self.stdin_frames.append(data)

    def close_stdin(self) -> None:
        self.stdin_closed = True

    async def stdout_lines(self) -> AsyncIterator[bytes]:
        for item in self.script:
            if self.terminated:
                break
            if isinstance(item, int | float):
                await asyncio.sleep(item)
                continue
            if callable(item):
                item = item(self.stdin_frames)
                if item is None:
                    continue
            if isinstance(item, dict):
                item = json.dumps(item)
            if isinstance(item, str):
                item = item.encode()
            yield item if item.endswith(b"\n") else item + b"\n"
        self._exhausted.set()

    def stderr_tail(self) -> str:
        return self._stderr

    async def wait(self) -> int:
        await self._exhausted.wait()
        return self.exit_code

    async def terminate(self, grace_seconds: float = 5.0) -> None:
        self.terminated = True
        self._exhausted.set()
        self.close_stdin()


class _TurnFactory:
    """A ProcessFactory that hands the driver one preset process and records specs."""

    def __init__(self, process: _FakeProcess) -> None:
        self.process = process
        self.spawned_specs: list[object] = []

    async def spawn(self, spec):
        self.spawned_specs.append(spec)
        return self.process


class _ProbeFactory:
    """A restricted-factory stand-in for ensure_ready: scripted probe + handshake."""

    def __init__(self, *, report: IsolationReport, handshake: _FakeProcess | None = None) -> None:
        self._report = report
        self._handshake = handshake if handshake is not None else _FakeProcess([_ok_initialize()])
        self.spawned_specs: list[object] = []
        self.probe_calls = 0

    async def probe(self) -> IsolationReport:
        self.probe_calls += 1
        return self._report

    async def spawn(self, spec):
        self.spawned_specs.append(spec)
        return self._handshake


class _FakeDriver:
    """A ProtocolDriver that emits preset events and returns a chosen result."""

    family = DriverFamily.APP_SERVER

    def __init__(self, result: DriverResult, *, events: tuple[DriverEvent, ...] = ()) -> None:
        self._result = result
        self._events = events
        self.requests: list[object] = []

    async def execute(self, request, profile, observer) -> DriverResult:
        self.requests.append(request)
        for event in self._events:
            observer(event)
        return self._result


# -- reports and frames ---------------------------------------------------


def _passing_report() -> IsolationReport:
    return IsolationReport(
        checks=(
            IsolationCheck(
                name="out_of_bounds_write_denied",
                verified=True,
                supported=True,
                required=True,
                detail="write outside the workspace was denied",
            ),
        ),
        platform="test",
    )


def _failing_report() -> IsolationReport:
    return IsolationReport(
        checks=(
            IsolationCheck(
                name="out_of_bounds_write_denied",
                verified=False,
                supported=True,
                required=True,
                detail="write escaped the workspace",
            ),
        ),
        platform="test",
    )


def _ok_initialize() -> dict:
    return {"id": 1, "result": {"userAgent": "codex/0.149.1"}}


def _ok_result() -> DriverResult:
    return DriverResult(status=DriverResultStatus.SUCCEEDED, summary="ok")


def _denied_request(tool_name: str = "commandExecution", **payload: object) -> DriverEvent:
    """One PERMISSION_REQUEST as the real drivers emit it under a deny-all policy."""

    return DriverEvent(
        kind=DriverEventKind.PERMISSION_REQUEST,
        payload={"tool_name": tool_name, "decision": "deny", **payload},
    )


def response(request_id: int, result: object) -> dict:
    return {"id": request_id, "result": result}


def notification(method: str, **params: object) -> dict:
    return {"method": method, "params": params}


def thread_payload() -> dict:
    return {"id": THREAD_ID, "path": TRANSCRIPT, "status": {"type": "idle"}}


def thread_started() -> dict:
    return notification("thread/started", thread=thread_payload())


def thread_start_result(model: str = "gpt-5.6-sol") -> dict:
    return {"thread": thread_payload(), "model": model, "cwd": "C:\\ws"}


def turn_ack(request_id: int = 3) -> dict:
    return response(request_id, {"turn": {"id": TURN_ID, "status": "inProgress", "error": None}})


def turn_terminal(method: str = "turn/completed", status: str = "completed", error=None) -> dict:
    return notification(
        method, threadId=THREAD_ID, turn={"id": TURN_ID, "status": status, "error": error}
    )


def agent_item(item_id: str, text: str, phase: str = "final_answer") -> dict:
    return {"type": "agentMessage", "id": item_id, "text": text, "phase": phase}


def command_item(status: str = "inProgress", **extra: object) -> dict:
    return {
        "type": "commandExecution",
        "id": "exec-1",
        "command": "powershell.exe -Command 'echo hi'",
        "cwd": "C:\\ws",
        "status": status,
        **extra,
    }


def item_started(item: dict) -> dict:
    return notification("item/started", item=item, threadId=THREAD_ID, turnId=TURN_ID)


def item_completed(item: dict) -> dict:
    return notification("item/completed", item=item, threadId=THREAD_ID, turnId=TURN_ID)


def approval_request(request_id: int = 90, method: str = "execCommandApproval") -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": {"threadId": THREAD_ID, "turnId": TURN_ID, "item": command_item()},
    }


def _success_script(agent_text: str = "The parser now handles quotes.") -> list:
    return [
        response(1, {"userAgent": "codex/0.149.1"}),
        PAUSE,
        thread_started(),
        response(2, thread_start_result()),
        PAUSE,
        item_completed(agent_item("msg-1", agent_text)),
        PAUSE,
        turn_ack(),
        PAUSE,
        turn_terminal(),
    ]


def sent_frames(process: _FakeProcess) -> list[dict]:
    return [json.loads(frame.decode()) for frame in process.stdin_frames]


def methods(frames: list[dict]) -> list[object]:
    return [frame.get("method") for frame in frames]


def _frame_for(frames: list[dict], method: str) -> dict:
    for frame in frames:
        if frame.get("method") == method:
            return frame
    raise AssertionError(f"no {method} frame in {methods(frames)}")


# -- session builders -----------------------------------------------------


def _write_auth(session_dir) -> None:
    codex_home = session_dir / "codex-home"
    codex_home.mkdir(parents=True, exist_ok=True)
    (codex_home / "auth.json").write_text("{}", encoding="utf-8")


def _two_binary_resolver(tmp_path, *, node: bool = True, codex: bool = True):
    """A resolver that answers both ``("codex",)`` and ``("node",)`` with absolute
    paths under ``tmp_path``, so the child ``PATH`` this build derives is
    deterministic. Either can be turned off to script a missing binary."""

    node_bin = str(tmp_path / "nodejs" / "node.exe")
    codex_bin = str(tmp_path / "npm" / "codex.cmd")

    def resolve(names):
        if names and names[0] == "node":
            return node_bin if node else None
        return codex_bin if codex else None

    return resolve


def _expected_path_dirs(tmp_path) -> tuple[str, str]:
    node_dir = str(Path(str(tmp_path / "nodejs" / "node.exe")).resolve().parent)
    codex_dir = str(Path(str(tmp_path / "npm" / "codex.cmd")).resolve().parent)
    return node_dir, codex_dir


def _make_session(tmp_path, driver, *, factory=None, with_auth: bool = True, resolve_binary=None):
    session_dir = tmp_path / "session"
    if with_auth:
        _write_auth(session_dir)
    factory = factory or _ProbeFactory(report=_passing_report())
    session = DriverCodingSession(
        driver,
        factory,
        session_dir=session_dir,
        worker_name=WORKER,
        resolve_binary=resolve_binary or _two_binary_resolver(tmp_path),
    )
    return session, factory


async def _ready_session(tmp_path, turn_process: _FakeProcess) -> DriverCodingSession:
    driver = AppServerDriver(_TurnFactory(turn_process))
    session, _ = _make_session(tmp_path, driver)
    await session.ensure_ready()
    return session


async def _wait_until(predicate, *, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not met in time")


# -- deny-all policy ------------------------------------------------------


def test_deny_all_policy_never_opens_the_gate() -> None:
    policy = _DenyAllPolicy()
    assert policy.decide("commandExecution", {"command": "rm -rf /"}) is PermissionDecision.DENY
    assert policy.decide("fileChange", {"path": "a.py"}) is PermissionDecision.DENY
    assert policy.decide("anything", {}) is PermissionDecision.DENY


def test_session_root_sits_beside_the_state_dir(tmp_path) -> None:
    worker = UUID("00000000-0000-0000-0000-000000000002")
    assert session_root(worker, tmp_path) == tmp_path / "sessions" / str(worker)


# -- ensure_ready: the three refusals (H-10) ------------------------------


async def test_ensure_ready_refuses_a_missing_binary(tmp_path) -> None:
    factory = _ProbeFactory(report=_passing_report())
    session = DriverCodingSession(
        _FakeDriver(_ok_result()),
        factory,
        session_dir=tmp_path / "session",
        worker_name=WORKER,
        binaries=("codex",),
        resolve_binary=lambda names: None,
    )

    with pytest.raises(SessionNotReady) as excinfo:
        await session.ensure_ready()

    assert "codex" in str(excinfo.value)
    assert factory.probe_calls == 0, "a missing binary is refused before the probe runs"


async def test_ensure_ready_refuses_when_isolation_cannot_be_proven(tmp_path) -> None:
    factory = _ProbeFactory(report=_failing_report())
    session = DriverCodingSession(
        _FakeDriver(_ok_result()),
        factory,
        session_dir=tmp_path / "session",
        worker_name=WORKER,
        resolve_binary=lambda names: "codex",
    )

    with pytest.raises(SessionNotReady) as excinfo:
        await session.ensure_ready()

    assert "write escaped the workspace" in str(excinfo.value)
    assert factory.spawned_specs == [], "a failed probe never reaches the handshake spawn"


async def test_ensure_ready_refuses_when_codex_is_not_logged_in(tmp_path) -> None:
    handshake = _FakeProcess([_ok_initialize()])
    factory = _ProbeFactory(report=_passing_report(), handshake=handshake)
    session = DriverCodingSession(
        _FakeDriver(_ok_result()),
        factory,
        session_dir=tmp_path / "session",
        worker_name=WORKER,
        resolve_binary=lambda names: "codex",
    )  # deliberately no auth.json

    with pytest.raises(SessionNotReady) as excinfo:
        await session.ensure_ready()

    message = str(excinfo.value)
    assert "codex login" in message
    assert "CODEX_HOME" in message
    assert handshake.terminated is True, "the gate reaps its own handshake even when it refuses"


async def test_ensure_ready_refuses_when_codex_will_not_start(tmp_path) -> None:
    # A handshake process that produces no line and exits, the way node/codex does
    # when the restricted environment is missing a variable it needs to start.
    handshake = _FakeProcess([], stderr="node: cannot find module\n")
    factory = _ProbeFactory(report=_passing_report(), handshake=handshake)
    session = DriverCodingSession(
        _FakeDriver(_ok_result()),
        factory,
        session_dir=tmp_path / "session",
        worker_name=WORKER,
        resolve_binary=lambda names: "codex",
    )

    with pytest.raises(SessionNotReady) as excinfo:
        await session.ensure_ready()

    message = str(excinfo.value)
    assert "initialize handshake" in message
    assert "cannot find module" in message, "the stderr tail is surfaced to the operator"
    assert handshake.terminated is True


async def test_ensure_ready_reaps_the_handshake_and_opens_the_session(tmp_path) -> None:
    handshake = _FakeProcess([_ok_initialize()])
    factory = _ProbeFactory(report=_passing_report(), handshake=handshake)
    session_dir = tmp_path / "session"
    _write_auth(session_dir)
    # Leave a stale file in the workspace to prove the reset clears it (H-7).
    workspace = session_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "stale.txt").write_text("left over", encoding="utf-8")
    session = DriverCodingSession(
        _FakeDriver(_ok_result()),
        factory,
        session_dir=session_dir,
        worker_name=WORKER,
        resolve_binary=lambda names: "codex",
    )

    await session.ensure_ready()

    assert factory.probe_calls == 1
    assert handshake.terminated is True
    assert workspace.is_dir()
    assert not list(workspace.iterdir()), "the workspace is reset on ensure_ready"
    assert (session_dir / "codex-home" / "auth.json").is_file(), "codex-home survives the reset"
    # close is idempotent and safe after a real open.
    await session.close()
    await session.close()


# -- respond: wire-level behaviour through the real driver ----------------


async def test_respond_resumes_the_thread_it_was_handed(tmp_path) -> None:
    process = _FakeProcess(_success_script("resumed and done"))
    session = await _ready_session(tmp_path, process)

    outcome = await session.respond(_turn(native=THREAD_ID))

    frames = sent_frames(process)
    assert _frame_for(frames, "thread/resume")["params"]["threadId"] == THREAD_ID
    assert all(frame.get("method") != "thread/start" for frame in frames)
    assert outcome.status == "completed"
    assert outcome.native_session_id == THREAD_ID
    assert [obs.body for obs in outcome.observations] == ["resumed and done"]


async def test_respond_cold_starts_without_a_handle(tmp_path) -> None:
    process = _FakeProcess(_success_script())
    session = await _ready_session(tmp_path, process)

    outcome = await session.respond(_turn(native=None))

    frames = sent_frames(process)
    assert _frame_for(frames, "thread/start")
    assert all(frame.get("method") != "thread/resume" for frame in frames)
    assert outcome.status == "completed"


async def test_respond_denies_every_tool_request(tmp_path) -> None:
    process = _FakeProcess(
        [
            response(1, {}),
            PAUSE,
            response(2, thread_start_result()),
            PAUSE,
            turn_ack(),
            PAUSE,
            approval_request(),
            PAUSE,
            item_completed(agent_item("msg-1", "finished")),
            turn_terminal(),
        ]
    )
    session = await _ready_session(tmp_path, process)

    outcome = await session.respond(_turn())

    answer = next(frame for frame in sent_frames(process) if frame.get("id") == 90)
    assert answer["result"] == {"decision": "denied"}
    assert outcome.status == "completed"
    assert outcome.denied_tool_requests == 1
    assert len(outcome.observations) == 1
    body = outcome.observations[0].body
    assert body.startswith("finished"), "the summary still leads the note"
    assert "1 tool action(s)" in body, "the room is told how many requests were refused"
    assert "start task" in body, "and where real changes can happen instead"
    assert "powershell.exe" not in body, "the denied command itself never reaches the room"


async def test_thinking_and_tool_frames_never_reach_an_observation(tmp_path) -> None:
    process = _FakeProcess(
        [
            response(1, {}),
            PAUSE,
            response(2, thread_start_result()),
            PAUSE,
            item_started(command_item()),
            item_completed(
                command_item(status="completed", exitCode=0, aggregatedOutput="secret-tool-output")
            ),
            item_started({"type": "reasoning", "id": "rs-1", "text": "private-chain-of-thought"}),
            item_completed(agent_item("msg-1", "PATCHED")),
            PAUSE,
            turn_ack(),
            PAUSE,
            turn_terminal(),
        ]
    )
    session = await _ready_session(tmp_path, process)

    outcome = await session.respond(_turn())

    assert outcome.status == "completed"
    assert len(outcome.observations) == 1
    bodies = " ".join(obs.body for obs in outcome.observations)
    assert bodies == "PATCHED"
    assert "private-chain-of-thought" not in bodies
    assert "secret-tool-output" not in bodies


async def test_respond_cancellation_cancels_the_child_and_reaps_the_process(tmp_path) -> None:
    process = _FakeProcess(
        [
            response(1, {}),
            PAUSE,
            response(2, thread_start_result()),
            PAUSE,
            turn_ack(),
            5.0,  # codex would keep working; we cancel the turn here
            turn_terminal(),
        ]
    )
    session = await _ready_session(tmp_path, process)

    task = asyncio.ensure_future(session.respond(_turn()))
    await _wait_until(lambda: any(f.get("method") == "turn/start" for f in sent_frames(process)))
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.terminated is True, "the driver's child was cancelled and its process reaped"


# -- respond: DriverResult -> TurnOutcome (H-11) --------------------------


async def test_succeeded_maps_to_a_completed_note(tmp_path) -> None:
    driver = _FakeDriver(
        DriverResult(
            status=DriverResultStatus.SUCCEEDED, summary="the answer", native_session_id="thread-1"
        )
    )
    session, _ = _make_session(tmp_path, driver)
    await session.ensure_ready()

    outcome = await session.respond(_turn())

    assert outcome.status == "completed"
    assert outcome.native_session_id == "thread-1"
    assert len(outcome.observations) == 1
    note = outcome.observations[0]
    assert note.body == "the answer"
    assert note.kind == "note"
    assert note.worker_name == WORKER
    assert note.room_id == ROOM


async def test_a_delivered_turn_discloses_the_requests_it_was_denied(tmp_path) -> None:
    driver = _FakeDriver(
        DriverResult(status=DriverResultStatus.SUCCEEDED, summary="the answer"),
        events=(_denied_request(), _denied_request("fileChange")),
    )
    session, _ = _make_session(tmp_path, driver)
    await session.ensure_ready()

    outcome = await session.respond(_turn())

    assert outcome.status == "completed"
    assert outcome.denied_tool_requests == 2
    assert len(outcome.observations) == 1
    body = outcome.observations[0].body
    assert body.startswith("the answer")
    assert body.endswith(_DENIAL_DISCLOSURE.format(n=2))


async def test_a_delivered_turn_that_asked_for_nothing_carries_no_disclosure(tmp_path) -> None:
    driver = _FakeDriver(DriverResult(status=DriverResultStatus.SUCCEEDED, summary="the answer"))
    session, _ = _make_session(tmp_path, driver)
    await session.ensure_ready()

    outcome = await session.respond(_turn())

    assert outcome.denied_tool_requests == 0
    assert outcome.observations[0].body == "the answer", "an untouched summary stays untouched"


async def test_only_the_count_of_a_permission_request_reaches_the_body(tmp_path) -> None:
    denied = _denied_request("secret-tool-name", command="del C:\\secrets", call_id="exec-7")
    driver = _FakeDriver(
        DriverResult(status=DriverResultStatus.SUCCEEDED, summary="done"), events=(denied,)
    )
    session, _ = _make_session(tmp_path, driver)
    await session.ensure_ready()

    outcome = await session.respond(_turn())

    body = outcome.observations[0].body
    assert "secret-tool-name" not in body
    assert "del C:\\secrets" not in body
    assert "exec-7" not in body
    assert "1 tool action(s)" in body, "the fact of the denial travels; the frame does not"


@pytest.mark.parametrize(
    "status",
    [DriverResultStatus.FAILED, DriverResultStatus.TIMEOUT, DriverResultStatus.INTERRUPTED],
)
async def test_non_success_terminals_become_one_canned_failed_note(tmp_path, status) -> None:
    driver = _FakeDriver(
        DriverResult(
            status=status,
            diagnostics="stack trace and a secret path",
            native_session_id="thread-x",
        )
    )
    session, _ = _make_session(tmp_path, driver)
    await session.ensure_ready()

    outcome = await session.respond(_turn())

    assert outcome.status == "failed"
    assert len(outcome.observations) == 1
    body = outcome.observations[0].body
    assert "stack trace" not in body
    assert "secret path" not in body
    assert "log" in body, "the room is pointed at the machine's log, nothing more"
    assert outcome.native_session_id == "thread-x", "the handle is passed through even on failure"


async def test_input_required_maps_to_a_blocked_note(tmp_path) -> None:
    driver = _FakeDriver(
        DriverResult(status=DriverResultStatus.INPUT_REQUIRED, diagnostics="needs a human")
    )
    session, _ = _make_session(tmp_path, driver)
    await session.ensure_ready()

    outcome = await session.respond(_turn())

    assert outcome.status == "blocked"
    assert len(outcome.observations) == 1
    assert "needs a human" not in outcome.observations[0].body


async def test_a_blocked_turn_reports_its_denials_without_disclosing_them(tmp_path) -> None:
    driver = _FakeDriver(
        DriverResult(status=DriverResultStatus.INPUT_REQUIRED, diagnostics="needs a human"),
        events=(_denied_request(), _denied_request()),
    )
    session, _ = _make_session(tmp_path, driver)
    await session.ensure_ready()

    outcome = await session.respond(_turn())

    assert outcome.status == "blocked"
    assert outcome.denied_tool_requests == 2, "the count is on the outcome for the supervisor"
    assert outcome.observations[0].body == _BLOCKED_NOTE, (
        "a turn that already says it did not deliver needs no second confession"
    )


async def test_succeeded_with_no_text_still_answers_the_room(tmp_path) -> None:
    driver = _FakeDriver(DriverResult(status=DriverResultStatus.SUCCEEDED, summary="   "))
    session, _ = _make_session(tmp_path, driver)
    await session.ensure_ready()

    outcome = await session.respond(_turn())

    assert outcome.status == "completed"
    assert outcome.observations[0].body.strip() != ""


async def test_an_empty_delivered_turn_still_discloses_its_denials(tmp_path) -> None:
    driver = _FakeDriver(
        DriverResult(status=DriverResultStatus.SUCCEEDED, summary="   "),
        events=(_denied_request(), _denied_request(), _denied_request()),
    )
    session, _ = _make_session(tmp_path, driver)
    await session.ensure_ready()

    outcome = await session.respond(_turn())

    assert outcome.denied_tool_requests == 3
    assert outcome.observations[0].body == (
        f"{_EMPTY_SUCCESS_NOTE}\n\n{_DENIAL_DISCLOSURE.format(n=3)}"
    )


async def test_native_session_id_falls_back_to_the_announced_thread(tmp_path) -> None:
    announced = DriverEvent(
        kind=DriverEventKind.SESSION_STARTED, payload={"native_session_id": "announced-thread"}
    )
    driver = _FakeDriver(
        DriverResult(status=DriverResultStatus.FAILED, diagnostics="died late"),
        events=(announced,),
    )
    session, _ = _make_session(tmp_path, driver)
    await session.ensure_ready()

    outcome = await session.respond(_turn())

    assert outcome.native_session_id == "announced-thread"


async def test_the_request_environment_is_a_fixed_allowlist(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SystemRoot", "C:\\Windows")
    monkeypatch.setenv("windir", "C:\\Windows")
    monkeypatch.setenv("SCM_TOKEN", "must-not-leak")  # a secret the child must never inherit
    monkeypatch.setenv("PATH", os.pathsep.join(["C:\\Windows\\System32", "C:\\operator\\bin"]))
    driver = _FakeDriver(DriverResult(status=DriverResultStatus.SUCCEEDED, summary="ok"))
    session, _ = _make_session(tmp_path, driver)
    await session.ensure_ready()

    await session.respond(_turn())

    environment = driver.requests[0].environment
    assert set(environment) == {"SystemRoot", "windir", "CODEX_HOME", "TMP", "TEMP", "PATH"}
    assert "SCM_TOKEN" not in environment, "os.environ is never merged in"
    assert environment["TMP"] == environment["TEMP"]
    assert environment["CODEX_HOME"].endswith("codex-home")
    # PATH is exactly the node and codex directories, in that order, and nothing
    # from the operator's own PATH.
    node_dir, codex_dir = _expected_path_dirs(tmp_path)
    assert environment["PATH"].split(os.pathsep) == [node_dir, codex_dir]
    assert "C:\\Windows\\System32" not in environment["PATH"]
    assert "C:\\operator\\bin" not in environment["PATH"]


async def test_ensure_ready_refuses_when_node_is_missing(tmp_path) -> None:
    factory = _ProbeFactory(report=_passing_report())
    session = DriverCodingSession(
        _FakeDriver(_ok_result()),
        factory,
        session_dir=tmp_path / "session",
        worker_name=WORKER,
        resolve_binary=_two_binary_resolver(tmp_path, node=False),
    )

    with pytest.raises(SessionNotReady) as excinfo:
        await session.ensure_ready()

    assert "node" in str(excinfo.value).lower()
    assert factory.probe_calls == 0, "a missing node is refused in segment 1, before the probe"


async def test_respond_before_ensure_ready_refuses_rather_than_crashing(tmp_path) -> None:
    driver = _FakeDriver(_ok_result())
    session, _ = _make_session(tmp_path, driver)

    with pytest.raises(SessionNotReady):
        await session.respond(_turn())


# -- real-codex smoke (runs when codex + node are installed; else skips) --


@pytest.mark.smoke_codex
async def test_real_codex_answers_an_initialize_handshake_under_restriction(tmp_path) -> None:
    """Restricted factory + real ``codex app-server`` for one initialize handshake.

    Spawns real codex under Low integrity with the exact production environment
    (the six-key allowlist including the node+codex ``PATH``), sends ``initialize``,
    asserts a JSON-RPC reply comes back, and confirms the process is reaped — then
    runs the shipped ``_handshake`` to prove that path too. Login is a per-turn
    concern and is not needed to answer ``initialize``. Skips, never fails, when
    this is not a Windows host, or when codex or Node.js is not installed, or when
    the isolation probe does not pass here; the authoritative live acceptance is
    the main session's job, so a host that cannot run it must not block delivery.
    """

    from repomesh_agent_bridge.adapters.coding_session import (
        executable_path,
        session_environment,
    )
    from repomesh_agent_bridge.adapters.restricted_process import (
        RestrictedProcessFactory,
        prepare_session_dirs,
    )
    from repomesh_runner.drivers.supervision import SpawnSpec, resolve_binary

    if os.name != "nt":
        pytest.skip("the restricted factory requires Windows mandatory integrity control")
    codex = resolve_binary(("codex",))
    if codex is None:
        pytest.skip("codex is not installed on this host")
    node = resolve_binary(("node",))
    if node is None:
        pytest.skip("Node.js is not installed on this host")

    from repomesh_agent_bridge.adapters.restricted_process import _process_alive

    factory = RestrictedProcessFactory()
    report = await factory.probe()
    if not report.required_ok:
        pytest.skip(f"isolation probe did not pass on this host:\n{report.summary()}")

    session_dir = tmp_path / "session"
    dirs = prepare_session_dirs(session_dir, reset_workspace=True)
    path_value = executable_path(node, codex)
    session = DriverCodingSession(
        AppServerDriver(factory),
        factory,
        session_dir=session_dir,
        worker_name="smoke",
        resolve_binary=resolve_binary,
    )
    environment = session_environment(dirs, path_value)  # the exact production env

    init = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"clientInfo": {"name": "smoke", "version": "0"}},
            }
        )
        + "\n"
    ).encode()
    spec = SpawnSpec(
        executable=codex,
        arguments=("app-server",),
        working_directory=dirs.workspace,
        environment=environment,
    )
    handle = await factory.spawn(spec)
    pid = handle.pid
    line: bytes | None = None
    try:
        handle.write_stdin(init)

        async def _first() -> bytes | None:
            async for raw in handle.stdout_lines():
                if raw.strip():
                    return raw.strip()
            return None

        line = await asyncio.wait_for(_first(), timeout=25)
    finally:
        await handle.terminate()

    assert line is not None, "codex did not answer initialize under the restricted environment"
    reply = json.loads(line.decode(errors="replace"))
    assert reply.get("id") == 1 and "result" in reply
    reaped = not _process_alive(pid)
    print(
        f"[smoke] restricted codex pid={pid} reaped={reaped} "
        f"initialize.result={json.dumps(reply.get('result'))[:200]}"
    )
    assert reaped, "the restricted codex process was not reaped after terminate"

    # The shipped gate's own handshake path must also succeed on this host.
    await session._handshake(codex, path_value, dirs)
