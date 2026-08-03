"""Contract tests for the ACP driver against the scripted fake process."""

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from repomesh_runner.drivers.acp import AcpDriver, detect_provider_error
from repomesh_runner.drivers.base import (
    DriverEvent,
    DriverEventKind,
    DriverFamily,
    DriverRequest,
    DriverResultStatus,
    PermissionDecision,
)
from repomesh_runner.profiles import AcpConfig, CliProfile

# Long enough for the driver to service a response between scripted lines, short
# enough to keep the suite fast.
PAUSE = 0.03


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


def acp_profile(quiescence: float = 0.01) -> CliProfile:
    return CliProfile(
        id="kimi-test",
        family=DriverFamily.ACP,
        binaries=("kimi",),
        observable=True,
        base_arguments=("acp",),
        acp=AcpConfig(protocol_version=1, quiescence_seconds=quiescence),
    )


def build_request(workspace: Path, policy: StubPolicy | None = None, **overrides: object):
    defaults: dict[str, object] = {
        "executable": "kimi",
        "workspace": workspace,
        "prompt": "refactor the parser",
        "permission_policy": policy or StubPolicy(),
        "idle_window_seconds": 5.0,
        "tool_window_seconds": 5.0,
    }
    defaults.update(overrides)
    return DriverRequest(**defaults)  # type: ignore[arg-type]


def response(request_id: int, result: object) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def error_response(request_id: int, message: str, code: int = -32602) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def session_update(update_kind: str, **fields: object) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {"sessionId": "sess-1", "update": {"sessionUpdate": update_kind, **fields}},
    }


def text_chunk(text: str) -> dict[str, object]:
    return session_update("agent_message_chunk", content={"type": "text", "text": text})


def permission_request(request_id: int = 90) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "session/request_permission",
        "params": {
            "sessionId": "sess-1",
            "toolCall": {
                "toolCallId": "call-1",
                "title": "Write",
                "kind": "edit",
                "rawInput": {"path": "a.py"},
            },
            "options": [
                {"optionId": "opt-allow-always", "name": "Always allow", "kind": "allow_always"},
                {"optionId": "opt-allow-once", "name": "Allow once", "kind": "allow_once"},
                {"optionId": "opt-reject-once", "name": "Reject", "kind": "reject_once"},
            ],
        },
    }


def sent_frames(factory) -> list[dict[str, object]]:
    return [json.loads(frame.decode()) for frame in factory.process.stdin_frames]


def frame_for(frames: list[dict[str, object]], method: str) -> dict[str, object]:
    for frame in frames:
        if frame.get("method") == method:
            return frame
    raise AssertionError(f"no {method} frame in {frames}")


async def test_successful_turn(fake_factory, tmp_path):
    factory = fake_factory(
        [
            response(1, {"protocolVersion": 1}),
            PAUSE,
            response(2, {"sessionId": "sess-abc"}),
            PAUSE,
            text_chunk("All done: "),
            text_chunk("parser refactored."),
            PAUSE,
            response(3, {"stopReason": "end_turn"}),
        ]
    )
    observer = Recorder()
    driver = AcpDriver(factory)

    result = await driver.execute(build_request(tmp_path), acp_profile(), observer)

    assert driver.family is DriverFamily.ACP
    assert result.status is DriverResultStatus.SUCCEEDED
    assert result.summary == "All done: parser refactored."
    assert result.native_session_id == "sess-abc"
    assert result.tool_call_count == 0

    spec = factory.spawned_specs[0]
    assert spec.argv == ("kimi", "acp")
    assert spec.working_directory == tmp_path

    frames = sent_frames(factory)
    assert [frame["method"] for frame in frames] == [
        "initialize",
        "session/new",
        "session/prompt",
    ]
    assert [frame["id"] for frame in frames] == [1, 2, 3]
    assert all(frame["jsonrpc"] == "2.0" for frame in frames)
    assert frames[0]["params"] == {
        "protocolVersion": 1,
        "clientInfo": {"name": "repomesh-runner", "version": "0.1.0"},
        "clientCapabilities": {},
    }
    assert frames[1]["params"] == {"cwd": str(tmp_path), "mcpServers": []}
    assert frames[2]["params"] == {
        "sessionId": "sess-abc",
        "prompt": [{"type": "text", "text": "refactor the parser"}],
    }

    assert observer.kinds() == [
        DriverEventKind.SESSION_STARTED,
        DriverEventKind.TEXT,
        DriverEventKind.TEXT,
    ]
    assert observer.of(DriverEventKind.SESSION_STARTED)[0]["native_session_id"] == "sess-abc"
    assert factory.process.stdin_closed


async def test_deliverable_resets_at_each_tool_call(fake_factory, tmp_path):
    factory = fake_factory(
        [
            response(1, {}),
            PAUSE,
            response(2, {"sessionId": "sess-abc"}),
            PAUSE,
            text_chunk("Let me look at the file."),
            session_update(
                "tool_call",
                toolCallId="call-1",
                title="Read",
                kind="read",
                rawInput={"path": "a.py"},
            ),
            session_update("tool_call_update", toolCallId="call-1", status="completed"),
            text_chunk("The parser now handles quotes."),
            PAUSE,
            response(3, {"stopReason": "end_turn"}),
        ]
    )
    observer = Recorder()

    result = await AcpDriver(factory).execute(build_request(tmp_path), acp_profile(), observer)

    assert result.status is DriverResultStatus.SUCCEEDED
    assert result.summary == "The parser now handles quotes."
    assert result.tool_call_count == 1
    tool_use = observer.of(DriverEventKind.TOOL_USE)[0]
    assert tool_use["call_id"] == "call-1"
    assert tool_use["tool_name"] == "Read"
    assert tool_use["input"] == {"path": "a.py"}
    assert observer.of(DriverEventKind.TOOL_RESULT)[0]["status"] == "completed"


async def test_thinking_and_unknown_updates_are_forwarded(fake_factory, tmp_path):
    factory = fake_factory(
        [
            response(1, {}),
            PAUSE,
            response(2, {"sessionId": "sess-abc"}),
            PAUSE,
            session_update("agent_thought_chunk", content={"type": "text", "text": "hmm"}),
            session_update("plan", entries=[]),
            text_chunk("ok"),
            PAUSE,
            response(3, {"stopReason": "end_turn"}),
        ]
    )
    observer = Recorder()

    result = await AcpDriver(factory).execute(build_request(tmp_path), acp_profile(), observer)

    assert result.status is DriverResultStatus.SUCCEEDED
    assert observer.of(DriverEventKind.THINKING)[0]["text"] == "hmm"
    assert observer.of(DriverEventKind.LOG)[0]["session_update"] == "plan"


async def test_permission_allow_selects_allow_once(fake_factory, tmp_path):
    factory = fake_factory(
        [
            response(1, {}),
            PAUSE,
            response(2, {"sessionId": "sess-abc"}),
            PAUSE,
            permission_request(),
            PAUSE,
            text_chunk("edited"),
            PAUSE,
            response(3, {"stopReason": "end_turn"}),
        ]
    )
    policy = StubPolicy(PermissionDecision.ALLOW)
    observer = Recorder()

    result = await AcpDriver(factory).execute(
        build_request(tmp_path, policy), acp_profile(), observer
    )

    assert result.status is DriverResultStatus.SUCCEEDED
    assert policy.calls == [("Write", {"path": "a.py"})]
    answer = next(frame for frame in sent_frames(factory) if frame.get("id") == 90)
    assert answer == {
        "jsonrpc": "2.0",
        "id": 90,
        "result": {"outcome": {"outcome": "selected", "optionId": "opt-allow-once"}},
    }
    payload = observer.of(DriverEventKind.PERMISSION_REQUEST)[0]
    assert payload["decision"] == "allow"
    assert payload["tool_name"] == "Write"


async def test_permission_deny_selects_reject_option(fake_factory, tmp_path):
    factory = fake_factory(
        [
            response(1, {}),
            PAUSE,
            response(2, {"sessionId": "sess-abc"}),
            PAUSE,
            permission_request(),
            PAUSE,
            response(3, {"stopReason": "end_turn"}),
        ]
    )
    observer = Recorder()

    result = await AcpDriver(factory).execute(
        build_request(tmp_path, StubPolicy(PermissionDecision.DENY)), acp_profile(), observer
    )

    assert result.status is DriverResultStatus.SUCCEEDED
    answer = next(frame for frame in sent_frames(factory) if frame.get("id") == 90)
    assert answer["result"] == {"outcome": {"outcome": "selected", "optionId": "opt-reject-once"}}
    assert observer.of(DriverEventKind.PERMISSION_REQUEST)[0]["decision"] == "deny"


async def test_permission_escalate_cancels_and_ends_input_required(fake_factory, tmp_path):
    factory = fake_factory(
        [
            response(1, {}),
            PAUSE,
            response(2, {"sessionId": "sess-abc"}),
            PAUSE,
            permission_request(),
            5.0,  # the agent would keep waiting; the driver must not
            response(3, {"stopReason": "end_turn"}),
        ]
    )
    observer = Recorder()

    result = await AcpDriver(factory).execute(
        build_request(tmp_path, StubPolicy(PermissionDecision.ESCALATE)), acp_profile(), observer
    )

    assert result.status is DriverResultStatus.INPUT_REQUIRED
    assert result.summary == ""
    assert "cannot be decided locally" in result.diagnostics
    answer = next(frame for frame in sent_frames(factory) if frame.get("id") == 90)
    assert answer["result"] == {"outcome": {"outcome": "cancelled"}}
    assert observer.of(DriverEventKind.PERMISSION_REQUEST)[0]["decision"] == "escalate"
    assert factory.process.terminated


async def test_set_model_error_fails_the_run(fake_factory, tmp_path):
    factory = fake_factory(
        [
            response(1, {}),
            PAUSE,
            response(2, {"sessionId": "sess-abc"}),
            PAUSE,
            error_response(3, "unknown model: k9"),
            PAUSE,
            response(4, {"stopReason": "end_turn"}),
        ]
    )

    result = await AcpDriver(factory).execute(
        build_request(tmp_path, model="k9"), acp_profile(), Recorder()
    )

    assert result.status is DriverResultStatus.FAILED
    assert result.summary == ""
    assert "model selection failed" in result.diagnostics
    assert "unknown model: k9" in result.diagnostics
    assert "default model" in result.diagnostics
    frames = sent_frames(factory)
    assert frame_for(frames, "session/set_model")["params"] == {
        "sessionId": "sess-abc",
        "modelId": "k9",
    }
    # The prompt must never be sent once the model is unknown.
    assert all(frame.get("method") != "session/prompt" for frame in frames)


async def test_non_end_turn_stop_reason_fails(fake_factory, tmp_path):
    factory = fake_factory(
        [
            response(1, {}),
            PAUSE,
            response(2, {"sessionId": "sess-abc"}),
            PAUSE,
            text_chunk("I will not do that."),
            PAUSE,
            response(3, {"stopReason": "refusal"}),
        ]
    )

    result = await AcpDriver(factory).execute(build_request(tmp_path), acp_profile(), Recorder())

    assert result.status is DriverResultStatus.FAILED
    assert result.summary == ""
    assert "refusal" in result.diagnostics


async def test_stream_end_without_prompt_response_fails(fake_factory, tmp_path):
    factory = fake_factory(
        [
            response(1, {}),
            PAUSE,
            response(2, {"sessionId": "sess-abc"}),
            PAUSE,
            text_chunk("looks fine to me"),
        ],
        exit_code=0,
        stderr="",
    )

    result = await AcpDriver(factory).execute(build_request(tmp_path), acp_profile(), Recorder())

    assert result.status is DriverResultStatus.FAILED
    assert result.summary == ""
    assert "without terminal result" in result.diagnostics
    assert "exit code 0" in result.diagnostics


async def test_provider_error_on_stderr_demotes_a_nominal_success(fake_factory, tmp_path):
    factory = fake_factory(
        [
            response(1, {}),
            PAUSE,
            response(2, {"sessionId": "sess-abc"}),
            PAUSE,
            text_chunk("Everything worked."),
            PAUSE,
            response(3, {"stopReason": "end_turn"}),
        ],
        stderr="API Error: 429 rate limit exceeded\n",
    )

    result = await AcpDriver(factory).execute(build_request(tmp_path), acp_profile(), Recorder())

    assert result.status is DriverResultStatus.FAILED
    assert result.summary == ""
    assert "429" in result.diagnostics


@pytest.mark.parametrize(
    "text",
    [
        "API Error: 429 rate limit exceeded",
        "API call failed after 3 attempts",
        "http request returned 503",
        "  RATE LIMIT reached  ",
    ],
)
def test_detect_provider_error_matches(text):
    assert detect_provider_error(text) is not None


@pytest.mark.parametrize(
    "text",
    ["", "wrote 404 lines of code", "all 200 tests passed", "refactored the parser"],
)
def test_detect_provider_error_ignores_benign_text(text):
    assert detect_provider_error(text) is None


async def test_resume_uses_session_resume_and_adopts_replacement_id(fake_factory, tmp_path):
    factory = fake_factory(
        [
            response(1, {}),
            PAUSE,
            response(2, {"sessionId": "sess-new"}),
            PAUSE,
            text_chunk("resumed and finished"),
            PAUSE,
            response(3, {"stopReason": "end_turn"}),
        ]
    )
    observer = Recorder()

    result = await AcpDriver(factory).execute(
        build_request(tmp_path, resume_session_id="sess-old"), acp_profile(), observer
    )

    assert result.status is DriverResultStatus.SUCCEEDED
    assert result.native_session_id == "sess-new"
    assert "resume replaced session id" in result.diagnostics
    frames = sent_frames(factory)
    assert frame_for(frames, "session/resume")["params"] == {
        "cwd": str(tmp_path),
        "sessionId": "sess-old",
        "mcpServers": [],
    }
    assert all(frame.get("method") != "session/new" for frame in frames)
    assert frame_for(frames, "session/prompt")["params"]["sessionId"] == "sess-new"


async def test_resume_keeps_the_requested_id_when_unchanged(fake_factory, tmp_path):
    factory = fake_factory(
        [
            response(1, {}),
            PAUSE,
            response(2, {"sessionId": "sess-old"}),
            PAUSE,
            text_chunk("done"),
            PAUSE,
            response(3, {"stopReason": "end_turn"}),
        ]
    )

    result = await AcpDriver(factory).execute(
        build_request(tmp_path, resume_session_id="sess-old"), acp_profile(), Recorder()
    )

    assert result.status is DriverResultStatus.SUCCEEDED
    assert result.native_session_id == "sess-old"
    assert "resume replaced" not in result.diagnostics


async def test_idle_timeout_terminates_the_process(fake_factory, tmp_path):
    factory = fake_factory([1.5, response(1, {})])

    result = await AcpDriver(factory).execute(
        build_request(tmp_path, idle_window_seconds=0.05, tool_window_seconds=0.05),
        acp_profile(),
        Recorder(),
    )

    assert result.status is DriverResultStatus.TIMEOUT
    assert result.summary == ""
    assert "idle timeout" in result.diagnostics
    assert factory.process.terminated
