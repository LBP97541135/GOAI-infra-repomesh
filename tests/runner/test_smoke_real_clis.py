"""Real-machine smoke tests.

Gated twice: the CLI binary must be installed and REPOMESH_RUNNER_SMOKE=1 must
be set. These verify the drivers against real vendor CLIs; contract tests with
fake processes remain the primary safety net.
"""

import os
from pathlib import Path

import pytest

from repomesh_runner.drivers.acp import AcpDriver
from repomesh_runner.drivers.app_server import AppServerDriver
from repomesh_runner.drivers.base import (
    DriverRequest,
    DriverResultStatus,
    PermissionDecision,
)
from repomesh_runner.drivers.stream_json import StreamJsonDriver
from repomesh_runner.drivers.supervision import SubprocessFactory, resolve_binary
from repomesh_runner.profiles import get_profile

SMOKE_ENABLED = os.environ.get("REPOMESH_RUNNER_SMOKE") == "1"
KIMI = resolve_binary(("kimi",))
CLAUDE = resolve_binary(("claude",))
CODEX = resolve_binary(("codex",))


class DenyAllPolicy:
    def decide(self, tool_name: str, tool_input: object) -> PermissionDecision:
        return PermissionDecision.DENY


@pytest.mark.skipif(
    not (SMOKE_ENABLED and KIMI), reason="requires REPOMESH_RUNNER_SMOKE=1 and kimi installed"
)
async def test_kimi_acp_answers_a_trivial_prompt(tmp_path: Path) -> None:
    driver = AcpDriver(SubprocessFactory())
    request = DriverRequest(
        executable=KIMI or "",
        workspace=tmp_path,
        prompt="Reply with exactly the single word OK and do nothing else.",
        permission_policy=DenyAllPolicy(),
        idle_window_seconds=120.0,
    )
    events = []
    result = await driver.execute(request, get_profile("kimi"), events.append)

    assert result.status is DriverResultStatus.SUCCEEDED, result.diagnostics
    assert "ok" in result.summary.lower()
    assert result.native_session_id


@pytest.mark.skipif(
    not (SMOKE_ENABLED and CLAUDE), reason="requires REPOMESH_RUNNER_SMOKE=1 and claude installed"
)
async def test_claude_stream_json_reaches_a_terminal_state(tmp_path: Path) -> None:
    """Auth state is not assumed; the assertion is a clean terminal outcome.

    Authenticated: SUCCEEDED with a summary. Unauthenticated: FAILED with
    diagnostics — never a hang, never a false success.
    """

    driver = StreamJsonDriver(SubprocessFactory())
    request = DriverRequest(
        executable=CLAUDE or "",
        workspace=tmp_path,
        prompt="Reply with exactly the single word OK and do nothing else.",
        permission_policy=DenyAllPolicy(),
        idle_window_seconds=120.0,
    )
    result = await driver.execute(request, get_profile("claude-code"), lambda event: None)

    assert result.status in (DriverResultStatus.SUCCEEDED, DriverResultStatus.FAILED)
    if result.status is DriverResultStatus.SUCCEEDED:
        assert result.summary.strip()
    else:
        assert result.diagnostics


@pytest.mark.skipif(
    not (SMOKE_ENABLED and CODEX), reason="requires REPOMESH_RUNNER_SMOKE=1 and codex installed"
)
async def test_codex_app_server_answers_a_trivial_prompt(tmp_path: Path) -> None:
    """The turn/start response is non-terminal; success proves the driver
    waited for the turn/completed notification instead."""

    driver = AppServerDriver(SubprocessFactory())
    request = DriverRequest(
        executable=CODEX or "",
        workspace=tmp_path,
        prompt="Reply with exactly the single word OK. Do not run any commands.",
        permission_policy=DenyAllPolicy(),
        idle_window_seconds=180.0,
    )
    result = await driver.execute(request, get_profile("codex"), lambda event: None)

    assert result.status is DriverResultStatus.SUCCEEDED, result.diagnostics
    assert "ok" in result.summary.lower()
    assert result.native_session_id
    # codex reports the rollout path natively; it is never derived from the id.
    assert result.transcript_path
