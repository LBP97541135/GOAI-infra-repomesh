"""The executable mock coding agent, driven by the real stream-json driver.

Everything here runs a genuine subprocess through :class:`SubprocessFactory`:
the point is to prove that ``components/repomesh-runner/mock/mock_coding_agent.py``
is protocol-correct against the driver, not merely self-consistent. No vendor
CLI, no network and no credentials are involved, so these tests are never gated
behind ``REPOMESH_RUNNER_SMOKE``.
"""

import asyncio
import sys
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from repomesh_runner.drivers.base import (
    DriverEvent,
    DriverEventKind,
    DriverRequest,
    DriverResultStatus,
    PermissionDecision,
)
from repomesh_runner.drivers.stream_json import StreamJsonDriver
from repomesh_runner.drivers.supervision import SubprocessFactory
from repomesh_runner.profiles import get_profile

MOCK_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "components"
    / "repomesh-runner"
    / "mock"
    / "mock_coding_agent.py"
)

# In the image the launcher on PATH *is* the script, so argv carries nothing but
# the profile's own flags. Here the interpreter is the executable and the script
# path leads the argument list; the flags the profile contributes are unchanged.
PROFILE = replace(
    get_profile("mock"),
    base_arguments=(str(MOCK_SCRIPT), *get_profile("mock").base_arguments),
)

PROMPT = "make the failing test pass"


class StubPolicy:
    def __init__(self, decision: PermissionDecision = PermissionDecision.ALLOW) -> None:
        self.decision = decision
        self.calls: list[str] = []

    def decide(self, tool_name: str, tool_input: Mapping[str, object]) -> PermissionDecision:
        self.calls.append(tool_name)
        return self.decision


class Recorder:
    """Observer that also lets a test await the first event of a kind."""

    def __init__(self) -> None:
        self.events: list[DriverEvent] = []
        self._waits: dict[DriverEventKind, asyncio.Event] = {}

    def __call__(self, event: DriverEvent) -> None:
        self.events.append(event)
        waiter = self._waits.get(event.kind)
        if waiter is not None:
            waiter.set()

    def kinds(self) -> list[DriverEventKind]:
        return [event.kind for event in self.events]

    async def wait_for(self, kind: DriverEventKind, timeout: float = 20.0) -> None:
        if kind in self.kinds():
            return
        waiter = self._waits.setdefault(kind, asyncio.Event())
        await asyncio.wait_for(waiter.wait(), timeout=timeout)


def make_request(
    scenario: str,
    workspace: Path,
    *,
    policy: PermissionDecision = PermissionDecision.ALLOW,
    seed: str = "m4",
    **overrides: object,
) -> DriverRequest:
    fields: dict[str, object] = {
        "executable": sys.executable,
        "workspace": workspace,
        "prompt": PROMPT,
        "permission_policy": StubPolicy(policy),
        "environment": {
            "REPOMESH_MOCK_SCENARIO": scenario,
            "REPOMESH_MOCK_SESSION_SEED": seed,
            "REPOMESH_MOCK_STATE_DIR": str(workspace / "mock-state"),
        },
        "idle_window_seconds": 20.0,
        "tool_window_seconds": 20.0,
    }
    fields.update(overrides)
    return DriverRequest(**fields)  # type: ignore[arg-type]


async def run(scenario: str, workspace: Path, **overrides: object):
    recorder = Recorder()
    result = await StreamJsonDriver(SubprocessFactory()).execute(
        make_request(scenario, workspace, **overrides), PROFILE, recorder
    )
    return result, recorder


async def test_success_scenario_succeeds_with_a_deterministic_session(tmp_path: Path) -> None:
    result, recorder = await run("success", tmp_path)

    assert result.status is DriverResultStatus.SUCCEEDED, result.diagnostics
    assert "scenario success" in result.summary
    assert (result.native_session_id or "").startswith("mock-success-")
    assert result.transcript_path
    assert result.tool_call_count == 1
    assert DriverEventKind.SESSION_STARTED in recorder.kinds()
    assert DriverEventKind.TOOL_USE in recorder.kinds()
    assert DriverEventKind.TOOL_RESULT in recorder.kinds()


async def test_session_id_is_stable_for_a_seed_and_moves_with_it(tmp_path: Path) -> None:
    first, _ = await run("success", tmp_path, seed="alpha")
    again, _ = await run("success", tmp_path, seed="alpha")
    other, _ = await run("success", tmp_path, seed="beta")

    assert first.native_session_id == again.native_session_id
    assert first.native_session_id != other.native_session_id


async def test_test_failed_scenario_fails_with_its_own_subtype(tmp_path: Path) -> None:
    result, _ = await run("test_failed", tmp_path)

    assert result.status is DriverResultStatus.FAILED
    assert result.summary == ""
    assert "test_failed" in result.diagnostics
    assert "1 failed test" in result.diagnostics
    assert result.tool_call_count == 1


async def test_failed_scenario_fails_with_provider_diagnostics(tmp_path: Path) -> None:
    result, _ = await run("failed", tmp_path)

    assert result.status is DriverResultStatus.FAILED
    assert result.summary == ""
    assert "error_during_execution" in result.diagnostics
    assert "failed as instructed" in result.diagnostics
    # The mock also writes to stderr, but the driver reads its bounded tail the
    # moment the terminal frame lands, so whether the drain task has caught up
    # is a genuine race; asserting on the tail here would only be flaky.
    assert result.native_session_id


async def test_timeout_scenario_trips_the_idle_watchdog(tmp_path: Path) -> None:
    result, recorder = await run(
        "timeout", tmp_path, idle_window_seconds=2.0, tool_window_seconds=2.0
    )

    assert result.status is DriverResultStatus.TIMEOUT
    assert result.summary == ""
    assert "idle watchdog fired" in result.diagnostics
    # The session was announced before the silence started, so it stays
    # reportable: a timed-out run is resumable, unlike one that never opened.
    assert (result.native_session_id or "").startswith("mock-timeout-")
    assert DriverEventKind.SESSION_STARTED in recorder.kinds()


async def test_cancelled_scenario_is_interrupted_mid_stream(tmp_path: Path) -> None:
    """The mock heartbeats forever: only cancellation can end this run."""

    recorder = Recorder()
    task = asyncio.ensure_future(
        StreamJsonDriver(SubprocessFactory()).execute(
            make_request("cancelled", tmp_path), PROFILE, recorder
        )
    )
    await recorder.wait_for(DriverEventKind.THINKING)
    task.cancel()

    result = await task

    assert result.status is DriverResultStatus.INTERRUPTED
    assert result.summary == ""
    assert "cancelled" in result.diagnostics
    assert (result.native_session_id or "").startswith("mock-cancelled-")


async def test_interrupted_scenario_stops_with_a_tool_call_in_flight(tmp_path: Path) -> None:
    recorder = Recorder()
    task = asyncio.ensure_future(
        StreamJsonDriver(SubprocessFactory()).execute(
            make_request("interrupted", tmp_path), PROFILE, recorder
        )
    )
    await recorder.wait_for(DriverEventKind.TOOL_USE)
    task.cancel()

    result = await task

    assert result.status is DriverResultStatus.INTERRUPTED
    assert result.tool_call_count == 1
    assert DriverEventKind.TOOL_RESULT not in recorder.kinds()
    assert (result.native_session_id or "").startswith("mock-interrupted-")


async def test_question_required_scenario_ends_input_required_on_escalation(
    tmp_path: Path,
) -> None:
    result, recorder = await run(
        "question_required", tmp_path, policy=PermissionDecision.ESCALATE
    )

    assert result.status is DriverResultStatus.INPUT_REQUIRED
    assert result.summary == ""
    assert "WebFetch" in result.diagnostics
    assert (result.native_session_id or "").startswith("mock-question_required-")
    assert DriverEventKind.PERMISSION_REQUEST in recorder.kinds()


async def test_question_required_continues_when_the_platform_allows(tmp_path: Path) -> None:
    """Proves the mock really reads the driver's control_response frame."""

    result, _ = await run("question_required", tmp_path, policy=PermissionDecision.ALLOW)

    assert result.status is DriverResultStatus.SUCCEEDED, result.diagnostics
    assert "approved WebFetch" in result.summary


async def test_question_required_stops_when_the_platform_denies(tmp_path: Path) -> None:
    result, _ = await run("question_required", tmp_path, policy=PermissionDecision.DENY)

    assert result.status is DriverResultStatus.FAILED
    assert "permission_denied" in result.diagnostics
    assert "WebFetch" in result.diagnostics


async def test_resume_recalls_the_prompt_of_the_earlier_turn(tmp_path: Path) -> None:
    first, _ = await run("success", tmp_path)
    assert first.native_session_id

    second, _ = await run(
        "success",
        tmp_path,
        prompt="what did I ask for?",
        resume_session_id=first.native_session_id,
    )

    assert second.status is DriverResultStatus.SUCCEEDED, second.diagnostics
    assert second.native_session_id == first.native_session_id
    assert PROMPT in second.summary


async def test_unknown_resume_id_fails_without_reporting_a_session(tmp_path: Path) -> None:
    """The mock echoes the id back on the error frame; the driver must drop it."""

    result, recorder = await run(
        "success", tmp_path, resume_session_id="mock-success-000000000000"
    )

    assert result.status is DriverResultStatus.FAILED
    assert result.native_session_id is None
    assert "no mock session found" in result.diagnostics
    assert DriverEventKind.SESSION_STARTED not in recorder.kinds()


async def test_unknown_scenario_fails_instead_of_pretending(tmp_path: Path) -> None:
    result, _ = await run("not_a_scenario", tmp_path)

    assert result.status is DriverResultStatus.FAILED
    assert "unknown_scenario" in result.diagnostics
