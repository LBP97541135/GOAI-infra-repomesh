import asyncio
import logging
import socket
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from repomesh.integrations.workspace import GitWorktreeManager
from repomesh.modules.agent_directory.contracts import AgentPrincipalReader, AgentRole
from repomesh.modules.agent_runtime.contracts import (
    DispatchWorkerTaskCommand,
    StartAssignedWorkerTaskCommand,
    WorkerDispatchReader,
    WorkerExecutionReservation,
    WorkerExecutionReservationPort,
)
from repomesh.modules.capability_management import ResolveAgentCapabilities
from repomesh.modules.context.application import PublishContextBundle
from repomesh.modules.context.domain import ContextBundle
from repomesh.modules.repository_intelligence.ports.catalog import RepositoryCatalog
from repomesh.modules.specification import (
    BuildCodingAgentPackage,
    BuildCodingAgentPackageCommand,
)
from repomesh.modules.task_orchestration.assignment import PostgresTaskAssignmentStore
from repomesh.modules.task_orchestration.contracts import (
    ReportTaskCommand,
    TaskExecutionStateGateway,
    TaskReader,
    TaskReportGateway,
    TaskStatus,
)
from repomesh_runner.contracts import RunnerTask
from repomesh_runner.wire import WireError, parse_runner_task

from .dispatch import DispatchWorkerTask

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WorkerExecutionStarted:
    task: RunnerTask
    status: TaskStatus = TaskStatus.IN_PROGRESS


class WorkerExecutionStartError(ValueError):
    pass


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
        reporter: TaskReportGateway | None = None,
        dispatches: WorkerDispatchReader | None = None,
        reservations: WorkerExecutionReservationPort | None = None,
        reservation_lease_seconds: int = 300,
        reservation_wait_seconds: int = 30,
        reservation_owner: str | None = None,
        assignments: PostgresTaskAssignmentStore | None = None,
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
        self._reporter = reporter
        self._dispatches = dispatches
        self._reservations = reservations
        self._reservation_lease_seconds = reservation_lease_seconds
        self._reservation_wait_seconds = reservation_wait_seconds
        self._reservation_owner = reservation_owner or f"{socket.gethostname()}:{uuid4()}"
        self._assignments = assignments

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
        in_flight = await self._in_flight_run(task.id, command.worker_agent_id)
        if in_flight is not None:
            return in_flight
        reservation = None
        assignment = (
            await self._assignments.ensure_initial(task.id)
            if self._assignments is not None
            else None
        )
        if self._reservations is not None:
            try:
                reserved = await self._reservations.reserve(
                    organization_id=task.organization_id,
                    project_id=task.project_id,
                    repository_id=task.repository_id,
                    task_id=task.id,
                    worker_agent_id=command.worker_agent_id,
                    lease_owner=self._reservation_owner,
                    lease_seconds=self._reservation_lease_seconds,
                    assignment_attempt_id=assignment.id if assignment else None,
                    assignment_generation=assignment.generation if assignment else None,
                )
            except Exception as error:
                raise WorkerExecutionStartError(
                    f"worker execution reservation unavailable: {error}"
                ) from error
            reservation = reserved.reservation
            if assignment is not None and reserved.created:
                await self._assignments.bind_execution(
                    task.id,
                    expected_generation=assignment.generation,
                    execution_id=reservation.id,
                )
            if not reserved.created:
                return await self._wait_for_reserved_execution(reservation)
        run_id = reservation.run_id if reservation is not None else uuid4()
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
                # Defect A-21: the grant has to cover the test paths too, or
                # the projector's own guard refuses the very dispatch this is
                # trying to make possible ("package paths exceed the execution
                # grant"). Built fresh on every run — including a re-dispatch —
                # so the catalog's current answer reaches a round materialized
                # long before anyone recorded where its tests live.
                allowed_paths=tuple(
                    dict.fromkeys(
                        (*package.allowed_paths, *(repository.test_paths or ()))
                    )
                ),
                denied_paths=(".git/**", ".github/workflows/**"),
                network_policy=(),
                expires_at=datetime.now(UTC) + timedelta(hours=4),
            )
            await self._bundles.execute(bundle, permission_layers=())
            if reservation is not None and self._reservations is not None:
                await self._reservations.renew(
                    reservation.id,
                    lease_owner=self._reservation_owner,
                    fencing_version=reservation.version,
                    lease_seconds=self._reservation_lease_seconds,
                )
        except Exception as error:
            if reservation is not None:
                await self._fail_reservation(reservation, error)
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
        try:
            started = await self._execution.execute(
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
                resume_session_id=command.resume_session_id,
                assignment_attempt_id=assignment.id if assignment else None,
                assignment_generation=assignment.generation if assignment else None,
                execution_id=reservation.id if reservation else None,
                execution_version=reservation.version if reservation else None,
                )
            )
            if reservation is not None and self._reservations is not None:
                await self._reservations.bind_payload(
                    reservation.id,
                    started.task.to_wire(),
                    lease_owner=self._reservation_owner,
                    fencing_version=reservation.version,
                )
            return started
        except Exception as error:
            if reservation is not None:
                await self._fail_reservation(reservation, error)
            raise

    async def _wait_for_reserved_execution(
        self, reservation: WorkerExecutionReservation
    ) -> WorkerExecutionStarted:
        assert self._reservations is not None
        deadline = asyncio.get_running_loop().time() + self._reservation_wait_seconds
        current = reservation
        while current.task_payload is None:
            if asyncio.get_running_loop().time() >= deadline:
                raise WorkerExecutionStartError(
                    f"worker execution {current.run_id} is still preparing"
                )
            await asyncio.sleep(0.05)
            refreshed = await self._reservations.get_active(current.task_id)
            if refreshed is None:
                raise WorkerExecutionStartError(
                    f"worker execution {current.run_id} preparation did not complete"
                )
            current = refreshed
        try:
            return WorkerExecutionStarted(parse_runner_task(current.task_payload))
        except WireError as error:
            raise WorkerExecutionStartError(
                f"reserved run {current.run_id} carries an unreadable dispatch payload: {error}"
            ) from error

    async def _fail_reservation(
        self, reservation: WorkerExecutionReservation, error: Exception
    ) -> None:
        if self._reservations is None:
            return
        try:
            await self._reservations.fail_preparation(
                reservation.id,
                f"{type(error).__name__}: {error}",
                lease_owner=self._reservation_owner,
                fencing_version=reservation.version,
            )
        except Exception:
            _logger.exception(
                "Could not release failed Worker execution reservation task_id=%s run_id=%s",
                reservation.task_id,
                reservation.run_id,
            )

    async def _in_flight_run(
        self, task_id: UUID, worker_agent_id: UUID
    ) -> WorkerExecutionStarted | None:
        """Return the run the execution plane is already performing for this Task, if any.

        A Worker that triggers the start action several times for one Task must not receive a
        second Worktree, Context Bundle and Runner dispatch. Only non-terminal dispatches are
        reused; once a run has finished, a repeated start is a new attempt.
        """

        if self._dispatches is None:
            return None
        dispatch = await self._dispatches.get_active_dispatch_for_task(
            task_id, worker_agent_id=worker_agent_id
        )
        if dispatch is None:
            return None
        try:
            runner_task = parse_runner_task(dispatch.task_payload)
        except WireError as error:
            raise WorkerExecutionStartError(
                f"in-flight run {dispatch.run_id} carries an unreadable dispatch payload: {error}"
            ) from error
        _logger.info(
            "Reusing in-flight worker run instead of dispatching again: "
            "task_id=%s run_id=%s attempt=%s dispatch_status=%s",
            task_id,
            dispatch.run_id,
            dispatch.attempt,
            dispatch.status,
        )
        return WorkerExecutionStarted(runner_task)
