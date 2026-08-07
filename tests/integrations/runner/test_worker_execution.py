from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from repomesh.integrations.runner import (
    AssessAssignedWorkerTask,
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
from repomesh.modules.agent_runtime.contracts import (
    AssessAssignedWorkerTaskCommand,
    WorkerPreflightDecision,
    WorkerPreflightView,
)
from repomesh.modules.agent_runtime.preflight_store import InMemoryWorkerPreflightStore
from repomesh.modules.task_orchestration.contracts import TaskStatus, TaskView
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


def assigned_worker_fixture():
    organization_id = uuid4()
    repository_id = uuid4()
    leader_id = uuid4()
    worker_id = uuid4()
    task = TaskView(
        id=uuid4(),
        organization_id=organization_id,
        project_id=uuid4(),
        repository_id=repository_id,
        parent_task_id=None,
        assigned_by_agent_id=leader_id,
        assignee_agent_id=worker_id,
        title="Pricing",
        instruction="Implement pricing.",
        acceptance=("Tests pass",),
        status=TaskStatus.ASSIGNED,
        result_summary=None,
        version=1,
    )
    principal = AgentPrincipalView(
        id=worker_id,
        organization_id=organization_id,
        role=AgentRole.WORKER,
        leader_agent_id=leader_id,
        repository_id=repository_id,
        responsibility_paths=("src/**",),
        agentteams_resource_name="pricing-worker",
        status=AgentPrincipalStatus.ACTIVE,
    )

    class Directory:
        async def get_view(self, agent_id):
            return principal

    class Tasks:
        async def get_view(self, task_id):
            return task

    return task, principal, Directory(), Tasks()


@pytest.mark.asyncio
async def test_worker_question_blocks_task_and_reports_to_leader() -> None:
    task, worker, directory, tasks = assigned_worker_fixture()
    preflights = InMemoryWorkerPreflightStore()
    states = StateStub()
    reporter = ReporterStub()

    result = await AssessAssignedWorkerTask(
        directory, tasks, preflights, states, reporter
    ).execute(
        AssessAssignedWorkerTaskCommand(
            task_id=task.id,
            worker_agent_id=worker.id,
            decision=WorkerPreflightDecision.QUESTION,
            spec_understood=True,
            scope_sufficient=False,
            tests_defined=True,
            dependencies_ready=True,
            notes="The allowed path does not include the pricing schema.",
        )
    )

    assert result.decision is WorkerPreflightDecision.QUESTION
    assert states.blocked_summary is not None
    assert reporter.command.status is TaskStatus.BLOCKED


@pytest.mark.asyncio
async def test_worker_ready_requires_every_preflight_check() -> None:
    task, worker, directory, tasks = assigned_worker_fixture()

    with pytest.raises(WorkerExecutionStartError, match="all checks"):
        await AssessAssignedWorkerTask(
            directory, tasks, InMemoryWorkerPreflightStore(), StateStub()
        ).execute(
            AssessAssignedWorkerTaskCommand(
                task_id=task.id,
                worker_agent_id=worker.id,
                decision=WorkerPreflightDecision.READY,
                spec_understood=True,
                scope_sufficient=True,
                tests_defined=False,
                dependencies_ready=True,
                notes="Tests are not yet defined.",
            )
        )


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

    class Tasks:
        async def get_view(self, task_id):
            return task_view

    class Directory:
        async def get_view(self, agent_id):
            return AgentPrincipalView(
                id=agent_id,
                organization_id=task_view.organization_id,
                role=AgentRole.WORKER,
                leader_agent_id=task_view.assigned_by_agent_id,
                repository_id=task_view.repository_id,
                responsibility_paths=("src/**",),
                agentteams_resource_name="pricing-worker",
                status=AgentPrincipalStatus.ACTIVE,
            )

    class Packages:
        async def execute(self, command):
            return package

    class Capabilities:
        async def execute(self, agent_id, *, task_features):
            return capabilities

    class Repositories:
        async def get(self, repository_id):
            return type("Repository", (), {"url": "https://example.test/pricing.git"})()

    class Workspaces:
        async def prepare(self, **kwargs):
            return type("Workspace", (), {"base_sha": "abc123"})()

    class Bundles:
        published = None

        async def execute(self, bundle, *, permission_layers):
            self.published = bundle

    class Execution:
        command = None

        async def execute(self, command):
            self.command = command
            return WorkerExecutionStarted(runner_task)

    states = StateStub()
    bundles = Bundles()
    execution = Execution()
    preflights = InMemoryWorkerPreflightStore()
    await preflights.save(
        WorkerPreflightView(
            task_id=task_view.id,
            worker_agent_id=task_view.assignee_agent_id,
            decision=WorkerPreflightDecision.READY,
            spec_understood=True,
            scope_sufficient=True,
            tests_defined=True,
            dependencies_ready=True,
            notes="Spec, scope, tests, and dependencies verified.",
            revision=1,
            assessed_at=datetime.now(UTC),
        )
    )
    result = await StartAssignedWorkerTask(
        Directory(),
        Tasks(),
        Packages(),
        Capabilities(),
        Repositories(),
        Workspaces(),
        bundles,
        execution,
        states,
        preflights,
    ).execute(
        StartAssignedWorkerTaskCommand(
            task_id=task_view.id,
            worker_agent_id=task_view.assignee_agent_id,
            adapter_id="claude-code",
        )
    )

    assert states.started is True
    assert bundles.published.run_id == execution.command.run_id
    assert bundles.published.id == execution.command.bundle_id
    assert bundles.published.workspace_id is not None
    assert result.task is runner_task


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
