from collections.abc import Awaitable, Callable
from typing import Protocol
from uuid import UUID

from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalReader,
    AgentPrincipalStatus,
    AgentRole,
)
from repomesh.modules.agent_runtime.execution_reservation import (
    PostgresWorkerExecutionReservationStore,
)
from repomesh.modules.agent_runtime.recovery import (
    WorkerRecoveryCandidate,
    WorkerRecoveryDecision,
    WorkerRecoveryOperation,
    select_replacement_worker,
)
from repomesh.modules.project.contracts import ProjectTopologyReader
from repomesh.modules.task_orchestration.assignment import (
    AssignmentReason,
    PostgresTaskAssignmentStore,
)
from repomesh.modules.task_orchestration.ports import TaskStore


class WorkerHealthReader(Protocol):
    async def healthy(self, worker_agent_id: UUID) -> bool: ...


class WorkerRecoveryCoordinator:
    def __init__(
        self,
        tasks: TaskStore,
        assignments: PostgresTaskAssignmentStore,
        directory: AgentPrincipalReader,
        topologies: ProjectTopologyReader,
        reservations: PostgresWorkerExecutionReservationStore,
        health: WorkerHealthReader,
        *,
        resume: Callable[[WorkerRecoveryOperation], Awaitable[None]],
        start_replacement: Callable[[UUID, UUID], Awaitable[None]],
        escalate: Callable[[WorkerRecoveryOperation, str], Awaitable[None]],
        recent_failures: Callable[[UUID], Awaitable[int]] | None = None,
        max_reassignments: int = 2,
    ) -> None:
        self._tasks = tasks
        self._assignments = assignments
        self._directory = directory
        self._topologies = topologies
        self._reservations = reservations
        self._health = health
        self._resume = resume
        self._start_replacement = start_replacement
        self._escalate = escalate
        self._recent_failures = recent_failures
        self._max_reassignments = max_reassignments

    async def decide(self, operation: WorkerRecoveryOperation) -> WorkerRecoveryDecision:
        task = await self._tasks.get(operation.task_id)
        assignment = await self._assignments.active(operation.task_id)
        if task is not None and assignment is None:
            assignment = await self._assignments.ensure_initial(operation.task_id)
        if task is None or assignment is None:
            await self._escalate(operation, "task_or_assignment_missing")
            return WorkerRecoveryDecision.ESCALATE
        if (
            operation.assignment_generation is not None
            and assignment.generation != operation.assignment_generation
        ):
            return WorkerRecoveryDecision.NO_ACTION
        same_worker_healthy = await self._health.healthy(operation.failed_worker_id)
        if operation.decision is WorkerRecoveryDecision.RESUME and not (
            operation.native_session_id and same_worker_healthy
        ):
            await self._escalate(operation, "requested_session_resume_is_unavailable")
            return WorkerRecoveryDecision.ESCALATE
        if (
            operation.decision is not WorkerRecoveryDecision.REASSIGN
            and operation.native_session_id
            and same_worker_healthy
        ):
            if operation.reason == "input_required":
                await self._escalate(operation, "runner_input_required")
                return WorkerRecoveryDecision.ESCALATE
            await self._assignments.reopen_same_assignment(
                task.id,
                expected_task_version=task.version,
                expected_generation=assignment.generation,
            )
            await self._resume(operation)
            return WorkerRecoveryDecision.RESUME
        if assignment.generation - 1 >= self._max_reassignments:
            await self._escalate(operation, "reassignment_budget_exhausted")
            return WorkerRecoveryDecision.ESCALATE

        topology = await self._topologies.get_view(task.project_id)
        if topology is None:
            await self._escalate(operation, "project_topology_missing")
            return WorkerRecoveryDecision.ESCALATE
        team = next(
            (
                item
                for item in topology.repository_teams
                if item.repository_id == task.repository_id
            ),
            None,
        )
        if team is None:
            await self._escalate(operation, "repository_team_missing")
            return WorkerRecoveryDecision.ESCALATE

        candidates: list[WorkerRecoveryCandidate] = []
        for worker_id in team.worker_agent_ids:
            principal = await self._directory.get_view(worker_id)
            if (
                principal is None
                or principal.role is not AgentRole.WORKER
                or principal.status is not AgentPrincipalStatus.ACTIVE
                or not await self._health.healthy(worker_id)
                or await self._reservations.worker_busy(worker_id)
            ):
                continue
            failures = (
                await self._recent_failures(worker_id)
                if self._recent_failures is not None
                else 0
            )
            candidates.append(WorkerRecoveryCandidate(worker_id, 0, failures))
        replacement = select_replacement_worker(
            candidates, failed_worker_id=operation.failed_worker_id
        )
        if replacement is None:
            await self._escalate(operation, "no_healthy_replacement_worker")
            return WorkerRecoveryDecision.ESCALATE

        reassigned = await self._assignments.reassign(
            task.id,
            expected_task_version=task.version,
            expected_generation=assignment.generation,
            replacement_worker_id=replacement.worker_id,
            reason=(
                AssignmentReason.RUNNER_INTERRUPTED
                if operation.reason == "interrupted"
                else AssignmentReason.WORKER_UNREACHABLE
            ),
        )
        await self._start_replacement(task.id, reassigned.worker_agent_id)
        return WorkerRecoveryDecision.REASSIGN
