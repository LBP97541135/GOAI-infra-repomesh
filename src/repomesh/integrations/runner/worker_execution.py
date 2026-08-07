from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from repomesh.integrations.workspace import GitWorktreeManager
from repomesh.modules.agent_directory.contracts import AgentPrincipalReader, AgentRole
from repomesh.modules.agent_runtime.contracts import (
    AssessAssignedWorkerTaskCommand,
    DispatchWorkerTaskCommand,
    StartAssignedWorkerTaskCommand,
    WorkerPreflightDecision,
    WorkerPreflightStore,
    WorkerPreflightView,
)
from repomesh.modules.capability_management import ResolveAgentCapabilities
from repomesh.modules.context.application import PublishContextBundle
from repomesh.modules.context.domain import ContextBundle
from repomesh.modules.repository_intelligence.ports.catalog import RepositoryCatalog
from repomesh.modules.specification import (
    BuildCodingAgentPackage,
    BuildCodingAgentPackageCommand,
)
from repomesh.modules.task_orchestration.contracts import (
    ReportTaskCommand,
    TaskExecutionMode,
    TaskExecutionStateGateway,
    TaskReader,
    TaskReportGateway,
    TaskStatus,
)
from repomesh_runner.contracts import RunnerTask

from .dispatch import DispatchWorkerTask


@dataclass(frozen=True, slots=True)
class WorkerExecutionStarted:
    task: RunnerTask
    status: TaskStatus = TaskStatus.IN_PROGRESS


class WorkerExecutionStartError(ValueError):
    pass


class AssessAssignedWorkerTask:
    """Persist the Worker's execution-readiness judgment before any Runner starts."""

    def __init__(
        self,
        directory: AgentPrincipalReader,
        tasks: TaskReader,
        preflights: WorkerPreflightStore,
        states: TaskExecutionStateGateway,
        reporter: TaskReportGateway | None = None,
    ) -> None:
        self._directory = directory
        self._tasks = tasks
        self._preflights = preflights
        self._states = states
        self._reporter = reporter

    async def execute(self, command: AssessAssignedWorkerTaskCommand) -> WorkerPreflightView:
        principal = await self._directory.get_view(command.worker_agent_id)
        if principal is None or principal.role is not AgentRole.WORKER:
            raise WorkerExecutionStartError("preflight requires a Worker identity")
        task = await self._tasks.get_view(command.task_id)
        if task is None:
            raise WorkerExecutionStartError(f"task not found: {command.task_id}")
        if task.assignee_agent_id != command.worker_agent_id:
            raise WorkerExecutionStartError("worker is not assigned to this task")
        if task.execution_mode is not TaskExecutionMode.GOVERNED_WORKER:
            raise WorkerExecutionStartError("task does not use governed_worker mode")
        notes = command.notes.strip()
        if not notes:
            raise WorkerExecutionStartError("preflight notes are required")
        checks = (
            command.spec_understood,
            command.scope_sufficient,
            command.tests_defined,
            command.dependencies_ready,
        )
        if command.decision is WorkerPreflightDecision.READY and not all(checks):
            raise WorkerExecutionStartError("ready preflight requires all checks to pass")
        current = await self._preflights.get(task.id)
        assessment = WorkerPreflightView(
            task_id=task.id,
            worker_agent_id=command.worker_agent_id,
            decision=command.decision,
            spec_understood=command.spec_understood,
            scope_sufficient=command.scope_sufficient,
            tests_defined=command.tests_defined,
            dependencies_ready=command.dependencies_ready,
            notes=notes,
            revision=(current.revision + 1 if current else 1),
            assessed_at=datetime.now(UTC),
        )
        await self._preflights.save(assessment)
        if command.decision is not WorkerPreflightDecision.READY:
            summary = f"Worker preflight {command.decision.value}: {notes}"
            await self._states.block(task.id, agent_id=command.worker_agent_id, summary=summary)
            if self._reporter is not None:
                await self._reporter.report(
                    ReportTaskCommand(
                        task_id=task.id,
                        reporter_agent_id=command.worker_agent_id,
                        status=TaskStatus.BLOCKED,
                        summary=summary,
                    ),
                    idempotency_key=f"{task.id}:preflight:{assessment.revision}",
                )
        return assessment


class StartWorkerTaskExecution:
    def __init__(
        self,
        states: TaskExecutionStateGateway,
        dispatcher: DispatchWorkerTask,
        reporter: TaskReportGateway | None = None,
    ) -> None:
        self._states = states
        self._dispatcher = dispatcher
        self._reporter = reporter

    async def execute(self, command: DispatchWorkerTaskCommand) -> WorkerExecutionStarted:
        await self._states.start(command.task_id, agent_id=command.worker_agent_id)
        try:
            task = await self._dispatcher.execute(command)
        except Exception as error:
            summary = f"Runner dispatch blocked: {type(error).__name__}: {error}"
            await self._states.block(
                command.task_id,
                agent_id=command.worker_agent_id,
                summary=summary,
            )
            if self._reporter is not None:
                try:
                    await self._reporter.report(
                        ReportTaskCommand(
                            task_id=command.task_id,
                            reporter_agent_id=command.worker_agent_id,
                            status=TaskStatus.BLOCKED,
                            summary=summary,
                        ),
                        idempotency_key=(f"{command.task_id}:dispatch:{command.run_id}:blocked"),
                    )
                except Exception as notification_error:
                    summary += (
                        "; leader notification failed: "
                        f"{type(notification_error).__name__}: {notification_error}"
                    )
            raise WorkerExecutionStartError(summary) from error
        return WorkerExecutionStarted(task)


class StartAssignedWorkerTask:
    """Derive the full immutable execution envelope from only Task and Worker identity."""

    def __init__(
        self,
        directory: AgentPrincipalReader,
        tasks: TaskReader,
        packages: BuildCodingAgentPackage,
        capabilities: ResolveAgentCapabilities,
        repositories: RepositoryCatalog,
        workspaces: GitWorktreeManager,
        bundles: PublishContextBundle,
        execution: StartWorkerTaskExecution,
        states: TaskExecutionStateGateway,
        preflights: WorkerPreflightStore,
        reporter: TaskReportGateway | None = None,
    ) -> None:
        self._directory = directory
        self._tasks = tasks
        self._packages = packages
        self._capabilities = capabilities
        self._repositories = repositories
        self._workspaces = workspaces
        self._bundles = bundles
        self._execution = execution
        self._states = states
        self._preflights = preflights
        self._reporter = reporter

    async def execute(self, command: StartAssignedWorkerTaskCommand) -> WorkerExecutionStarted:
        principal = await self._directory.get_view(command.worker_agent_id)
        if principal is None:
            raise WorkerExecutionStartError("worker identity does not exist")
        if principal.role is not AgentRole.WORKER:
            raise WorkerExecutionStartError(
                "coding execution is restricted to Worker identities"
            )
        task = await self._tasks.get_view(command.task_id)
        if task is None:
            raise WorkerExecutionStartError(f"task not found: {command.task_id}")
        if task.assignee_agent_id != command.worker_agent_id:
            raise WorkerExecutionStartError("worker is not assigned to this task")
        if task.execution_mode is not TaskExecutionMode.GOVERNED_WORKER:
            raise WorkerExecutionStartError("task does not use governed_worker mode")
        if self._preflights is None:
            raise WorkerExecutionStartError("worker preflight store is not configured")
        preflight = await self._preflights.get(task.id)
        if (
            preflight is None
            or preflight.worker_agent_id != command.worker_agent_id
            or preflight.decision is not WorkerPreflightDecision.READY
        ):
            raise WorkerExecutionStartError("worker preflight READY is required before execution")
        run_id = uuid4()
        await self._states.start(task.id, agent_id=command.worker_agent_id)
        try:
            repository = await self._repositories.get(task.repository_id)
            if repository is None:
                raise ValueError(f"repository not found: {task.repository_id}")
            workspace_id = uuid4()
            package = await self._packages.execute(
                BuildCodingAgentPackageCommand(
                    organization_id=task.organization_id,
                    project_id=task.project_id,
                    repository_id=task.repository_id,
                    task_id=task.id,
                    worker_agent_id=command.worker_agent_id,
                )
            )
            capabilities = await self._capabilities.execute(
                command.worker_agent_id, task_features=command.task_features
            )
            workspace = await self._workspaces.prepare(
                repository_id=task.repository_id,
                repository_url=repository.url,
                base_revision=command.base_revision,
                run_id=run_id,
                workspace_id=workspace_id,
            )
            bundle = ContextBundle.create(
                project_id=task.project_id,
                run_id=run_id,
                task_spec_version_id=package.context_files[0].version_id,
                agent_id=command.worker_agent_id,
                role="worker",
                repository_id=task.repository_id,
                base_sha=workspace.base_sha,
                workspace_id=workspace_id,
                items=(),
                allowed_tools=tuple(
                    dict.fromkeys(("read", "edit", "test", *capabilities.tool_allowlist))
                ),
                allowed_paths=package.allowed_paths,
                denied_paths=(".git/**", ".github/workflows/**"),
                network_policy=(),
                expires_at=datetime.now(UTC) + timedelta(hours=4),
            )
            await self._bundles.execute(bundle, permission_layers=())
        except Exception as error:
            summary = f"Execution preparation blocked: {type(error).__name__}: {error}"
            await self._states.block(task.id, agent_id=command.worker_agent_id, summary=summary)
            if self._reporter is not None:
                await self._reporter.report(
                    ReportTaskCommand(
                        task_id=task.id,
                        reporter_agent_id=command.worker_agent_id,
                        status=TaskStatus.BLOCKED,
                        summary=summary,
                    ),
                    idempotency_key=f"{task.id}:prepare:{run_id}:blocked",
                )
            raise WorkerExecutionStartError(summary) from error
        return await self._execution.execute(
            DispatchWorkerTaskCommand(
                organization_id=task.organization_id,
                project_id=task.project_id,
                repository_id=task.repository_id,
                task_id=task.id,
                worker_agent_id=command.worker_agent_id,
                bundle_id=bundle.id,
                run_id=run_id,
                correlation_id=task.id,
                adapter_id=command.adapter_id,
                base_revision=command.base_revision,
                task_features=command.task_features,
            )
        )

    async def execute_direct(
        self, command: StartAssignedWorkerTaskCommand
    ) -> WorkerExecutionStarted:
        principal = await self._directory.get_view(command.worker_agent_id)
        if principal is None or principal.role is not AgentRole.REPOSITORY_LEADER:
            raise WorkerExecutionStartError("direct_run requires a Repository Leader identity")
        task = await self._tasks.get_view(command.task_id)
        if task is None:
            raise WorkerExecutionStartError(f"task not found: {command.task_id}")
        if task.assignee_agent_id != principal.id:
            raise WorkerExecutionStartError("repository leader is not assigned to this task")
        if task.execution_mode is not TaskExecutionMode.DIRECT_RUN:
            raise WorkerExecutionStartError("task does not use direct_run mode")
        return await self._prepare_direct(task, principal, command)

    async def _prepare_direct(
        self, task, principal, command: StartAssignedWorkerTaskCommand
    ) -> WorkerExecutionStarted:
        run_id = uuid4()
        await self._states.start(task.id, agent_id=principal.id)
        try:
            repository = await self._repositories.get(task.repository_id)
            if repository is None:
                raise ValueError(f"repository not found: {task.repository_id}")
            workspace_id = uuid4()
            package = await self._packages.execute(
                BuildCodingAgentPackageCommand(
                    organization_id=task.organization_id,
                    project_id=task.project_id,
                    repository_id=task.repository_id,
                    task_id=task.id,
                    worker_agent_id=principal.id,
                )
            )
            workspace = await self._workspaces.prepare(
                repository_id=task.repository_id,
                repository_url=repository.url,
                base_revision=command.base_revision,
                run_id=run_id,
                workspace_id=workspace_id,
            )
            bundle = ContextBundle.create(
                project_id=task.project_id,
                run_id=run_id,
                task_spec_version_id=package.context_files[0].version_id,
                agent_id=principal.id,
                role="repository_leader_direct",
                repository_id=task.repository_id,
                base_sha=workspace.base_sha,
                workspace_id=workspace_id,
                items=(),
                allowed_tools=("read", "edit", "test"),
                allowed_paths=package.allowed_paths,
                denied_paths=(".git/**", ".github/workflows/**"),
                network_policy=(),
                expires_at=datetime.now(UTC) + timedelta(hours=4),
            )
            await self._bundles.execute(bundle, permission_layers=())
        except Exception as error:
            summary = f"Direct execution preparation blocked: {type(error).__name__}: {error}"
            await self._states.block(task.id, agent_id=principal.id, summary=summary)
            raise WorkerExecutionStartError(summary) from error
        return await self._execution.execute(
            DispatchWorkerTaskCommand(
                organization_id=task.organization_id,
                project_id=task.project_id,
                repository_id=task.repository_id,
                task_id=task.id,
                worker_agent_id=principal.id,
                bundle_id=bundle.id,
                run_id=run_id,
                correlation_id=task.id,
                adapter_id=command.adapter_id,
                base_revision=command.base_revision,
                task_features=command.task_features,
                execution_mode=TaskExecutionMode.DIRECT_RUN.value,
            )
        )
