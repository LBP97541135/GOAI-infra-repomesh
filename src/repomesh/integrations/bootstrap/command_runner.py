import asyncio
import os
from collections.abc import Mapping
from dataclasses import dataclass


class BootstrapCommandError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BootstrapCommandResult:
    returncode: int
    stdout: bytes


class BootstrapCommandRunner:
    def __init__(self, *, output_limit: int = 65536) -> None:
        if output_limit < 1024:
            raise ValueError("bootstrap output limit must be at least 1024 bytes")
        self._output_limit = output_limit

    async def run(
        self,
        arguments: tuple[str, ...],
        *,
        stdin: bytes | None = None,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: float = 30,
        capture_stdout: bool = True,
    ) -> BootstrapCommandResult:
        if not arguments or not arguments[0]:
            raise ValueError("bootstrap command requires an executable")
        if timeout_seconds <= 0:
            raise ValueError("bootstrap command timeout must be positive")
        child_environment = os.environ.copy()
        if environment:
            child_environment.update(environment)
        process = await asyncio.create_subprocess_exec(
            *arguments,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE if capture_stdout else asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=child_environment,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(input=stdin),
                timeout=timeout_seconds,
            )
        except TimeoutError as error:
            process.kill()
            await process.wait()
            raise BootstrapCommandError("bootstrap command timed out") from error
        output = stdout or b""
        if len(output) > self._output_limit:
            raise BootstrapCommandError("bootstrap command output exceeded the safe limit")
        return BootstrapCommandResult(returncode=process.returncode, stdout=output)
