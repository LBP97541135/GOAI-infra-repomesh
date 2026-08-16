from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from repomesh.integrations.runner import (
    DispatchWorkerTask,
    DispatchWorkerTaskCommand,
    RunnerContextMaterializer,
)
from repomesh.integrations.workspace import PreparedGitWorkspace
from repomesh.modules.repository_intelligence.domain import RepositoryProfile

from .test_task_projection import scenario


class ReturningService:
    def __init__(self, value) -> None:
        self.value = value
        self.calls: list[object] = []

    async def execute(self, command=None, **kwargs):
        self.calls.append((command, kwargs))
        return self.value


class RepositoryCatalogStub:
    """Answers with a real ``RepositoryProfile``, not a shape that resembles one.

    It used to be a ``SimpleNamespace`` of the two fields the dispatcher happened
    to read. The dispatcher now also reads ``test_commands`` (defect A-19), and a
    hand-rolled double would have gone on answering the old shape happily while
    production read a field the double never had.
    """

    def __init__(
        self,
        repository_id,
        url: str,
        *,
        test_commands: tuple[str, ...] = (),
        test_paths: tuple[str, ...] = (),
    ) -> None:
        self.repository_id = repository_id
        self.url = url
        self.test_commands = test_commands
        self.test_paths = test_paths

    async def get(self, repository_id):
        if repository_id != self.repository_id:
            return None
        return RepositoryProfile(
            id=repository_id,
            name="pricing",
            url=self.url,
            test_commands=self.test_commands,
            test_paths=self.test_paths,
        )


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


async def _dispatch(
    tmp_path: Path,
    *,
    spec_tests: tuple[str, ...] = (),
    catalog_tests: tuple[str, ...] = (),
    spec_paths: tuple[str, ...] | None = None,
    grant_paths: tuple[str, ...] | None = None,
    catalog_paths: tuple[str, ...] = (),
):
    """Run one real dispatch, stubbing only what talks to the world.

    One ``scenario`` call, because the projector checks that the grant, the
    package and the run all name the same project, repository, worker and run —
    two scenarios would be two sets of ids and the dispatch would be refused
    before it ever reached the question this is asking.
    """

    projection, package, capabilities = scenario(tmp_path)
    package = replace(package, test_commands=spec_tests)
    grant = projection.context_grant
    if spec_paths is not None:
        package = replace(package, allowed_paths=spec_paths)
    if grant_paths is not None:
        grant = replace(grant, allowed_paths=grant_paths)
    gateway = GatewayStub()
    dispatcher = DispatchWorkerTask(
        ReturningService(package),
        ReturningService(grant),
        ReturningService(capabilities),
        RepositoryCatalogStub(
            package.repository_id,
            projection.repository_url,
            test_commands=catalog_tests,
            test_paths=catalog_paths,
        ),
        WorkspaceStub(tmp_path),
        RunnerContextMaterializer(Path.cwd()),
        gateway,
    )
    return await dispatcher.execute(
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


@pytest.mark.asyncio
async def test_a_pre_fix_task_row_dispatches_with_the_catalogs_commands(
    tmp_path: Path,
) -> None:
    """Defect A-19: the shape a re-dispatch of an old round actually has.

    A round materialized before the console supplied verification commands has
    an empty ``tests`` written into its Specification, and re-dispatch replays
    that row verbatim — the package is rebuilt from the same stored spec, so it
    comes back empty again no matter how many times the operator presses the
    button. Live: re-dispatched run 8aa3b0a5 completed with ``testResults: []``
    while its catalog row already read ``["python scripts/run_tests.py"]``.

    Driven through the real ``DispatchWorkerTask`` rather than the projector
    alone, because the fix is only worth anything if the dispatcher actually
    hands the catalog's answer over — which is the half a projector-level test
    cannot see.
    """

    task = await _dispatch(
        tmp_path,
        spec_tests=(),
        catalog_tests=("python scripts/run_tests.py",),
    )

    assert task.test_commands == ("python scripts/run_tests.py",)


@pytest.mark.asyncio
async def test_a_dispatch_whose_repository_declares_nothing_stays_empty(
    tmp_path: Path,
) -> None:
    """The honest half, asserted through the same real path.

    Without this the previous test passes just as well against a fallback that
    invented a command, and "the catalog said so" would stop being checkable.
    """

    task = await _dispatch(tmp_path, spec_tests=(), catalog_tests=())

    assert task.test_commands == ()


@pytest.mark.asyncio
async def test_a_pre_fix_round_redispatches_with_its_test_directory_permitted(
    tmp_path: Path,
) -> None:
    """Defect A-21, in the shape a re-dispatch of the failed round actually has.

    The Specification of a round materialized before this fix carries only the
    Worker's responsibility paths — ``src/checkout/**`` — and re-dispatch
    rebuilds the package from that same stored row, so the narrow permit comes
    back however many times the operator presses the button. Live, the run that
    followed died on ``changed_path_denied: tests/test_discount.py`` with a null
    commitSha: the agent had written the test exactly where its own verification
    command discovers from.

    Driven through the real ``DispatchWorkerTask`` because the fix is only
    worth anything if the dispatcher hands the catalog's answer over, and
    because the projector validates the widened list against the grant — the
    half a projector-level test cannot see.
    """

    task = await _dispatch(
        tmp_path,
        spec_paths=("src/checkout/**",),
        grant_paths=("src/checkout/**", "tests/**"),
        catalog_paths=("tests/**",),
    )

    assert task.permissions.allowed_paths == ("src/checkout/**", "tests/**")


@pytest.mark.asyncio
async def test_a_repository_with_no_declared_test_paths_dispatches_unchanged(
    tmp_path: Path,
) -> None:
    """The honest half, through the same real path.

    Without it the rescue above passes just as well against a fallback that
    invented ``tests/**`` for everyone, and "the catalog said so" would stop
    being a checkable claim about a permission grant.
    """

    task = await _dispatch(
        tmp_path,
        spec_paths=("src/checkout/**",),
        grant_paths=("src/checkout/**", "tests/**"),
    )

    assert task.permissions.allowed_paths == ("src/checkout/**",)
