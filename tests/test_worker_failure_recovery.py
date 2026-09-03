from dataclasses import replace
from uuid import uuid4

import pytest

from repomesh.integrations.runner.recovery import WorkerRecoveryCoordinator
from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalStatus,
    AgentPrincipalView,
    AgentRole,
)
from repomesh.modules.agent_runtime.recovery import (
    PostgresWorkerRecoveryStore,
    WorkerRecoveryCandidate,
    WorkerRecoveryDecision,
    WorkerRecoveryOperation,
    WorkerRecoveryState,
    select_replacement_worker,
)
from repomesh.modules.project.contracts import (
    ProjectAgentTopologyView,
    ProjectExecutionMode,
    ProjectOperationalStatus,
    ProjectTeamRuntimeStatus,
    RepositoryTeamView,
)
from repomesh.modules.task_orchestration.assignment import (
    AssignmentReason,
    PostgresTaskAssignmentStore,
)
from repomesh.modules.task_orchestration.domain import Task
from repomesh.modules.task_orchestration.infrastructure import PostgresTaskStore
from repomesh.persistence import Database
from repomesh.persistence.base import ALL_SCHEMAS


@pytest.fixture
async def recovery_database(tmp_path):
    database = Database(
        f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}",
        schema_translate_map={schema: None for schema in ALL_SCHEMAS},
    )
    await database.create_all_for_tests()
    try:
        yield database
    finally:
        await database.dispose()


async def _task(database, *, worker_id=None):
    task = Task(
        organization_id=uuid4(), project_id=uuid4(), repository_id=uuid4(),
        assigned_by_agent_id=uuid4(), assignee_agent_id=worker_id or uuid4(),
        title="Recover checkout", instruction="Repair checkout", acceptance=("tests pass",),
    )
    store = PostgresTaskStore(database)
    await store.add(task, idempotency_key=f"recovery:{task.id}", request_fingerprint="x" * 71)
    return task, store


@pytest.mark.asyncio
async def test_assignment_history_and_atomic_reassignment(recovery_database) -> None:
    task, tasks = await _task(recovery_database)
    assignments = PostgresTaskAssignmentStore(recovery_database)
    initial = await assignments.ensure_initial(task.id)
    replacement_id = uuid4()

    replacement = await assignments.reassign(
        task.id,
        expected_task_version=task.version,
        expected_generation=initial.generation,
        replacement_worker_id=replacement_id,
        reason=AssignmentReason.WORKER_UNREACHABLE,
    )

    assert replacement.generation == 2
    assert replacement.previous_attempt_id == initial.id
    assert (await tasks.get(task.id)).assignee_agent_id == replacement_id
    history = await assignments.history(task.id)
    assert [item.state.value for item in history] == ["superseded", "active"]


@pytest.mark.asyncio
async def test_ensure_initial_after_rerun_redispatch_opens_next_generation(
    recovery_database,
) -> None:
    task, _ = await _task(recovery_database)
    assignments = PostgresTaskAssignmentStore(recovery_database)
    initial = await assignments.ensure_initial(task.id)
    await assignments.complete_current(task.id, initial.id, initial.generation)

    reopened = await assignments.ensure_initial(task.id)

    assert reopened.id != initial.id
    assert reopened.generation == initial.generation + 1
    assert reopened.previous_attempt_id == initial.id
    assert reopened.state.value == "active"
    assert reopened.worker_agent_id == task.assignee_agent_id
    assert reopened.reason is AssignmentReason.OPERATOR
    history = await assignments.history(task.id)
    assert [item.state.value for item in history] == ["completed", "active"]
    converged = await assignments.ensure_initial(task.id)
    assert converged.id == reopened.id


@pytest.mark.asyncio
async def test_two_recovery_claimers_have_one_winner(recovery_database) -> None:
    store = PostgresWorkerRecoveryStore(recovery_database)
    operation = await store.ensure(
        execution_id=uuid4(), task_id=uuid4(), assignment_attempt_id=uuid4(),
        assignment_generation=1, failed_worker_id=uuid4(), reason="interrupted",
        native_session_id="session-safe",
    )
    winner = await store.claim("reconciler-a")
    assert winner is not None and winner.id == operation.id
    assert await store.claim("reconciler-b") is None


def test_replacement_selection_is_deterministic() -> None:
    failed = uuid4()
    workers = [
        WorkerRecoveryCandidate(uuid4(), active_executions=0, recent_failures=2),
        WorkerRecoveryCandidate(uuid4(), active_executions=0, recent_failures=0),
        WorkerRecoveryCandidate(failed, active_executions=0, recent_failures=0),
    ]
    selected = select_replacement_worker(workers, failed_worker_id=failed)
    assert selected is not None and selected.recent_failures == 0


class _Tasks:
    def __init__(self, task): self.task = task
    async def get(self, task_id): return self.task if task_id == self.task.id else None


class _Assignments:
    def __init__(self, assignment):
        self.assignment = assignment
        self.reassigned = None
    async def active(self, task_id): return self.assignment
    async def reopen_same_assignment(self, *args, **kwargs): return self.assignment
    async def reassign(self, task_id, **kwargs):
        self.reassigned = kwargs["replacement_worker_id"]
        self.assignment = replace(
            self.assignment,
            id=uuid4(), worker_agent_id=self.reassigned,
            generation=self.assignment.generation + 1,
            previous_attempt_id=self.assignment.id,
        )
        return self.assignment


class _Directory:
    def __init__(self, principals): self.principals = {item.id: item for item in principals}
    async def get_view(self, agent_id): return self.principals.get(agent_id)


class _Topology:
    def __init__(self, topology): self.topology = topology
    async def get_view(self, project_id): return self.topology


class _Reservations:
    async def worker_busy(self, worker_id): return False


class _Health:
    def __init__(self, healthy): self.ids = set(healthy)
    async def healthy(self, worker_id): return worker_id in self.ids


def _operation(task, assignment, *, session=None):
    return WorkerRecoveryOperation(
        id=uuid4(), execution_id=uuid4(), task_id=task.id,
        assignment_attempt_id=assignment.id, assignment_generation=assignment.generation,
        failed_worker_id=assignment.worker_agent_id, state=WorkerRecoveryState.RUNNING,
        reason="interrupted", native_session_id=session, attempts=1,
        lease_owner="reconciler", decision=None,
    )


@pytest.mark.asyncio
async def test_recovery_prefers_resume_then_reassigns_when_worker_is_unhealthy(
    recovery_database,
) -> None:
    failed = uuid4()
    replacement_id = uuid4()
    task, _ = await _task(recovery_database, worker_id=failed)
    assignment = await PostgresTaskAssignmentStore(recovery_database).ensure_initial(task.id)
    principals = [
        AgentPrincipalView(
            id=worker_id, organization_id=task.organization_id, role=AgentRole.WORKER,
            leader_agent_id=task.assigned_by_agent_id, repository_id=task.repository_id,
            responsibility_paths=("src/**",), agentteams_resource_name=f"worker-{worker_id}",
            status=AgentPrincipalStatus.ACTIVE,
        )
        for worker_id in (failed, replacement_id)
    ]
    topology = ProjectAgentTopologyView(
        id=uuid4(), organization_id=task.organization_id, project_id=task.project_id,
        organization_leader_id=task.assigned_by_agent_id,
        repository_teams=(RepositoryTeamView(
            id=uuid4(), project_id=task.project_id, repository_id=task.repository_id,
            leader_agent_id=task.assigned_by_agent_id,
            worker_agent_ids=(failed, replacement_id), agentteams_team_name="team",
            runtime_status=ProjectTeamRuntimeStatus.READY, room_id=None, leader_room_id=None,
        ),), execution_mode=ProjectExecutionMode.AUTO,
        operational_status=ProjectOperationalStatus.ACTIVE,
    )
    resumed = []
    started = []
    escalated = []
    assignments = _Assignments(assignment)

    coordinator = WorkerRecoveryCoordinator(
        _Tasks(task), assignments, _Directory(principals), _Topology(topology),
        _Reservations(), _Health({failed, replacement_id}),
        resume=lambda op: _append(resumed, op),
        start_replacement=lambda task_id, worker_id: _append(started, (task_id, worker_id)),
        escalate=lambda op, reason: _append(escalated, reason),
    )
    assert await coordinator.decide(_operation(task, assignment, session="session")) is (
        WorkerRecoveryDecision.RESUME
    )
    assert len(resumed) == 1

    coordinator._health = _Health({replacement_id})
    assert await coordinator.decide(_operation(task, assignment)) is (
        WorkerRecoveryDecision.REASSIGN
    )
    assert assignments.reassigned == replacement_id
    assert started == [(task.id, replacement_id)]
    assert escalated == []


async def _append(target, value):
    target.append(value)
