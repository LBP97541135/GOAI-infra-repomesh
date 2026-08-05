"""Contract tests for the stream-json driver against a scripted fake process."""

import asyncio
import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from repomesh_runner.contracts import RunnerPermissionMode
from repomesh_runner.drivers.base import (
    DriverEvent,
    DriverEventKind,
    DriverRequest,
    DriverResultStatus,
    PermissionDecision,
)
from repomesh_runner.drivers.stream_json import DENY_MESSAGE, StreamJsonDriver
from repomesh_runner.profiles import StreamJsonConfig, get_profile

PROFILE = get_profile("claude-code")


class StubPolicy:
    """Minimal PermissionPolicy returning one canned decision."""

    def __init__(self, decision: PermissionDecision = PermissionDecision.ALLOW) -> None:
        self.decision = decision
        self.calls: list[tuple[str, dict[str, object]]] = []

    def decide(self, tool_name: str, tool_input: Mapping[str, object]) -> PermissionDecision:
        self.calls.append((tool_name, dict(tool_input)))
        return self.decision


class Recorder:
    def __init__(self) -> None:
        self.events: list[DriverEvent] = []

    def __call__(self, event: DriverEvent) -> None:
        self.events.append(event)

    def kinds(self) -> list[DriverEventKind]:
        return [event.kind for event in self.events]

    def payloads(self, kind: DriverEventKind) -> list[Mapping[str, object]]:
        return [event.payload for event in self.events if event.kind is kind]


def make_request(workspace: Path, **overrides: object) -> DriverRequest:
    fields: dict[str, object] = {
        "executable": "/opt/bin/claude",
        "workspace": workspace,
        "prompt": "fix the flaky test",
        "permission_policy": StubPolicy(),
        "idle_window_seconds": 5.0,
        "tool_window_seconds": 5.0,
    }
    fields.update(overrides)
    return DriverRequest(**fields)  # type: ignore[arg-type]


def assistant_text(text: str) -> dict[str, object]:
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


def result_frame(**overrides: object) -> dict[str, object]:
    frame: dict[str, object] = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "",
        "session_id": "sess-final",
    }
    frame.update(overrides)
    return frame


def control_request(tool_name: str, tool_input: dict[str, object]) -> dict[str, object]:
    return {
        "type": "control_request",
        "request_id": "req-7",
        "request": {"subtype": "can_use_tool", "tool_name": tool_name, "input": tool_input},
    }


async def test_argv_shape_and_prompt_frame(tmp_path, fake_factory):
    observed: dict[str, object] = {}
    factory = fake_factory(
        [
            0.01,
            lambda frames: observed.update(
                stdin_closed=factory.process.stdin_closed,
                frames=list(frames),
            ),
            result_frame(result="done"),
        ]
    )
    request = make_request(
        tmp_path,
        model="claude-opus-4",
        system_prompt="stay in the worktree",
        resume_session_id="prev-session",
    )

    result = await StreamJsonDriver(factory).execute(request, PROFILE, Recorder())

    assert result.status is DriverResultStatus.SUCCEEDED
    spec = factory.spawned_specs[0]
    assert spec.executable == "/opt/bin/claude"
    assert spec.arguments == (
        *PROFILE.base_arguments,
        "--model",
        "claude-opus-4",
        "--append-system-prompt",
        "stay in the worktree",
        "--resume",
        "prev-session",
    )
    assert request.prompt not in spec.arguments
    assert "--permission-mode" not in spec.arguments
    assert spec.working_directory == tmp_path

    frames = observed["frames"]
    assert isinstance(frames, list)
    assert len(frames) == 1
    assert json.loads(frames[0]) == {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": "fix the flaky test"}],
        },
    }
    # stdin must still be open while the turn is running: control_response
    # frames travel back up the same pipe.
    assert observed["stdin_closed"] is False


async def test_prompt_goes_to_argv_when_stdin_is_disabled(tmp_path, fake_factory):
    profile = replace(PROFILE, stream_json=StreamJsonConfig(prompt_via_stdin=False))
    factory = fake_factory([result_frame(result="done")])

    result = await StreamJsonDriver(factory).execute(
        make_request(tmp_path), profile, Recorder()
    )

    assert result.status is DriverResultStatus.SUCCEEDED
    assert factory.spawned_specs[0].arguments[-1] == "fix the flaky test"
    assert factory.process.stdin_frames == []


async def test_successful_run_reports_summary_and_session(tmp_path, fake_factory):
    factory = fake_factory(
        [
            {
                "type": "system",
                "subtype": "init",
                "session_id": "sess-abc",
                "transcript_path": "/logs/sess-abc.jsonl",
            },
            assistant_text("Patched the retry loop."),
            result_frame(result="Patched the retry loop.", session_id="sess-abc"),
        ]
    )
    recorder = Recorder()

    result = await StreamJsonDriver(factory).execute(
        make_request(tmp_path), PROFILE, recorder
    )

    assert result.status is DriverResultStatus.SUCCEEDED
    assert result.summary == "Patched the retry loop."
    assert result.native_session_id == "sess-abc"
    assert result.transcript_path == "/logs/sess-abc.jsonl"
    assert result.diagnostics == ""
    assert result.tool_call_count == 0
    assert DriverEventKind.SESSION_STARTED in recorder.kinds()
    assert DriverEventKind.TEXT in recorder.kinds()
    assert recorder.payloads(DriverEventKind.SESSION_STARTED)[0]["native_session_id"] == "sess-abc"


async def test_clean_exit_without_result_is_failed(tmp_path, fake_factory):
    factory = fake_factory([assistant_text("All done, everything works!")], exit_code=0)

    result = await StreamJsonDriver(factory).execute(
        make_request(tmp_path), PROFILE, Recorder()
    )

    assert result.status is DriverResultStatus.FAILED
    assert result.summary == ""
    assert "without terminal result" in result.diagnostics
    assert "exit code 0" in result.diagnostics


async def test_tool_turn_text_is_narration_not_deliverable(tmp_path, fake_factory):
    factory = fake_factory(
        [
            {"type": "system", "session_id": "sess-tool"},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "the test is order dependent"},
                        {"type": "text", "text": "Let me look at the test file."},
                        {
                            "type": "tool_use",
                            "id": "call-1",
                            "name": "Read",
                            "input": {"file_path": "/repo/test_x.py"},
                        },
                    ],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "call-1", "content": "ok"}
                    ],
                },
            },
            assistant_text("Removed the shared fixture."),
            result_frame(result=""),
        ]
    )
    recorder = Recorder()

    result = await StreamJsonDriver(factory).execute(
        make_request(tmp_path), PROFILE, recorder
    )

    assert result.status is DriverResultStatus.SUCCEEDED
    # result carried no text -> fall back to the last tool-free assistant turn.
    assert result.summary == "Removed the shared fixture."
    assert "Let me look at the test file." not in result.summary
    assert result.tool_call_count == 1
    tool_use = recorder.payloads(DriverEventKind.TOOL_USE)[0]
    assert tool_use == {
        "call_id": "call-1",
        "tool_name": "Read",
        "input": {"file_path": "/repo/test_x.py"},
    }
    assert recorder.payloads(DriverEventKind.TOOL_RESULT)[0]["call_id"] == "call-1"
    assert DriverEventKind.THINKING in recorder.kinds()


async def test_permission_allow_writes_control_response(tmp_path, fake_factory):
    policy = StubPolicy(PermissionDecision.ALLOW)
    factory = fake_factory(
        [
            control_request("Bash", {"command": "ls -la"}),
            result_frame(result="listed"),
        ]
    )
    recorder = Recorder()

    result = await StreamJsonDriver(factory).execute(
        make_request(tmp_path, permission_policy=policy), PROFILE, recorder
    )

    assert result.status is DriverResultStatus.SUCCEEDED
    assert policy.calls == [("Bash", {"command": "ls -la"})]
    assert json.loads(factory.process.stdin_frames[1]) == {
        "type": "control_response",
        "response": {
            "subtype": "success",
            "request_id": "req-7",
            "response": {"behavior": "allow", "updatedInput": {"command": "ls -la"}},
        },
    }
    assert recorder.payloads(DriverEventKind.PERMISSION_REQUEST) == [
        {"tool_name": "Bash", "decision": "allow"}
    ]


async def test_bypass_argv_still_answers_control_requests(tmp_path, fake_factory):
    """The bypass mapping must leave the CLI asking, or the deny rules go dark."""

    policy = StubPolicy(PermissionDecision.ALLOW)
    bypass_arguments = PROFILE.permission_arguments[RunnerPermissionMode.BYPASS_PERMISSIONS]
    factory = fake_factory(
        [
            control_request("Bash", {"command": "ls -la"}),
            result_frame(result="listed"),
        ]
    )

    result = await StreamJsonDriver(factory).execute(
        make_request(tmp_path, permission_policy=policy, extra_arguments=bypass_arguments),
        PROFILE,
        Recorder(),
    )

    assert result.status is DriverResultStatus.SUCCEEDED
    assert "bypassPermissions" not in factory.spawned_specs[0].arguments
    assert bypass_arguments == ("--permission-mode", "default")
    assert factory.spawned_specs[0].arguments == (*PROFILE.base_arguments, *bypass_arguments)
    # The callback channel is alive: the tool call was decided by the policy.
    assert policy.calls == [("Bash", {"command": "ls -la"})]
    assert json.loads(factory.process.stdin_frames[1])["response"]["response"]["behavior"] == (
        "allow"
    )


async def test_permission_deny_writes_control_response(tmp_path, fake_factory):
    policy = StubPolicy(PermissionDecision.DENY)
    factory = fake_factory(
        [
            control_request("Bash", {"command": "rm -rf /"}),
            result_frame(result="refused to run that"),
        ]
    )
    recorder = Recorder()

    result = await StreamJsonDriver(factory).execute(
        make_request(tmp_path, permission_policy=policy), PROFILE, recorder
    )

    assert result.status is DriverResultStatus.SUCCEEDED
    assert json.loads(factory.process.stdin_frames[1])["response"]["response"] == {
        "behavior": "deny",
        "message": DENY_MESSAGE,
    }
    assert recorder.payloads(DriverEventKind.PERMISSION_REQUEST) == [
        {"tool_name": "Bash", "decision": "deny"}
    ]


async def test_permission_escalate_ends_input_required(tmp_path, fake_factory):
    policy = StubPolicy(PermissionDecision.ESCALATE)
    factory = fake_factory(
        [
            {"type": "system", "session_id": "sess-esc"},
            control_request("WebFetch", {"url": "https://example.test"}),
            result_frame(result="should never be reached"),
        ]
    )
    recorder = Recorder()

    result = await StreamJsonDriver(factory).execute(
        make_request(tmp_path, permission_policy=policy), PROFILE, recorder
    )

    assert result.status is DriverResultStatus.INPUT_REQUIRED
    assert result.summary == ""
    assert "WebFetch" in result.diagnostics
    assert result.native_session_id == "sess-esc"
    assert factory.process.terminated is True
    # no control_response was written; only the prompt frame went out
    assert len(factory.process.stdin_frames) == 1
    assert recorder.payloads(DriverEventKind.PERMISSION_REQUEST) == [
        {"tool_name": "WebFetch", "decision": "escalate"}
    ]


async def test_error_result_is_failed_with_diagnostics(tmp_path, fake_factory):
    factory = fake_factory(
        [
            assistant_text("I started the migration."),
            result_frame(
                subtype="error_during_execution",
                is_error=True,
                result="API call failed: 529 overloaded",
            ),
        ],
        exit_code=1,
        stderr="fatal: upstream rejected",
    )

    result = await StreamJsonDriver(factory).execute(
        make_request(tmp_path), PROFILE, Recorder()
    )

    assert result.status is DriverResultStatus.FAILED
    assert result.summary == ""
    assert "API call failed: 529 overloaded" in result.diagnostics
    assert "fatal: upstream rejected" in result.diagnostics


async def test_invalid_session_ids_are_dropped(tmp_path, fake_factory):
    factory = fake_factory(
        [
            {"type": "system", "session_id": "-x-injected-flag"},
            assistant_text("ok"),
            result_frame(result="ok", session_id="\x00broken"),
        ]
    )
    recorder = Recorder()

    result = await StreamJsonDriver(factory).execute(
        make_request(tmp_path), PROFILE, recorder
    )

    assert result.status is DriverResultStatus.SUCCEEDED
    assert result.native_session_id is None
    assert DriverEventKind.SESSION_STARTED not in recorder.kinds()


async def test_failed_resume_does_not_report_the_unopened_session(tmp_path, fake_factory):
    """An unknown --resume id is echoed back on the error frame; drop it.

    Reporting it would tell the platform the session is alive and retryable,
    when in fact no session was ever opened.
    """

    factory = fake_factory(
        [
            result_frame(
                subtype="error_during_execution",
                is_error=True,
                result="provider reported an error",
                session_id="sess-ghost",
            )
        ],
        exit_code=1,
        stderr="No conversation found with session ID: sess-ghost",
    )

    result = await StreamJsonDriver(factory).execute(
        make_request(tmp_path, resume_session_id="sess-ghost"), PROFILE, Recorder()
    )

    assert result.status is DriverResultStatus.FAILED
    assert result.native_session_id is None
    assert "No conversation found" in result.diagnostics


async def test_failure_after_init_still_reports_the_live_session(tmp_path, fake_factory):
    """The crash-resume case: init confirmed the session, so it is retryable."""

    factory = fake_factory(
        [
            {"type": "system", "subtype": "init", "session_id": "sess-live"},
            assistant_text("working on it"),
            result_frame(
                subtype="error_during_execution",
                is_error=True,
                result="API call failed: 529 overloaded",
                session_id="sess-live",
            ),
        ],
        exit_code=1,
    )
    recorder = Recorder()

    result = await StreamJsonDriver(factory).execute(
        make_request(tmp_path, resume_session_id="sess-live"), PROFILE, recorder
    )

    assert result.status is DriverResultStatus.FAILED
    assert result.native_session_id == "sess-live"
    assert DriverEventKind.SESSION_STARTED in recorder.kinds()


async def test_failed_run_reports_a_session_id_it_did_not_ask_to_resume(tmp_path, fake_factory):
    """Only the echo of the requested id is suppressed, not every failure id."""

    factory = fake_factory(
        [result_frame(subtype="error_during_execution", is_error=True, session_id="sess-fresh")],
        exit_code=1,
    )

    result = await StreamJsonDriver(factory).execute(
        make_request(tmp_path, resume_session_id="sess-ghost"), PROFILE, Recorder()
    )

    assert result.status is DriverResultStatus.FAILED
    assert result.native_session_id == "sess-fresh"


async def test_non_json_lines_are_logged_and_ignored(tmp_path, fake_factory):
    factory = fake_factory(
        [
            "Welcome to Claude Code!",
            "[]",
            result_frame(result="fine"),
        ]
    )
    recorder = Recorder()

    result = await StreamJsonDriver(factory).execute(
        make_request(tmp_path), PROFILE, recorder
    )

    assert result.status is DriverResultStatus.SUCCEEDED
    assert result.summary == "fine"
    assert len(recorder.payloads(DriverEventKind.LOG)) == 2


async def test_cancellation_reports_interrupted(tmp_path, fake_factory):
    factory = fake_factory([5.0, result_frame(result="never")])
    driver = StreamJsonDriver(factory)
    task = asyncio.ensure_future(
        driver.execute(make_request(tmp_path), PROFILE, Recorder())
    )
    await asyncio.sleep(0.05)
    task.cancel()

    result = await task

    assert result.status is DriverResultStatus.INTERRUPTED
    assert result.summary == ""
    assert "cancelled" in result.diagnostics
    assert factory.process.terminated is True


async def test_idle_timeout_terminates_the_process(tmp_path, fake_factory):
    factory = fake_factory([0.5, result_frame(result="too late")])
    request = make_request(tmp_path, idle_window_seconds=0.05, tool_window_seconds=0.05)

    result = await StreamJsonDriver(factory).execute(request, PROFILE, Recorder())

    assert result.status is DriverResultStatus.TIMEOUT
    assert result.summary == ""
    assert "idle watchdog fired" in result.diagnostics
    assert factory.process.terminated is True
