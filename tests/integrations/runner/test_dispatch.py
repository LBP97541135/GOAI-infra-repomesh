from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from repomesh.integrations.runner import (
    DispatchWorkerTask,
    DispatchWorkerTaskCommand,
    RunnerContextMaterializer,
)
from repomesh.integrations.workspace import PreparedGitWorkspace

from .test_task_projection import scenario


class ReturningService:
    def __init__(self, value) -> None:
        self.value = value
        self.calls: list[object] = []

    async def execute(self, command=None, **kwargs):
        self.calls.append((command, kwargs))
        return self.value


class RepositoryCatalogStub:
    def __init__(self, repository_id, url: str) -> None:
        self.repository_id = repository_id
        self.url = url

    async def get(self, repository_id):
        if repository_id != self.repository_id:
            return None
        return SimpleNamespace(id=repository_id, url=self.url)


class WorkspaceStub:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.released = False

    async def prepare(self, **kwargs):
        return PreparedGitWorkspace("ws-1", self.path, "abc123", True)

    async def release(self, workspace) -> None:
        self.released = True


class GatewayStub:
    def __init__(self) -> None:
        self.task = None

    async def enqueue(self, task) -> None:
        self.task = task


@pytest.mark.asyncio
async def test_worker_dispatch_builds_mounts_and_enqueues(tmp_path: Path) -> None:
    projection, package, capabilities = scenario(tmp_path)
    grant = projection.context_grant
    packages = ReturningService(package)
    grants = ReturningService(grant)
    capability_service = ReturningService(capabilities)
    workspaces = WorkspaceStub(tmp_path)
    gateway = GatewayStub()
    dispatcher = DispatchWorkerTask(
        packages,
        grants,
        capability_service,
        RepositoryCatalogStub(package.repository_id, projection.repository_url),
        workspaces,
        RunnerContextMaterializer(Path.cwd()),
        gateway,
    )

    task = await dispatcher.execute(
        DispatchWorkerTaskCommand(
            organization_id=projection.organization_id,
            project_id=package.project_id,
            repository_id=package.repository_id,
            task_id=package.task_id,
            worker_agent_id=package.worker_agent_id,
            bundle_id=grant.bundle_id,
            run_id=grant.run_id,
            correlation_id=uuid4(),
            adapter_id="claude-code",
        )
    )

    assert gateway.task is task
    assert task.worker_agent_id == package.worker_agent_id
    assert task.workspace is not None and task.workspace.base_sha == "abc123"
    assert (tmp_path / ".repomesh/context/current-task.md").is_file()
    assert (tmp_path / ".repomesh/context/manifest.json").is_file()
    assert workspaces.released is False
