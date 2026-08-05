#!/usr/bin/env python3
"""An executable mock coding agent that speaks the stream-json dialect.

This is a *validation fixture*, not a vendor CLI and not an AI: it produces a
scripted NDJSON conversation so the Runner's ``stream_json`` driver, the
``repomesh-runner`` worker image, and the AgentTeams worker around it can be
exercised end to end without any vendor binary, any network access, and any
credentials.

The protocol implemented here is exactly the subset that
``src/repomesh_runner/drivers/stream_json.py`` consumes and produces:

* one ``{"type": "user", ...}`` frame arrives on stdin carrying the prompt, and
  stdin stays open for the rest of the turn;
* ``{"type": "system", "session_id": ..., "transcript_path": ...}`` announces the
  session (the driver treats this frame, and only this frame, as proof that a
  session was really opened);
* ``{"type": "assistant", "message": {"content": [...]}}`` carries ``text``,
  ``thinking`` and ``tool_use`` blocks;
* ``{"type": "user", "message": {"content": [{"type": "tool_result", ...}]}}``
  closes a tool call;
* ``{"type": "control_request", "request_id": ..., "request": {"subtype":
  "can_use_tool", ...}}`` asks for permission and is answered on stdin with a
  ``control_response`` frame whose ``response.response.behavior`` is ``allow`` or
  ``deny``;
* ``{"type": "result", "is_error": <bool>, ...}`` is the single terminal frame.
  A run that ends without one is a failure by the driver's fail-closed rule.

Scenario selection
------------------
``REPOMESH_MOCK_SCENARIO`` picks the behavior; it defaults to ``success``. The
seven names mirror ``repomesh.integrations.coding_agents.mock.MockScenario`` so
both sides of the system keep one vocabulary:

======================  =====================================================
scenario                what the mock does / driver status
======================  =====================================================
``success``             tool call, final text, ``is_error: false`` -> SUCCEEDED
``test_failed``         runs a test tool, terminal ``is_error: true`` with
                        ``subtype: test_failed`` -> FAILED
``failed``              terminal ``is_error: true`` with
                        ``subtype: error_during_execution`` plus stderr ->
                        FAILED
``timeout``             announces the session, then goes silent forever ->
                        TIMEOUT (the driver's idle watchdog fires)
``cancelled``           emits ``thinking`` heartbeats forever and never
                        terminates -> INTERRUPTED when the run is cancelled
``interrupted``         starts a tool call and never closes it -> INTERRUPTED
                        when the run is cancelled, with one tool call counted
``question_required``   emits a ``control_request`` -> INPUT_REQUIRED when the
                        permission policy escalates
======================  =====================================================

Honest limits of the mapping
----------------------------
* The stream-json dialect has **five** terminal driver statuses and no frame by
  which an agent can *declare* "interrupted", "cancelled", "timed out" or
  "input required". Those four outcomes are decided by the driver, so the mock
  can only create the process behavior that provokes them. It never prints a
  status string pretending otherwise.
* ``cancelled`` and ``interrupted`` therefore both end as ``INTERRUPTED``: the
  protocol has no way to distinguish who asked for the stop. They differ in the
  process behavior (heartbeats and no tool in flight, versus silence with one
  tool call in flight) so a harness can still tell them apart by the events.
* ``timeout`` and ``cancelled`` both stay alive indefinitely; only the silence
  differs. ``timeout`` is ended by the driver's watchdog, ``cancelled`` by
  whoever cancels the run — a live ``cancelled`` process would never time out.
* ``question_required`` cannot force ``INPUT_REQUIRED`` on its own: the driver
  returns that status only when the *platform's* permission policy escalates.
  If the policy answers instead, the mock behaves like a real agent would —
  ``allow`` continues to a successful result, ``deny`` ends with an error result
  — rather than faking an input-required status.
* ``test_failed`` and ``failed`` both map onto ``FAILED``; they are distinguished
  by the terminal frame's ``subtype``, which the driver puts into diagnostics.

Session identity and resume
---------------------------
The session id is deterministic: ``mock-<scenario>-<12 hex chars>`` derived from
the scenario and the optional ``REPOMESH_MOCK_SESSION_SEED``. Every run that
gets as far as announcing a session writes a small JSON record under
``REPOMESH_MOCK_STATE_DIR`` (default: a ``repomesh-mock-agent`` directory in the
system temp directory), so ``--resume <id>`` really reloads the prompt of the
earlier turn and repeats it back in the final result. An unknown id fails loudly
with an error result that echoes the requested id back, which is what the real
CLIs do and what the driver's unopened-resume rule expects.

Runtime: standard library only, no imports from ``repomesh`` or
``repomesh_runner`` — the container running this need not have either installed.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

SCENARIO_VARIABLE = "REPOMESH_MOCK_SCENARIO"
SEED_VARIABLE = "REPOMESH_MOCK_SESSION_SEED"
STATE_DIR_VARIABLE = "REPOMESH_MOCK_STATE_DIR"

DEFAULT_SCENARIO = "success"
SCENARIOS = (
    "success",
    "test_failed",
    "failed",
    "timeout",
    "cancelled",
    "interrupted",
    "question_required",
)

STATE_DIRNAME = "repomesh-mock-agent"
HEARTBEAT_SECONDS = 0.25
#: How long a "never ends on its own" scenario stays alive before giving up. It
#: exists only so a forgotten process cannot outlive the day; every legitimate
#: run is ended by the driver terminating the process well before this.
QUIET_SECONDS = 3600.0
REQUEST_ID = "mock-control-1"

_ARGUMENT_FLAGS = {
    "--resume": "resume",
    "--model": "model",
    "--append-system-prompt": "system_prompt",
}


# -- transport ----------------------------------------------------------------


def emit(frame: dict[str, Any]) -> None:
    """Write one NDJSON frame to stdout and flush it immediately."""

    sys.stdout.write(json.dumps(frame) + "\n")
    sys.stdout.flush()


def log(message: str) -> None:
    """Write a diagnostic to stderr; the driver keeps a bounded tail of it."""

    sys.stderr.write(f"mock-coding-agent: {message}\n")
    sys.stderr.flush()


def read_frame() -> dict[str, Any] | None:
    """Next JSON object on stdin, or None at end of input.

    Non-JSON and non-object lines are skipped: the mock is as tolerant of junk
    on its input as the driver is of junk on its output.
    """

    while True:
        line = sys.stdin.readline()
        if not line:
            return None
        text = line.strip()
        if not text:
            continue
        try:
            frame = json.loads(text)
        except json.JSONDecodeError:
            log(f"ignoring non-json stdin line: {text[:200]}")
            continue
        if isinstance(frame, dict):
            return frame
        log(f"ignoring unexpected stdin payload: {text[:200]}")


def read_prompt() -> str:
    """Block until the driver's user frame arrives; return its text."""

    while True:
        frame = read_frame()
        if frame is None:
            return ""
        if frame.get("type") != "user":
            continue
        content = frame.get("message", {}).get("content", [])
        texts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(part for part in texts if part).strip()


# -- frame builders -----------------------------------------------------------


def assistant(session_id: str, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "assistant",
        "session_id": session_id,
        "message": {"role": "assistant", "content": blocks},
    }


def tool_result(session_id: str, call_id: str, output: str) -> dict[str, Any]:
    return {
        "type": "user",
        "session_id": session_id,
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": call_id, "content": output}],
        },
    }


def terminal(session_id: str, *, is_error: bool, subtype: str, text: str) -> dict[str, Any]:
    return {
        "type": "result",
        "subtype": subtype,
        "is_error": is_error,
        "result": text,
        "session_id": session_id,
    }


# -- session state ------------------------------------------------------------


def state_dir() -> Path:
    raw = os.environ.get(STATE_DIR_VARIABLE, "").strip()
    root = Path(raw) if raw else Path(tempfile.gettempdir()) / STATE_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def session_path(session_id: str) -> Path:
    # The id is derived here or supplied by --resume; keep it to one path
    # segment so a hand-written id cannot select a file outside the store.
    return state_dir() / f"{Path(session_id).name}.json"


def derive_session_id(scenario: str) -> str:
    seed = os.environ.get(SEED_VARIABLE, "")
    digest = hashlib.sha256(f"{scenario}\x1f{seed}".encode()).hexdigest()[:12]
    return f"mock-{scenario}-{digest}"


def save_session(session_id: str, scenario: str, prompt: str) -> None:
    record = {"session_id": session_id, "scenario": scenario, "prompt": prompt}
    try:
        session_path(session_id).write_text(json.dumps(record), encoding="utf-8")
    except OSError as error:  # pragma: no cover - defensive, storage is optional
        log(f"could not persist session {session_id}: {error}")


def load_session(session_id: str) -> dict[str, Any] | None:
    try:
        record = json.loads(session_path(session_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return record if isinstance(record, dict) else None


# -- scenarios ----------------------------------------------------------------


def sleep_quietly() -> None:
    """Stay alive without emitting anything, until terminated."""

    deadline = time.monotonic() + QUIET_SECONDS
    while time.monotonic() < deadline:
        time.sleep(0.05)


def run_success(session_id: str, recalled: str | None) -> int:
    emit(
        assistant(
            session_id,
            [
                {"type": "text", "text": "Reading the workspace before touching anything."},
                {
                    "type": "tool_use",
                    "id": "mock-call-1",
                    "name": "Read",
                    "input": {"file_path": "README.md"},
                },
            ],
        )
    )
    emit(tool_result(session_id, "mock-call-1", "# README\n"))
    summary = "Mock run complete: scenario success."
    if recalled:
        summary += f" Recalled from the resumed session: {recalled}"
    emit(assistant(session_id, [{"type": "text", "text": summary}]))
    emit(terminal(session_id, is_error=False, subtype="success", text=summary))
    return 0


def run_test_failed(session_id: str) -> int:
    emit(
        assistant(
            session_id,
            [
                {"type": "text", "text": "Running the verification command."},
                {
                    "type": "tool_use",
                    "id": "mock-call-1",
                    "name": "Bash",
                    "input": {"command": "pytest -q"},
                },
            ],
        )
    )
    emit(tool_result(session_id, "mock-call-1", "1 failed, 12 passed"))
    log("verification command exited 1")
    emit(
        terminal(
            session_id,
            is_error=True,
            subtype="test_failed",
            text="pytest reported 1 failed test; the candidate change was not validated",
        )
    )
    return 1


def run_failed(session_id: str) -> int:
    emit(assistant(session_id, [{"type": "text", "text": "Starting the requested change."}]))
    log("fatal: simulated provider failure")
    emit(
        terminal(
            session_id,
            is_error=True,
            subtype="error_during_execution",
            text=f"the mock agent failed as instructed by {SCENARIO_VARIABLE}=failed",
        )
    )
    return 1


def run_timeout(session_id: str) -> int:
    emit(
        assistant(
            session_id,
            [{"type": "text", "text": "Starting a long operation; no further output follows."}],
        )
    )
    sleep_quietly()
    return 0


def run_cancelled(session_id: str) -> int:
    """Stay demonstrably alive: heartbeats mean the watchdog never fires."""

    emit(assistant(session_id, [{"type": "text", "text": "Working; this run never ends."}]))
    deadline = time.monotonic() + QUIET_SECONDS
    while time.monotonic() < deadline:
        emit(assistant(session_id, [{"type": "thinking", "thinking": "still working"}]))
        time.sleep(HEARTBEAT_SECONDS)
    return 0


def run_interrupted(session_id: str) -> int:
    """One tool call opened and never closed: a recoverable mid-tool stop."""

    emit(
        assistant(
            session_id,
            [
                {"type": "text", "text": "Starting a long build."},
                {
                    "type": "tool_use",
                    "id": "mock-call-1",
                    "name": "Bash",
                    "input": {"command": "sleep 600"},
                },
            ],
        )
    )
    sleep_quietly()
    return 0


def run_question_required(session_id: str) -> int:
    tool_input = {"url": "https://example.test/policy"}
    emit(
        assistant(
            session_id,
            [{"type": "text", "text": "I need a decision before fetching the policy document."}],
        )
    )
    emit(
        {
            "type": "control_request",
            "request_id": REQUEST_ID,
            "session_id": session_id,
            "request": {
                "subtype": "can_use_tool",
                "tool_name": "WebFetch",
                "input": tool_input,
            },
        }
    )

    behavior = read_permission_decision()
    if behavior is None:
        # The platform closed the channel without answering. In the escalating
        # case the driver has already stopped reading and is terminating this
        # process, so this frame usually never lands anywhere -- it exists so
        # that an unanswered question is never mistaken for a success.
        emit(
            terminal(
                session_id,
                is_error=True,
                subtype="permission_unanswered",
                text="the permission request for WebFetch was never answered",
            )
        )
        return 1
    if behavior == "allow":
        emit(tool_result(session_id, "mock-call-1", "policy document fetched"))
        summary = "Mock run complete: the platform approved WebFetch."
        emit(assistant(session_id, [{"type": "text", "text": summary}]))
        emit(terminal(session_id, is_error=False, subtype="success", text=summary))
        return 0
    emit(
        terminal(
            session_id,
            is_error=True,
            subtype="permission_denied",
            text=f"the platform denied WebFetch ({behavior}); the run cannot continue",
        )
    )
    return 1


def read_permission_decision() -> str | None:
    """Read stdin until the matching control_response arrives.

    Returns the ``behavior`` string, or None when the channel closed or the
    answer did not belong to this request.
    """

    while True:
        frame = read_frame()
        if frame is None:
            return None
        if frame.get("type") != "control_response":
            continue
        response = frame.get("response", {})
        if not isinstance(response, dict):
            return None
        if response.get("request_id") != REQUEST_ID:
            log(f"control_response for an unknown request: {response.get('request_id')!r}")
            return None
        inner = response.get("response", {})
        behavior = inner.get("behavior") if isinstance(inner, dict) else None
        return behavior if isinstance(behavior, str) else None


_SCENARIO_RUNNERS = {
    "test_failed": run_test_failed,
    "failed": run_failed,
    "timeout": run_timeout,
    "cancelled": run_cancelled,
    "interrupted": run_interrupted,
    "question_required": run_question_required,
}


# -- entry point --------------------------------------------------------------


def parse_arguments(argv: list[str]) -> dict[str, str | None]:
    """Pick the flags the mock honors; anything else is ignored, not rejected."""

    options: dict[str, str | None] = {"resume": None, "model": None, "system_prompt": None}
    index = 0
    while index < len(argv):
        key = _ARGUMENT_FLAGS.get(argv[index])
        if key is not None and index + 1 < len(argv):
            options[key] = argv[index + 1]
            index += 2
            continue
        index += 1
    return options


def configure_streams() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        with_reconfigure = getattr(stream, "reconfigure", None)
        if with_reconfigure is not None:
            with_reconfigure(encoding="utf-8", errors="replace")


def resolve_scenario() -> str:
    return (os.environ.get(SCENARIO_VARIABLE) or DEFAULT_SCENARIO).strip() or DEFAULT_SCENARIO


def main(argv: list[str]) -> int:
    configure_streams()
    scenario = resolve_scenario()
    options = parse_arguments(argv)

    if scenario not in SCENARIOS:
        log(f"unknown scenario {scenario!r}; expected one of {', '.join(SCENARIOS)}")
        emit(
            terminal(
                derive_session_id(DEFAULT_SCENARIO),
                is_error=True,
                subtype="unknown_scenario",
                text=f"{SCENARIO_VARIABLE}={scenario!r} is not a known mock scenario",
            )
        )
        return 2

    prompt = read_prompt()
    session_id = derive_session_id(scenario)
    recalled: str | None = None

    requested = options["resume"]
    if requested:
        record = load_session(requested)
        if record is None:
            # No system frame was emitted, so the driver knows this id was never
            # opened and will drop it instead of reporting a resumable session.
            log(f"no mock session found with id {requested}")
            emit(
                terminal(
                    requested,
                    is_error=True,
                    subtype="resume_failed",
                    text=f"no mock session found with id {requested}",
                )
            )
            return 1
        session_id = requested
        recalled = str(record.get("prompt") or "")

    emit(
        {
            "type": "system",
            "subtype": "init",
            "session_id": session_id,
            "transcript_path": str(session_path(session_id)),
            "model": options["model"] or "mock-model",
        }
    )
    save_session(session_id, scenario, prompt)

    runner = _SCENARIO_RUNNERS.get(scenario)
    if runner is not None:
        return runner(session_id)
    return run_success(session_id, recalled)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
