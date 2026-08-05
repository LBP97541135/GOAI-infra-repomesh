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
    # Observed flaky roughly one run in three (2026-08-05) while the resume
    # tests below passed consistently. The wording is what is unproven, so the
    # summary travels with the failure rather than the assertion being relaxed.
    assert "ok" in result.summary.lower(), f"summary was {result.summary!r}"
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


# -- session resume ------------------------------------------------------
#
# Each test plants a nonce in turn one and asks a NEW driver instance to recall
# it in turn two, so a pass proves the CLI really reloaded the session rather
# than the driver holding state. Nonces are distinct per test: a session leaked
# from another test cannot fake a pass.

PLANT = "Remember this codeword: {nonce}. Reply with exactly OK. Do not run any commands."
RECALL = (
    "What codeword did I ask you to remember? Reply with only that word. "
    "Do not run any commands."
)
UNKNOWN_SESSION_ID = "00000000-0000-4000-8000-00000000dead"


def resume_request(executable: str, workspace: Path, prompt: str, **overrides: object):
    fields: dict[str, object] = {
        "executable": executable,
        "workspace": workspace,
        "prompt": prompt,
        "permission_policy": DenyAllPolicy(),
        "idle_window_seconds": 180.0,
    }
    fields.update(overrides)
    return DriverRequest(**fields)  # type: ignore[arg-type]


@pytest.mark.skipif(
    not (SMOKE_ENABLED and CODEX), reason="requires REPOMESH_RUNNER_SMOKE=1 and codex installed"
)
async def test_codex_resumes_a_thread_and_recalls_it(tmp_path: Path) -> None:
    profile = get_profile("codex")
    first = await AppServerDriver(SubprocessFactory()).execute(
        resume_request(CODEX or "", tmp_path, PLANT.format(nonce="marmoset")),
        profile,
        lambda event: None,
    )
    assert first.status is DriverResultStatus.SUCCEEDED, first.diagnostics
    assert first.native_session_id

    second = await AppServerDriver(SubprocessFactory()).execute(
        resume_request(
            CODEX or "", tmp_path, RECALL, resume_session_id=first.native_session_id
        ),
        profile,
        lambda event: None,
    )

    assert second.status is DriverResultStatus.SUCCEEDED, second.diagnostics
    assert "marmoset" in second.summary.lower()
    assert second.native_session_id == first.native_session_id


@pytest.mark.skipif(
    not (SMOKE_ENABLED and CODEX), reason="requires REPOMESH_RUNNER_SMOKE=1 and codex installed"
)
async def test_codex_rejects_an_unknown_thread_id(tmp_path: Path) -> None:
    result = await AppServerDriver(SubprocessFactory()).execute(
        resume_request(
            CODEX or "", tmp_path, RECALL, resume_session_id=UNKNOWN_SESSION_ID
        ),
        get_profile("codex"),
        lambda event: None,
    )

    assert result.status is DriverResultStatus.FAILED
    assert result.diagnostics
    assert result.native_session_id is None


@pytest.mark.skipif(
    not (SMOKE_ENABLED and KIMI), reason="requires REPOMESH_RUNNER_SMOKE=1 and kimi installed"
)
async def test_kimi_resumes_a_session_and_recalls_it(tmp_path: Path) -> None:
    profile = get_profile("kimi")
    first = await AcpDriver(SubprocessFactory()).execute(
        resume_request(KIMI or "", tmp_path, PLANT.format(nonce="quartzite")),
        profile,
        lambda event: None,
    )
    assert first.status is DriverResultStatus.SUCCEEDED, first.diagnostics
    assert first.native_session_id

    second = await AcpDriver(SubprocessFactory()).execute(
        resume_request(KIMI or "", tmp_path, RECALL, resume_session_id=first.native_session_id),
        profile,
        lambda event: None,
    )

    assert second.status is DriverResultStatus.SUCCEEDED, second.diagnostics
    assert "quartzite" in second.summary.lower()
    assert second.native_session_id == first.native_session_id


@pytest.mark.skipif(
    not (SMOKE_ENABLED and KIMI), reason="requires REPOMESH_RUNNER_SMOKE=1 and kimi installed"
)
async def test_kimi_rejects_an_unknown_session_id(tmp_path: Path) -> None:
    result = await AcpDriver(SubprocessFactory()).execute(
        resume_request(KIMI or "", tmp_path, RECALL, resume_session_id=UNKNOWN_SESSION_ID),
        get_profile("kimi"),
        lambda event: None,
    )

    assert result.status is DriverResultStatus.FAILED
    assert result.diagnostics
    assert result.native_session_id is None


@pytest.mark.skipif(
    not (SMOKE_ENABLED and CLAUDE), reason="requires REPOMESH_RUNNER_SMOKE=1 and claude installed"
)
async def test_claude_resumes_a_session_and_recalls_it(tmp_path: Path) -> None:
    """Auth is not assumed: an unauthenticated CLI fails turn one cleanly and
    the test skips rather than reporting a confusing resume failure."""

    profile = get_profile("claude-code")
    first = await StreamJsonDriver(SubprocessFactory()).execute(
        resume_request(CLAUDE or "", tmp_path, PLANT.format(nonce="basalt")),
        profile,
        lambda event: None,
    )
    if first.status is not DriverResultStatus.SUCCEEDED:
        assert first.status is DriverResultStatus.FAILED
        assert first.diagnostics
        pytest.skip(f"claude did not complete turn one: {first.diagnostics[:200]}")
    assert first.native_session_id

    second = await StreamJsonDriver(SubprocessFactory()).execute(
        resume_request(CLAUDE or "", tmp_path, RECALL, resume_session_id=first.native_session_id),
        profile,
        lambda event: None,
    )

    assert second.status is DriverResultStatus.SUCCEEDED, second.diagnostics
    assert "basalt" in second.summary.lower()
    assert second.native_session_id == first.native_session_id


@pytest.mark.skipif(
    not (SMOKE_ENABLED and CLAUDE), reason="requires REPOMESH_RUNNER_SMOKE=1 and claude installed"
)
async def test_claude_rejects_an_unknown_session_id(tmp_path: Path) -> None:
    """The failure must not carry the id back: no session was ever opened, so
    reporting it would make the run look retryable by resume."""

    result = await StreamJsonDriver(SubprocessFactory()).execute(
        resume_request(CLAUDE or "", tmp_path, RECALL, resume_session_id=UNKNOWN_SESSION_ID),
        get_profile("claude-code"),
        lambda event: None,
    )

    assert result.status is DriverResultStatus.FAILED
    assert result.diagnostics
    assert result.native_session_id is None
