from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from repomesh.integrations.runner import (
    DispatchWorkerTaskCommand,
    RunnerTaskProjector,
    StartAssignedWorkerTask,
    StartAssignedWorkerTaskCommand,
    StartWorkerTaskExecution,
    WorkerExecutionStarted,
    WorkerExecutionStartError,
)
from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalStatus,
    AgentPrincipalView,
    AgentRole,
)
from repomesh.modules.agent_runtime.contracts import ActiveWorkerDispatch
from repomesh.modules.agent_runtime.runner_store import (
    ACTIVE_DISPATCH_STATUSES,
    PostgresRunnerGatewayStore,
)
from repomesh.modules.repository_intelligence.domain import RepositoryProfile
from repomesh.modules.task_orchestration.contracts import TaskStatus, TaskView
from repomesh.persistence import Database
from repomesh.persistence.base import ALL_SCHEMAS
from repomesh_runner.contracts import RunnerTask

from .test_task_projection import scenario


class StateStub:
    def __init__(self) -> None:
        self.started = False
        self.blocked_summary: str | None = None

    async def start(self, task_id, *, agent_id):
        self.started = True

    async def block(self, task_id, *, agent_id, summary):
        self.blocked_summary = summary


class DispatcherStub:
    def __init__(self, task: RunnerTask | None = None, error: Exception | None = None) -> None:
        self.task = task
        self.error = error

    async def execute(self, command):
        if self.error is not None:
            raise self.error
        return self.task


class ReporterStub:
    def __init__(self) -> None:
        self.command = None

    async def report(self, command, *, idempotency_key):
        self.command = command


def command_for(task: RunnerTask) -> DispatchWorkerTaskCommand:
    return DispatchWorkerTaskCommand(
        organization_id=task.organization_id,
        project_id=task.project_id,
        repository_id=task.repository.repository_id,
        task_id=task.task_id,
        worker_agent_id=task.worker_agent_id or uuid4(),
        bundle_id=task.context_bundle.bundle_id,
        run_id=task.run_id,
        correlation_id=task.correlation_id,
        adapter_id=task.adapter_id,
    )


class DispatchStoreFake:
    """In-memory stand-in for the durable runner dispatch table."""

    def __init__(self) -> None:
        self.dispatches: list[ActiveWorkerDispatch] = []

    def enqueue(self, task: RunnerTask) -> None:
        assert task.worker_agent_id is not None
        self.dispatches.append(
            ActiveWorkerDispatch(
                run_id=task.run_id,
                task_id=task.task_id,
                worker_agent_id=task.worker_agent_id,
                attempt=task.attempt,
                status="queued",
                task_payload=task.to_wire(),
            )
        )

    def finish(self, run_id: UUID, status: str = "completed") -> None:
        self.dispatches = [
            replace(dispatch, status=status) if dispatch.run_id == run_id else dispatch
            for dispatch in self.dispatches
        ]

    async def get_active_dispatch_for_task(
        self, task_id: UUID, *, worker_agent_id: UUID
    ) -> ActiveWorkerDispatch | None:
        for dispatch in reversed(self.dispatches):
            if (
                dispatch.task_id == task_id
                and dispatch.worker_agent_id == worker_agent_id
                and dispatch.status in ACTIVE_DISPATCH_STATUSES
            ):
                return dispatch
        return None


class TasksStub:
    def __init__(self, task_view: TaskView) -> None:
        self.task_view = task_view

    async def get_view(self, task_id):
        return self.task_view if task_id == self.task_view.id else None


class DirectoryStub:
    def __init__(self, task_view: TaskView) -> None:
        self.task_view = task_view

    async def get_view(self, agent_id):
        return AgentPrincipalView(
            id=agent_id,
            organization_id=self.task_view.organization_id,
            role=AgentRole.WORKER,
            leader_agent_id=self.task_view.assigned_by_agent_id,
            repository_id=self.task_view.repository_id,
            responsibility_paths=("src/**",),
            agentteams_resource_name="pricing-worker",
            status=AgentPrincipalStatus.ACTIVE,
        )


class PackagesStub:
    def __init__(self, package) -> None:
        self.package = package

    async def execute(self, command):
        return self.package


class CapabilitiesStub:
    def __init__(self, capabilities) -> None:
        self.capabilities = capabilities

    async def execute(self, agent_id, *, task_features):
        return self.capabilities


class RepositoriesStub:
    """Answers with a real ``RepositoryProfile``, not a type minted on the spot.

    It used to be an anonymous class carrying the one attribute the code under
    test happened to read. The bundle build now also reads ``test_paths``
    (defect A-21), and a double shaped by yesterday's reads is a double that
    goes green while production raises ``AttributeError`` — which is exactly
    what it did.
    """

    def __init__(self, *, test_paths: tuple[str, ...] = ()) -> None:
        self.test_paths = test_paths

    async def get(self, repository_id):
        return RepositoryProfile(
            id=repository_id,
            name="pricing",
            url="https://example.test/pricing.git",
            test_paths=self.test_paths,
        )


class WorkspacesStub:
    def __init__(self) -> None:
        self.prepared = 0

    async def prepare(self, **kwargs):
        self.prepared += 1
        return type("Workspace", (), {"base_sha": "abc123"})()


class BundlesStub:
    def __init__(self) -> None:
        self.published: list[object] = []

    async def execute(self, bundle, *, permission_layers):
        self.published.append(bundle)


class ExecutionStub:
    """Replace the dispatch pipeline and record the resulting run in the dispatch store."""

    def __init__(self, runner_task: RunnerTask, store: DispatchStoreFake) -> None:
        self._runner_task = runner_task
        self._store = store
        self.commands: list[DispatchWorkerTaskCommand] = []

    async def execute(self, command: DispatchWorkerTaskCommand) -> WorkerExecutionStarted:
        self.commands.append(command)
        task = replace(self._runner_task, run_id=command.run_id)
        self._store.enqueue(task)
        return WorkerExecutionStarted(task)


@dataclass(frozen=True, slots=True)
class AssignedTaskHarness:
    service: StartAssignedWorkerTask
    task_view: TaskView
    store: DispatchStoreFake
    workspaces: WorkspacesStub
    bundles: BundlesStub
    execution: ExecutionStub
    states: StateStub

    def command(self, worker_agent_id: UUID | None = None) -> StartAssignedWorkerTaskCommand:
        return StartAssignedWorkerTaskCommand(
            task_id=self.task_view.id,
            worker_agent_id=worker_agent_id or self.task_view.assignee_agent_id,
            adapter_id="claude-code",
        )


def assigned_task_harness(
    tmp_path: Path, *, catalog_test_paths: tuple[str, ...] = ()
) -> AssignedTaskHarness:
    projection, package, capabilities = scenario(tmp_path)
    runner_task = RunnerTaskProjector().project(projection)
    task_view = TaskView(
        id=package.task_id,
        organization_id=projection.organization_id,
        project_id=package.project_id,
        repository_id=package.repository_id,
        parent_task_id=None,
        assigned_by_agent_id=uuid4(),
        assignee_agent_id=package.worker_agent_id,
        title="Pricing",
        instruction=package.instruction,
        acceptance=package.acceptance,
        status=TaskStatus.ASSIGNED,
        result_summary=None,
        version=1,
    )
    store = DispatchStoreFake()
    workspaces = WorkspacesStub()
    bundles = BundlesStub()
    execution = ExecutionStub(runner_task, store)
    states = StateStub()
    return AssignedTaskHarness(
        service=StartAssignedWorkerTask(
            DirectoryStub(task_view),
            TasksStub(task_view),
            PackagesStub(package),
            CapabilitiesStub(capabilities),
            RepositoriesStub(test_paths=catalog_test_paths),
            workspaces,
            bundles,
            execution,
            states,
            None,
            store,
        ),
        task_view=task_view,
        store=store,
        workspaces=workspaces,
        bundles=bundles,
        execution=execution,
        states=states,
    )


@pytest.mark.asyncio
async def test_start_action_transitions_before_dispatch(tmp_path: Path) -> None:
    projection, _, _ = scenario(tmp_path)
    task = RunnerTaskProjector().project(projection)
    states = StateStub()

    result = await StartWorkerTaskExecution(states, DispatcherStub(task)).execute(command_for(task))

    assert states.started is True
    assert result.task is task
    assert result.status is TaskStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_dispatch_failure_blocks_and_reports_to_leader(tmp_path: Path) -> None:
    projection, _, _ = scenario(tmp_path)
    task = RunnerTaskProjector().project(projection)
    states = StateStub()
    reporter = ReporterStub()

    with pytest.raises(WorkerExecutionStartError, match="checkout failed"):
        await StartWorkerTaskExecution(
            states,
            DispatcherStub(error=RuntimeError("checkout failed")),
            reporter,
        ).execute(command_for(task))

    assert states.blocked_summary is not None
    assert reporter.command.status is TaskStatus.BLOCKED


@pytest.mark.asyncio
async def test_assigned_task_derives_run_workspace_and_context_bundle(tmp_path: Path) -> None:
    harness = assigned_task_harness(tmp_path)

    result = await harness.service.execute(harness.command())

    dispatched = harness.execution.commands[0]
    published = harness.bundles.published[0]
    assert harness.states.started is True
    assert published.run_id == dispatched.run_id
    assert published.id == dispatched.bundle_id
    assert published.workspace_id is not None
    assert result.task.run_id == dispatched.run_id


@pytest.mark.asyncio
async def test_repeated_start_reuses_the_in_flight_run(tmp_path: Path) -> None:
    harness = assigned_task_harness(tmp_path)

    first = await harness.service.execute(harness.command())
    second = await harness.service.execute(harness.command())

    assert second.task.run_id == first.task.run_id
    assert second.task.workspace == first.task.workspace
    assert second.task.context_bundle == first.task.context_bundle
    assert second.status is TaskStatus.IN_PROGRESS
    assert len(harness.execution.commands) == 1
    assert len(harness.store.dispatches) == 1
    assert harness.workspaces.prepared == 1
    assert len(harness.bundles.published) == 1


@pytest.mark.asyncio
async def test_start_after_a_terminal_dispatch_creates_a_new_run(tmp_path: Path) -> None:
    harness = assigned_task_harness(tmp_path)
    first = await harness.service.execute(harness.command())
    harness.store.finish(first.task.run_id)

    second = await harness.service.execute(harness.command())

    assert second.task.run_id != first.task.run_id
    assert len(harness.execution.commands) == 2
    assert harness.workspaces.prepared == 2


@pytest.mark.asyncio
async def test_in_flight_run_is_never_handed_to_another_worker(tmp_path: Path) -> None:
    harness = assigned_task_harness(tmp_path)
    await harness.service.execute(harness.command())

    with pytest.raises(WorkerExecutionStartError, match="not assigned to this task"):
        await harness.service.execute(harness.command(worker_agent_id=uuid4()))

    assert len(harness.execution.commands) == 1
    assert harness.workspaces.prepared == 1


@pytest.mark.asyncio
async def test_active_dispatch_query_ignores_terminal_runs(tmp_path: Path) -> None:
    database = Database(
        f"sqlite+aiosqlite:///{tmp_path / 'dispatch.db'}",
        schema_translate_map={schema: None for schema in ALL_SCHEMAS},
    )
    await database.create_all_for_tests()
    store = PostgresRunnerGatewayStore(database)
    projection, package, _ = scenario(tmp_path)
    runner_task = RunnerTaskProjector().project(projection)
    await store.enqueue(runner_task.to_wire())

    active = await store.get_active_dispatch_for_task(
        runner_task.task_id, worker_agent_id=package.worker_agent_id
    )
    assert active is not None
    assert active.run_id == runner_task.run_id
    assert active.status == "queued"
    assert active.task_payload["runId"] == str(runner_task.run_id)

    assert (
        await store.get_active_dispatch_for_task(runner_task.task_id, worker_agent_id=uuid4())
    ) is None

    assert await store.record_event(_completed_event(runner_task)) is True
    assert (
        await store.get_active_dispatch_for_task(
            runner_task.task_id, worker_agent_id=package.worker_agent_id
        )
    ) is None
    await database.dispose()


def _completed_event(task: RunnerTask) -> dict[str, object]:
    return {
        "eventId": str(uuid4()),
        "eventType": "runner.completed",
        "organizationId": str(task.organization_id),
        "projectId": str(task.project_id),
        "taskId": str(task.task_id),
        "runId": str(task.run_id),
        "attempt": task.attempt,
        "sequence": 1,
        "occurredAt": datetime.now(UTC).isoformat(),
        "payload": {"summary": "done"},
    }


@pytest.mark.asyncio
async def test_repository_leader_cannot_start_coding_execution() -> None:
    worker_id = uuid4()

    class Directory:
        async def get_view(self, agent_id):
            return AgentPrincipalView(
                id=agent_id,
                organization_id=uuid4(),
                role=AgentRole.REPOSITORY_LEADER,
                leader_agent_id=uuid4(),
                repository_id=uuid4(),
                responsibility_paths=("src/**",),
                agentteams_resource_name="pricing-leader",
                status=AgentPrincipalStatus.ACTIVE,
            )

    service = StartAssignedWorkerTask(
        Directory(),
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )
    with pytest.raises(
        WorkerExecutionStartError,
        match="restricted to Worker identities",
    ):
        await service.execute(
            StartAssignedWorkerTaskCommand(
                task_id=uuid4(),
                worker_agent_id=worker_id,
                adapter_id="claude-code",
            )
        )


@pytest.mark.asyncio
async def test_the_context_grant_covers_the_repositorys_test_paths(tmp_path: Path) -> None:
    """Defect A-21: widening the payload without the grant only moves the refusal.

    The projector validates every path in the dispatch against the execution
    grant, so a payload carrying ``tests/**`` that the grant does not name is
    denied — "package paths exceed the execution grant" instead of
    ``changed_path_denied``, which is a different message for the same dead
    round. The bundle is built fresh on every run, including a re-dispatch, so
    it is where the catalog's current answer has to land.

    The Specification's own paths are still there and still first: the grant is
    widened, never rewritten.

    The catalog path here is deliberately one the fixture package does NOT
    already carry. Asserting with ``tests/**`` — which the scenario's own
    ``allowed_paths`` already contains — would pass identically with the union
    deleted, and a test that cannot fail is not evidence of anything.
    """

    harness = assigned_task_harness(tmp_path, catalog_test_paths=("integration-tests/**",))

    await harness.service.execute(harness.command())

    assert len(harness.bundles.published) == 1
    granted = harness.bundles.published[0].allowed_paths
    assert granted == ("src/**", "tests/**", "integration-tests/**")


@pytest.mark.asyncio
async def test_a_grant_for_a_repository_that_declared_no_test_paths_is_unchanged(
    tmp_path: Path,
) -> None:
    """No directory is granted to a repository that never named one."""

    harness = assigned_task_harness(tmp_path)

    await harness.service.execute(harness.command())

    assert harness.bundles.published[0].allowed_paths == ("src/**", "tests/**")

