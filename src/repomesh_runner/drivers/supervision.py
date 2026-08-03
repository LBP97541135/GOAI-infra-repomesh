"""Process supervision for protocol drivers.

Owns spawning, bounded stderr capture, whole-tree termination, and the idle
watchdog. Drivers never touch asyncio.subprocess directly; tests substitute
``ProcessFactory`` with a scripted fake.
"""

import asyncio
import os
import shutil
import signal
import subprocess
import time
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

MAX_LINE_BYTES = 10 * 1024 * 1024
STDERR_TAIL_BYTES = 8 * 1024
_TERMINATE_GRACE_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class SpawnSpec:
    executable: str
    arguments: tuple[str, ...]
    working_directory: Path
    environment: Mapping[str, str]

    @property
    def argv(self) -> tuple[str, ...]:
        return (self.executable, *self.arguments)


class ProcessHandle(Protocol):
    @property
    def pid(self) -> int: ...

    def write_stdin(self, data: bytes) -> None: ...

    def close_stdin(self) -> None: ...

    def stdout_lines(self) -> AsyncIterator[bytes]: ...

    def stderr_tail(self) -> str: ...

    async def wait(self) -> int: ...

    async def terminate(self, grace_seconds: float = _TERMINATE_GRACE_SECONDS) -> None: ...


class ProcessFactory(Protocol):
    async def spawn(self, spec: SpawnSpec) -> ProcessHandle: ...


def resolve_binary(names: tuple[str, ...]) -> str | None:
    """Locate the first available binary, tolerating Windows launcher suffixes."""

    for name in names:
        if found := shutil.which(name):
            return found
    if os.name == "nt":
        roots = [Path.home() / ".local" / "bin"]
        if appdata := os.environ.get("APPDATA"):
            roots.append(Path(appdata) / "npm")
        for root in roots:
            for name in names:
                for suffix in (".exe", ".cmd"):
                    candidate = root / f"{name}{suffix}"
                    if candidate.is_file():
                        return str(candidate)
    return None


class _StderrTail:
    def __init__(self, limit: int = STDERR_TAIL_BYTES) -> None:
        self._limit = limit
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> None:
        self._buffer.extend(chunk)
        if len(self._buffer) > self._limit:
            del self._buffer[: len(self._buffer) - self._limit]

    def text(self) -> str:
        return self._buffer.decode(errors="replace")


class SubprocessHandle:
    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process
        self._tail = _StderrTail()
        self._stderr_task = asyncio.ensure_future(self._drain_stderr())
        self._stdin_closed = False

    @property
    def pid(self) -> int:
        return self._process.pid

    async def _drain_stderr(self) -> None:
        stream = self._process.stderr
        if stream is None:
            return
        while chunk := await stream.read(4096):
            self._tail.feed(chunk)

    def write_stdin(self, data: bytes) -> None:
        stdin = self._process.stdin
        if stdin is None or self._stdin_closed:
            raise RuntimeError("stdin is not writable")
        stdin.write(data)

    def close_stdin(self) -> None:
        stdin = self._process.stdin
        if stdin is not None and not self._stdin_closed:
            self._stdin_closed = True
            stdin.close()

    async def stdout_lines(self) -> AsyncIterator[bytes]:
        stream = self._process.stdout
        if stream is None:
            return
        while True:
            try:
                line = await stream.readline()
            except (ValueError, asyncio.LimitOverrunError):
                # Oversized line: skip forward rather than abort the stream.
                await self._discard_until_newline(stream)
                continue
            if not line:
                return
            yield line

    @staticmethod
    async def _discard_until_newline(stream: asyncio.StreamReader) -> None:
        while True:
            chunk = await stream.read(65536)
            if not chunk or b"\n" in chunk:
                return

    def stderr_tail(self) -> str:
        return self._tail.text()

    async def wait(self) -> int:
        code = await self._process.wait()
        if not self._stderr_task.done():
            try:
                await asyncio.wait_for(self._stderr_task, timeout=2.0)
            except TimeoutError:
                self._stderr_task.cancel()
        return code

    async def terminate(self, grace_seconds: float = _TERMINATE_GRACE_SECONDS) -> None:
        if self._process.returncode is not None:
            return
        self._signal_tree(force=False)
        try:
            await asyncio.wait_for(self._process.wait(), timeout=grace_seconds)
        except TimeoutError:
            self._signal_tree(force=True)
            await self._process.wait()
        # Pipes close only after the kill escalated; a SIGTERM-immune child
        # holding stdout must not strand the reader forever.
        self.close_stdin()

    def _signal_tree(self, *, force: bool) -> None:
        pid = self._process.pid
        if os.name == "nt":
            if force:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                )
            else:
                self._process.terminate()
            return
        try:
            group = os.getpgid(pid)
            os.killpg(group, signal.SIGKILL if force else signal.SIGTERM)
        except ProcessLookupError:
            pass


class SubprocessFactory:
    async def spawn(self, spec: SpawnSpec) -> ProcessHandle:
        environment = {**os.environ, **spec.environment}
        kwargs: dict[str, object] = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        process = await asyncio.create_subprocess_exec(
            *spec.argv,
            cwd=str(spec.working_directory),
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=MAX_LINE_BYTES,
            **kwargs,  # type: ignore[arg-type]
        )
        return SubprocessHandle(process)


class IdleWatchdog:
    """Dual-budget inactivity tracker.

    The active window is ``tool_window`` while at least one tool call is in
    flight (long installs and builds are legitimately silent) and
    ``idle_window`` otherwise. Drivers call :meth:`touch` on every protocol
    event and use :meth:`remaining` as their read timeout.
    """

    def __init__(
        self,
        idle_window_seconds: float,
        tool_window_seconds: float,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._idle_window = idle_window_seconds
        self._tool_window = tool_window_seconds
        self._clock = clock or time.monotonic
        self._last_activity = self._clock()
        self._tools_in_flight = 0

    def touch(self) -> None:
        self._last_activity = self._clock()

    def tool_started(self) -> None:
        self._tools_in_flight += 1
        self.touch()

    def tool_finished(self) -> None:
        self._tools_in_flight = max(0, self._tools_in_flight - 1)
        self.touch()

    @property
    def window_seconds(self) -> float:
        return self._tool_window if self._tools_in_flight > 0 else self._idle_window

    def remaining(self) -> float:
        elapsed = self._clock() - self._last_activity
        return max(0.0, self.window_seconds - elapsed)

    def expired(self) -> bool:
        return self.remaining() <= 0.0
