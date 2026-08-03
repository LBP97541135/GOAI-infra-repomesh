import asyncio
import sys
from pathlib import Path

import pytest

from repomesh_runner.drivers.base import sanitize_session_id
from repomesh_runner.drivers.supervision import (
    IdleWatchdog,
    SpawnSpec,
    SubprocessFactory,
)


class TestSanitizeSessionId:
    def test_accepts_plain_ids(self) -> None:
        assert sanitize_session_id("session_abc-123") == "session_abc-123"
        assert sanitize_session_id("  padded  ") == "padded"

    def test_rejects_flag_like_control_and_oversized(self) -> None:
        assert sanitize_session_id("-rf") is None
        assert sanitize_session_id("has\x1bescape") is None
        assert sanitize_session_id("x" * 513) is None
        assert sanitize_session_id("") is None
        assert sanitize_session_id(42) is None


class TestIdleWatchdog:
    def test_dual_budget_switches_with_tool_flight(self) -> None:
        now = [0.0]
        watchdog = IdleWatchdog(10.0, 60.0, clock=lambda: now[0])
        assert watchdog.window_seconds == 10.0
        watchdog.tool_started()
        assert watchdog.window_seconds == 60.0
        watchdog.tool_finished()
        assert watchdog.window_seconds == 10.0

    def test_expiry_and_touch(self) -> None:
        now = [0.0]
        watchdog = IdleWatchdog(10.0, 60.0, clock=lambda: now[0])
        now[0] = 9.0
        assert not watchdog.expired()
        watchdog.touch()
        now[0] = 18.0
        assert not watchdog.expired()
        now[0] = 19.5
        assert watchdog.expired()
        assert watchdog.remaining() == 0.0

    def test_tool_finished_never_goes_negative(self) -> None:
        watchdog = IdleWatchdog(10.0, 60.0, clock=lambda: 0.0)
        watchdog.tool_finished()
        watchdog.tool_started()
        assert watchdog.window_seconds == 60.0


class TestSubprocessFactory:
    async def test_spawn_read_lines_and_exit(self, tmp_path: Path) -> None:
        spec = SpawnSpec(
            executable=sys.executable,
            arguments=("-c", "print('alpha'); print('beta')"),
            working_directory=tmp_path,
            environment={},
        )
        handle = await SubprocessFactory().spawn(spec)
        lines = [line async for line in handle.stdout_lines()]
        assert [line.strip() for line in lines] == [b"alpha", b"beta"]
        assert await handle.wait() == 0

    async def test_stderr_tail_is_captured(self, tmp_path: Path) -> None:
        spec = SpawnSpec(
            executable=sys.executable,
            arguments=("-c", "import sys; sys.stderr.write('boom-detail'); sys.exit(3)"),
            working_directory=tmp_path,
            environment={},
        )
        handle = await SubprocessFactory().spawn(spec)
        assert await handle.wait() == 3
        assert "boom-detail" in handle.stderr_tail()

    async def test_terminate_kills_a_hanging_process(self, tmp_path: Path) -> None:
        spec = SpawnSpec(
            executable=sys.executable,
            arguments=("-c", "import time; time.sleep(60)"),
            working_directory=tmp_path,
            environment={},
        )
        handle = await SubprocessFactory().spawn(spec)
        await asyncio.wait_for(handle.terminate(grace_seconds=1.0), timeout=15.0)
        exit_code = await asyncio.wait_for(handle.wait(), timeout=5.0)
        assert exit_code != 0

    async def test_environment_overlay_reaches_child(self, tmp_path: Path) -> None:
        spec = SpawnSpec(
            executable=sys.executable,
            arguments=("-c", "import os; print(os.environ['REPOMESH_PROBE'])"),
            working_directory=tmp_path,
            environment={"REPOMESH_PROBE": "carried"},
        )
        handle = await SubprocessFactory().spawn(spec)
        lines = [line async for line in handle.stdout_lines()]
        assert lines and lines[0].strip() == b"carried"
        await handle.wait()


@pytest.mark.parametrize("bad", ["\x00", "\n"])
def test_session_id_rejects_embedded_terminators(bad: str) -> None:
    assert sanitize_session_id(f"abc{bad}def") is None
