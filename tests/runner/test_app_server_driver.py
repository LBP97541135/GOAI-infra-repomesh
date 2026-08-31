"""Contract tests for the app-server (codex) driver against the fake process.

Frame shapes are transcribed from a live codex-cli 0.145.0 session, including
the two traits that make this family different: responses carry no ``jsonrpc``
member, and the ``turn/start`` response only acknowledges the turn.
"""

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from repomesh_runner.drivers.app_server import AppServerDriver
from repomesh_runner.drivers.base import (
    DriverError,
    DriverEvent,
    DriverEventKind,
    DriverFamily,
    DriverRequest,
    DriverResultStatus,
    PermissionDecision,
)
from repomesh_runner.profiles import AppServerConfig, CliProfile

# Long enough for the driver to service a response between scripted lines, short
# enough to keep the suite fast.
PAUSE = 0.03

THREAD_ID = "019fc762-d677-7cd3-80e6-cd25db68a7a7"
TURN_ID = "019fc762-da53-7750-b01a-6862c23e6382"
TRANSCRIPT = (
    "C:\\Users\\dev\\.codex\\sessions\\2026\\08\\03\\"
    "rollout-2026-08-03T04-29-31-019fc762-d677-7cd3-80e6-cd25db68a7a7.jsonl"
)


class StubPolicy:
    def __init__(self, decision: PermissionDecision = PermissionDecision.ALLOW) -> None:
        self.decision = decision
        self.calls: list[tuple[str, Mapping[str, object]]] = []

    def decide(self, tool_name: str, tool_input: Mapping[str, object]) -> PermissionDecision:
        self.calls.append((tool_name, tool_input))
        return self.decision


class Recorder:
    def __init__(self) -> None:
        self.events: list[DriverEvent] = []

    def __call__(self, event: DriverEvent) -> None:
        self.events.append(event)

    def kinds(self) -> list[DriverEventKind]:
        return [event.kind for event in self.events]

    def of(self, kind: DriverEventKind) -> list[Mapping[str, object]]:
        return [event.payload for event in self.events if event.kind is kind]


def app_server_profile(quiescence: float = 0.01) -> CliProfile:
    return CliProfile(
        id="codex-test",
        family=DriverFamily.APP_SERVER,
        binaries=("codex",),
        observable=True,
        base_arguments=("app-server",),
        app_server=AppServerConfig(quiescence_seconds=quiescence),
    )


def build_request(workspace: Path, policy: StubPolicy | None = None, **overrides: object):
    defaults: dict[str, object] = {
        "executable": "codex",
        "workspace": workspace,
        "prompt": "refactor the parser",
        "permission_policy": policy or StubPolicy(),
        "idle_window_seconds": 5.0,
        "tool_window_seconds": 5.0,
    }
    defaults.update(overrides)
    return DriverRequest(**defaults)  # type: ignore[arg-type]


# -- wire frames, exactly as codex emits them -----------------------------


def response(request_id: int, result: object) -> dict[str, object]:
    """A codex response: no ``jsonrpc`` member, id-only routing."""

    return {"id": request_id, "result": result}


def error_response(request_id: int, message: str, code: int = -32600) -> dict[str, object]:
    # Observed key order: error first, id second, still no jsonrpc member.
    return {"error": {"code": code, "message": message}, "id": request_id}


def notification(method: str, **params: object) -> dict[str, object]:
    return {"method": method, "params": params, "emittedAtMs": 1785756586224}


def thread_payload() -> dict[str, object]:
    return {"id": THREAD_ID, "sessionId": THREAD_ID, "path": TRANSCRIPT, "status": {"type": "idle"}}


def thread_started() -> dict[str, object]:
    return notification("thread/started", thread=thread_payload())


def thread_start_result(model: str = "gpt-5.6-sol") -> dict[str, object]:
    return {"thread": thread_payload(), "model": model, "cwd": "C:\\ws"}


def turn_ack(request_id: int = 3) -> dict[str, object]:
    return response(request_id, {"turn": {"id": TURN_ID, "status": "inProgress", "error": None}})


def turn_terminal(
    method: str = "turn/completed",
    status: str = "completed",
    error: object = None,
) -> dict[str, object]:
    return notification(
        method,
        threadId=THREAD_ID,
        turn={"id": TURN_ID, "status": status, "error": error},
    )


def agent_item(item_id: str, text: str, phase: str = "final_answer") -> dict[str, object]:
    return {"type": "agentMessage", "id": item_id, "text": text, "phase": phase}


def command_item(status: str = "inProgress", **extra: object) -> dict[str, object]:
    return {
        "type": "commandExecution",
        "id": "exec-1",
        "command": "powershell.exe -Command 'echo hi'",
        "cwd": "C:\\ws",
        "status": status,
        **extra,
    }


def item_started(item: Mapping[str, object]) -> dict[str, object]:
    return notification("item/started", item=item, threadId=THREAD_ID, turnId=TURN_ID)


def item_completed(item: Mapping[str, object]) -> dict[str, object]:
    return notification("item/completed", item=item, threadId=THREAD_ID, turnId=TURN_ID)


def agent_delta(item_id: str, delta: str) -> dict[str, object]:
    return notification(
        "item/agentMessage/delta", itemId=item_id, delta=delta, threadId=THREAD_ID
    )


def approval_request(
    request_id: int = 90,
    method: str = "execCommandApproval",
) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": {
            "threadId": THREAD_ID,
            "turnId": TURN_ID,
            "item": command_item(),
        },
    }


def sent_frames(factory) -> list[dict[str, object]]:
    return [json.loads(frame.decode()) for frame in factory.process.stdin_frames]


def frame_for(frames: list[dict[str, object]], method: str) -> dict[str, object]:
    for frame in frames:
        if frame.get("method") == method:
            return frame
    raise AssertionError(f"no {method} frame in {frames}")


# -- tests ----------------------------------------------------------------


async def test_successful_turn(fake_factory, tmp_path):
    factory = fake_factory(
        [
            response(1, {"userAgent": "codex/0.145.0", "codexHome": "C:\\.codex"}),
            PAUSE,
            thread_started(),
            response(2, thread_start_result()),
            PAUSE,
            item_started(agent_item("msg-1", "")),
            agent_delta("msg-1", "The parser "),
            agent_delta("msg-1", "now handles quotes."),
            item_completed(agent_item("msg-1", "The parser now handles quotes.")),
            PAUSE,
            turn_ack(),
            PAUSE,
            turn_terminal(),
        ]
    )
    observer = Recorder()
    driver = AppServerDriver(factory)

    result = await driver.execute(build_request(tmp_path), app_server_profile(), observer)

    assert driver.family is DriverFamily.APP_SERVER
    assert result.status is DriverResultStatus.SUCCEEDED
    assert result.summary == "The parser now handles quotes."
    assert result.native_session_id == THREAD_ID
    assert result.transcript_path == TRANSCRIPT
    assert result.tool_call_count == 0

    spec = factory.spawned_specs[0]
    assert spec.argv == ("codex", "app-server")
    assert spec.working_directory == tmp_path

    frames = sent_frames(factory)
    assert [frame.get("method") for frame in frames] == [
        "initialize",
        "initialized",
        "thread/start",
        "turn/start",
    ]
    # The client speaks strict JSON-RPC even though the server's replies do not.
    assert all(frame["jsonrpc"] == "2.0" for frame in frames)
    assert frames[0]["id"] == 1
    assert frames[0]["params"] == {
        "clientInfo": {"name": "repomesh-runner", "version": "0.1.0"}
    }
    assert "id" not in frames[1] and frames[1]["params"] == {}
    assert frames[2]["id"] == 2
    assert frames[2]["params"] == {"cwd": str(tmp_path)}
    assert frames[3]["id"] == 3
    assert frames[3]["params"] == {
        "threadId": THREAD_ID,
        "input": [{"type": "text", "text": "refactor the parser"}],
    }

    session = observer.of(DriverEventKind.SESSION_STARTED)
    assert len(session) == 1
    assert session[0] == {"native_session_id": THREAD_ID, "transcript_path": TRANSCRIPT}
    assert [payload["text"] for payload in observer.of(DriverEventKind.TEXT)] == [
        "The parser ",
        "now handles quotes.",
    ]
    assert factory.process.stdin_closed


async def test_turn_start_response_is_not_terminal(fake_factory, tmp_path):
    """The regression that defines this family: inProgress is not success."""

    factory = fake_factory(
        [
            response(1, {}),
            PAUSE,
            response(2, thread_start_result()),
            PAUSE,
            item_completed(agent_item("msg-1", "All done, I promise.")),
            turn_ack(),
            # ... and then the stream simply ends: no turn/completed ever comes.
        ],
        exit_code=0,
    )

    result = await AppServerDriver(factory).execute(
        build_request(tmp_path), app_server_profile(), Recorder()
    )

    assert result.status is DriverResultStatus.FAILED
    assert result.summary == ""
    assert "stream ended without terminal result" in result.diagnostics
    assert "exit code 0" in result.diagnostics
    assert "turn/completed" in result.diagnostics


async def test_commentary_messages_are_not_the_deliverable(fake_factory, tmp_path):
    factory = fake_factory(
        [
            response(1, {}),
            PAUSE,
            response(2, thread_start_result()),
            PAUSE,
            item_completed(agent_item("msg-1", "Running the requested command now.", "commentary")),
            item_completed(agent_item("msg-2", "DONE")),
            # Trailing narration must not displace the answer: the rule selects
            # on phase, not on arrival order.
            item_completed(agent_item("msg-3", "Let me know if you need more.", "commentary")),
            PAUSE,
            turn_ack(),
            PAUSE,
            turn_terminal(),
        ]
    )

    result = await AppServerDriver(factory).execute(
        build_request(tmp_path), app_server_profile(), Recorder()
    )

    assert result.status is DriverResultStatus.SUCCEEDED
    assert result.summary == "DONE"


async def test_commentary_only_turn_falls_back_with_a_note(fake_factory, tmp_path):
    factory = fake_factory(
        [
            response(1, {}),
            PAUSE,
            response(2, thread_start_result()),
            PAUSE,
            item_completed(agent_item("msg-1", "Working on it.", "commentary")),
            PAUSE,
            turn_ack(),
            PAUSE,
            turn_terminal(),
        ]
    )

    result = await AppServerDriver(factory).execute(
        build_request(tmp_path), app_server_profile(), Recorder()
    )

    assert result.status is DriverResultStatus.SUCCEEDED
    assert result.summary == "Working on it."
    assert "fell back to the last commentary message" in result.diagnostics


async def test_command_execution_maps_to_tool_events(fake_factory, tmp_path):
    factory = fake_factory(
        [
            response(1, {}),
            PAUSE,
            response(2, thread_start_result()),
            PAUSE,
            item_started(command_item()),
            notification(
                "item/commandExecution/outputDelta",
                itemId="exec-1",
                delta="hi\r\n",
                threadId=THREAD_ID,
            ),
            item_completed(
                command_item(status="completed", exitCode=0, aggregatedOutput="hi\r\n")
            ),
            item_started({"type": "reasoning", "id": "rs-1", "text": "thinking about it"}),
            item_started({"type": "fileChange", "id": "fc-1", "changes": [{"path": "a.py"}]}),
            item_completed({"type": "fileChange", "id": "fc-1", "status": "completed"}),
            item_completed(agent_item("msg-1", "Patched a.py.")),
            PAUSE,
            turn_ack(),
            PAUSE,
            turn_terminal(),
        ]
    )
    observer = Recorder()

    result = await AppServerDriver(factory).execute(
        build_request(tmp_path), app_server_profile(), observer
    )

    assert result.status is DriverResultStatus.SUCCEEDED
    assert result.summary == "Patched a.py."
    assert result.tool_call_count == 2

    tool_use = observer.of(DriverEventKind.TOOL_USE)
    assert [payload["tool_name"] for payload in tool_use] == ["commandExecution", "fileChange"]
    assert tool_use[0]["call_id"] == "exec-1"
    assert tool_use[0]["input"] == {
        "command": "powershell.exe -Command 'echo hi'",
        "cwd": "C:\\ws",
    }
    tool_results = observer.of(DriverEventKind.TOOL_RESULT)
    # Streamed output first, then the two completions.
    assert tool_results[0] == {"call_id": "exec-1", "output": "hi\r\n", "streaming": True}
    assert tool_results[1]["status"] == "completed"
    assert tool_results[1]["exit_code"] == 0
    assert tool_results[1]["output"] == "hi\r\n"
    assert tool_results[2]["call_id"] == "fc-1"
    assert observer.of(DriverEventKind.THINKING)[0]["text"] == "thinking about it"


async def test_user_message_and_unknown_notifications_are_logged(fake_factory, tmp_path):
    factory = fake_factory(
        [
            response(1, {}),
            PAUSE,
            response(2, thread_start_result()),
            PAUSE,
            item_started(
                {
                    "type": "userMessage",
                    "id": "um-1",
                    "content": [{"type": "text", "text": "refactor the parser"}],
                }
            ),
            notification("thread/status/changed", status={"type": "active"}),
            notification("turn/diff/updated", threadId=THREAD_ID, diff="--- a\n+++ b\n"),
            item_completed(agent_item("msg-1", "done")),
            PAUSE,
            turn_ack(),
            PAUSE,
            turn_terminal(),
        ]
    )
    observer = Recorder()

    result = await AppServerDriver(factory).execute(
        build_request(tmp_path), app_server_profile(), observer
    )

    assert result.status is DriverResultStatus.SUCCEEDED
    # The cumulative diff is reported, never mistaken for the deliverable.
    assert result.summary == "done"
    logs = observer.of(DriverEventKind.LOG)
    assert any(payload.get("item_type") == "userMessage" for payload in logs)
    assert any(payload.get("method") == "thread/status/changed" for payload in logs)
    assert any(payload.get("turn_diff") == "--- a\n+++ b\n" for payload in logs)


@pytest.mark.parametrize(
    ("method", "status", "error", "expected"),
    [
        ("turn/failed", "failed", {"message": "model refused"}, DriverResultStatus.FAILED),
        ("turn/completed", "error", None, DriverResultStatus.FAILED),
        ("turn/completed", "completed", {"message": "tool crash"}, DriverResultStatus.FAILED),
        ("turn/aborted", "aborted", None, DriverResultStatus.INTERRUPTED),
    ],
)
async def test_non_success_terminals(fake_factory, tmp_path, method, status, error, expected):
    factory = fake_factory(
        [
            response(1, {}),
            PAUSE,
            response(2, thread_start_result()),
            PAUSE,
            item_completed(agent_item("msg-1", "I did everything.")),
            turn_ack(),
            PAUSE,
            turn_terminal(method, status, error),
        ]
    )

    result = await AppServerDriver(factory).execute(
        build_request(tmp_path), app_server_profile(), Recorder()
    )

    assert result.status is expected
    assert result.summary == ""
    assert method in result.diagnostics or "aborted" in result.diagnostics
    if error is not None:
        assert "model refused" in result.diagnostics or "tool crash" in result.diagnostics


async def test_rpc_error_on_thread_start_fails_before_any_turn(fake_factory, tmp_path):
    factory = fake_factory(
        [
            response(1, {}),
            PAUSE,
            error_response(2, "no rollout found for thread id"),
            PAUSE,
            turn_ack(),
        ],
        stderr="codex: thread start failed\n",
    )

    result = await AppServerDriver(factory).execute(
        build_request(tmp_path), app_server_profile(), Recorder()
    )

    assert result.status is DriverResultStatus.FAILED
    assert result.summary == ""
    assert "thread/start returned a JSON-RPC error" in result.diagnostics
    assert "no rollout found" in result.diagnostics
    assert "thread start failed" in result.diagnostics
    assert all(frame.get("method") != "turn/start" for frame in sent_frames(factory))
    assert factory.process.terminated


async def test_missing_thread_id_fails(fake_factory, tmp_path):
    factory = fake_factory(
        [
            response(1, {}),
            PAUSE,
            response(2, {"thread": {"path": TRANSCRIPT}}),
            PAUSE,
        ]
    )

    result = await AppServerDriver(factory).execute(
        build_request(tmp_path), app_server_profile(), Recorder()
    )

    assert result.status is DriverResultStatus.FAILED
    assert "thread/start returned no thread id" in result.diagnostics


async def test_substituted_model_fails_the_run(fake_factory, tmp_path):
    """codex accepts an unknown model silently; the thread's echo is the check."""

    factory = fake_factory(
        [
            response(1, {}),
            PAUSE,
            response(2, thread_start_result(model="gpt-5.6-sol")),
            PAUSE,
            turn_ack(),
        ]
    )

    result = await AppServerDriver(factory).execute(
        build_request(tmp_path, model="o4-mini"), app_server_profile(), Recorder()
    )

    assert result.status is DriverResultStatus.FAILED
    assert result.summary == ""
    assert "requested model 'o4-mini'" in result.diagnostics
    frames = sent_frames(factory)
    assert frame_for(frames, "thread/start")["params"]["model"] == "o4-mini"
    assert all(frame.get("method") != "turn/start" for frame in frames)


@pytest.mark.parametrize(
    ("decision", "verdict"),
    # The words are codex's, captured from a live 0.149.1 approval (C-8a); the
    # ones that used to be here were plausible and refused on the wire.
    [(PermissionDecision.ALLOW, "accept"), (PermissionDecision.DENY, "decline")],
)
async def test_approval_allow_and_deny_answer_on_the_request_id(
    fake_factory, tmp_path, decision, verdict
):
    factory = fake_factory(
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
    policy = StubPolicy(decision)
    observer = Recorder()

    result = await AppServerDriver(factory).execute(
        build_request(tmp_path, policy), app_server_profile(), observer
    )

    assert result.status is DriverResultStatus.SUCCEEDED
    assert [name for name, _ in policy.calls] == ["commandExecution"]
    answer = next(frame for frame in sent_frames(factory) if frame.get("id") == 90)
    assert answer == {"jsonrpc": "2.0", "id": 90, "result": {"decision": verdict}}
    payload = observer.of(DriverEventKind.PERMISSION_REQUEST)[0]
    assert payload["decision"] == decision.value
    assert payload["tool_name"] == "commandExecution"
    assert payload["call_id"] == "exec-1"


async def test_approval_escalate_ends_input_required(fake_factory, tmp_path):
    factory = fake_factory(
        [
            response(1, {}),
            PAUSE,
            response(2, thread_start_result()),
            PAUSE,
            turn_ack(),
            PAUSE,
            approval_request(),
            5.0,  # codex would keep waiting on the human; the driver must not
            turn_terminal(),
        ]
    )
    observer = Recorder()

    result = await AppServerDriver(factory).execute(
        build_request(tmp_path, StubPolicy(PermissionDecision.ESCALATE)),
        app_server_profile(),
        observer,
    )

    assert result.status is DriverResultStatus.INPUT_REQUIRED
    assert result.summary == ""
    assert "cannot be decided locally" in result.diagnostics
    answer = next(frame for frame in sent_frames(factory) if frame.get("id") == 90)
    assert answer == {"jsonrpc": "2.0", "id": 90, "result": {"decision": "cancel"}}
    assert observer.of(DriverEventKind.PERMISSION_REQUEST)[0]["decision"] == "escalate"
    assert factory.process.terminated


async def test_unknown_server_request_is_rejected(fake_factory, tmp_path):
    factory = fake_factory(
        [
            response(1, {}),
            PAUSE,
            response(2, thread_start_result()),
            PAUSE,
            turn_ack(),
            PAUSE,
            {"jsonrpc": "2.0", "id": 77, "method": "client/openFilePicker", "params": {}},
            PAUSE,
            item_completed(agent_item("msg-1", "ok")),
            turn_terminal(),
        ]
    )

    result = await AppServerDriver(factory).execute(
        build_request(tmp_path), app_server_profile(), Recorder()
    )

    assert result.status is DriverResultStatus.SUCCEEDED
    answer = next(frame for frame in sent_frames(factory) if frame.get("id") == 77)
    assert answer["error"]["code"] == -32601


async def test_responses_without_a_jsonrpc_member_are_routed(fake_factory, tmp_path):
    """Regression: codex omits ``jsonrpc`` on every reply it sends."""

    factory = fake_factory(
        [
            {"id": 1, "result": {"userAgent": "codex/0.145.0"}},
            PAUSE,
            {"id": 2, "result": thread_start_result()},
            PAUSE,
            item_completed(agent_item("msg-1", "routed")),
            {"id": 3, "result": {"turn": {"id": TURN_ID, "status": "inProgress"}}},
            PAUSE,
            turn_terminal(),
        ]
    )

    result = await AppServerDriver(factory).execute(
        build_request(tmp_path), app_server_profile(), Recorder()
    )

    replies = [
        frame
        for frame in factory.process.script
        if isinstance(frame, dict) and ("result" in frame or "error" in frame)
    ]
    assert replies and all("jsonrpc" not in frame for frame in replies)
    assert result.status is DriverResultStatus.SUCCEEDED
    assert result.summary == "routed"


async def test_unparsable_and_unroutable_lines_do_not_derail_the_run(fake_factory, tmp_path):
    factory = fake_factory(
        [
            "not json at all",
            response(1, {}),
            PAUSE,
            response(2, thread_start_result()),
            PAUSE,
            {"result": {"stray": True}},
            response(999, {"late": True}),
            item_completed(agent_item("msg-1", "survived")),
            PAUSE,
            turn_ack(),
            PAUSE,
            turn_terminal(),
        ]
    )
    observer = Recorder()

    result = await AppServerDriver(factory).execute(
        build_request(tmp_path), app_server_profile(), observer
    )

    assert result.status is DriverResultStatus.SUCCEEDED
    assert result.summary == "survived"
    logs = observer.of(DriverEventKind.LOG)
    assert any(payload.get("unparsed_stdout") == "not json at all" for payload in logs)
    assert any("unroutable_message" in payload for payload in logs)


async def test_idle_timeout_terminates_the_process(fake_factory, tmp_path):
    factory = fake_factory(
        [
            response(1, {}),
            PAUSE,
            response(2, thread_start_result()),
            PAUSE,
            turn_ack(),
            1.5,  # no turn/completed within the idle window
            turn_terminal(),
        ]
    )

    result = await AppServerDriver(factory).execute(
        build_request(tmp_path, idle_window_seconds=0.05, tool_window_seconds=0.05),
        app_server_profile(),
        Recorder(),
    )

    assert result.status is DriverResultStatus.TIMEOUT
    assert result.summary == ""
    assert "idle timeout" in result.diagnostics
    assert "turn/completed" in result.diagnostics
    assert factory.process.terminated


async def test_resume_uses_thread_resume(fake_factory, tmp_path):
    factory = fake_factory(
        [
            response(1, {}),
            PAUSE,
            response(2, thread_start_result()),
            PAUSE,
            item_completed(agent_item("msg-1", "resumed and finished")),
            turn_ack(),
            PAUSE,
            turn_terminal(),
        ]
    )

    result = await AppServerDriver(factory).execute(
        build_request(tmp_path, resume_session_id=THREAD_ID), app_server_profile(), Recorder()
    )

    assert result.status is DriverResultStatus.SUCCEEDED
    frames = sent_frames(factory)
    assert frame_for(frames, "thread/resume")["params"] == {
        "cwd": str(tmp_path),
        "threadId": THREAD_ID,
    }
    assert all(frame.get("method") != "thread/start" for frame in frames)


async def test_profile_without_app_server_config_is_rejected(fake_factory, tmp_path):
    # CliProfile refuses to build this combination, so the guard is reached only
    # via a hand-made profile; the driver must still refuse to spawn anything.
    profile = CliProfile(
        id="codex-test",
        family=DriverFamily.APP_SERVER,
        binaries=("codex",),
        app_server=AppServerConfig(),
    )
    object.__setattr__(profile, "app_server", None)
    factory = fake_factory([])

    with pytest.raises(DriverError, match="no app_server config"):
        await AppServerDriver(factory).execute(build_request(tmp_path), profile, Recorder())
    assert factory.spawned_specs == []
