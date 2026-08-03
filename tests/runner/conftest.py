"""Shared fixtures for runner driver tests: a scripted fake process."""

import asyncio
import json
from collections.abc import AsyncIterator, Iterable

import pytest

from repomesh_runner.drivers.supervision import ProcessHandle, SpawnSpec


class FakeProcess:
    """Scripted ProcessHandle: emits predefined stdout lines, records stdin.

    Lines may be str/bytes (emitted immediately), a float (async delay), or a
    callable invoked with the list of stdin frames received so far — letting
    tests emit responses conditioned on what the driver wrote.
    """

    def __init__(
        self,
        script: Iterable[object] = (),
        *,
        exit_code: int = 0,
        stderr: str = "",
    ) -> None:
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


class FakeProcessFactory:
    def __init__(self, process: FakeProcess) -> None:
        self.process = process
        self.spawned_specs: list[SpawnSpec] = []

    async def spawn(self, spec: SpawnSpec) -> ProcessHandle:
        self.spawned_specs.append(spec)
        return self.process


@pytest.fixture
def fake_factory():
    def build(script: Iterable[object] = (), **kwargs: object) -> FakeProcessFactory:
        return FakeProcessFactory(FakeProcess(script, **kwargs))  # type: ignore[arg-type]

    return build
