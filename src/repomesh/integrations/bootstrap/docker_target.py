import asyncio
import os
import re
from dataclasses import dataclass
from typing import Protocol

_LABEL_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_CONTAINER_ID = re.compile(r"^[a-f0-9]{12,64}$")


class DockerCommandError(RuntimeError):
    pass


class DockerTargetUnavailable(DockerCommandError):
    pass


class DockerTargetSafetyError(DockerCommandError):
    pass


class DockerCommandRunner(Protocol):
    async def run(self, arguments: tuple[str, ...]) -> str: ...


class AsyncDockerCommandRunner:
    def __init__(self, *, timeout_seconds: float = 10) -> None:
        if timeout_seconds <= 0:
            raise ValueError("Docker command timeout must be positive")
        self._timeout_seconds = timeout_seconds

    async def run(self, arguments: tuple[str, ...]) -> str:
        process = await asyncio.create_subprocess_exec(
            "docker",
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as error:
            process.kill()
            await process.wait()
            raise DockerCommandError("Docker command timed out") from error
        if process.returncode != 0:
            raise DockerCommandError("Docker command failed")
        return stdout.decode("utf-8", errors="strict").strip()


@dataclass(frozen=True, slots=True)
class DockerComposeTarget:
    container_id: str
    project: str
    service: str = "api"


class DockerComposeApiTargetSelector:
    def __init__(
        self,
        runner: DockerCommandRunner,
        *,
        own_container_id: str | None = None,
    ) -> None:
        self._runner = runner
        self._own_container_id = own_container_id or os.environ.get("HOSTNAME", "")
        if not self._own_container_id:
            raise ValueError("bootstrap container id is required")

    async def select(self) -> DockerComposeTarget:
        project = await self._own_project()
        output = await self._runner.run(
            (
                "ps",
                "--filter",
                f"label=com.docker.compose.project={project}",
                "--filter",
                "label=com.docker.compose.service=api",
                "--format",
                "{{.ID}}",
            )
        )
        matches = [line.strip() for line in output.splitlines() if line.strip()]
        if not matches:
            raise DockerTargetUnavailable("RepoMesh API container is not running")
        if len(matches) > 1:
            raise DockerTargetSafetyError("multiple RepoMesh API containers matched")
        container_id = matches[0]
        if not _CONTAINER_ID.fullmatch(container_id):
            raise DockerTargetSafetyError("Docker returned an invalid API container id")
        labels = await self._runner.run(
            (
                "inspect",
                "--format",
                '{{ index .Config.Labels "com.docker.compose.project" }}|'
                '{{ index .Config.Labels "com.docker.compose.service" }}',
                container_id,
            )
        )
        if labels != f"{project}|api":
            raise DockerTargetSafetyError(
                "API container labels do not match the bootstrap project"
            )
        return DockerComposeTarget(container_id=container_id, project=project)

    async def _own_project(self) -> str:
        project = await self._runner.run(
            (
                "inspect",
                "--format",
                '{{ index .Config.Labels "com.docker.compose.project" }}',
                self._own_container_id,
            )
        )
        if not _LABEL_VALUE.fullmatch(project):
            raise DockerTargetSafetyError(
                "bootstrap container has no valid Compose project label"
            )
        return project
