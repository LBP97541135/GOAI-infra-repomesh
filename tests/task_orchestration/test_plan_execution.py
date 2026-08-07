from uuid import UUID, uuid4

import pytest

from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalStatus,
    AgentPrincipalView,
    AgentRole,
)
from repomesh.modules.project.contracts import (
    ProjectAgentTopologyView,
    ProjectTeamRuntimeStatus,
    RepositoryTeamView,
)
from repomesh.modules.task_orchestration.application import (
    AdvanceExecutionPlan,
    DecomposeRepositoryTask,
)
from repomesh.modules.task_orchestration.contracts import (
    AssignTaskCommand,
    ExecutionPlanStatus,
    TaskStatus,
)
from repomesh.modules.task_orchestration.domain import (
    ExecutionPlan,
    PlannedRepositoryTask,
    Task,
    TaskDenied,
)
from repomesh.modules.task_orchestration.infrastructure import (
    InMemoryExecutionPlanStore,
    InMemoryTaskStore,
)


class FakeAgentDirectory:
    def __init__(self, principals: tuple[AgentPrincipalView, ...]) -> None:
        self._principals = {principal.id: principal for principal in principals}

    async def get_view(self, agent_id: UUID) -> AgentPrincipalView | None:
        return self._principals.get(agent_id)


class FakeProjectTopologies:
    def __init__(self, topology: ProjectAgentTopologyView) -> None:
        self._topology = topology

    async def get_view(self, project_id: UUID) -> ProjectAgentTopologyView | None:
        return self._topology if project_id == self._topology.project_id else None


class RecordingAssigner:
    """Persist assignments like TaskOrchestrator does, without chat delivery."""

    def __init__(self, tasks: InMemoryTaskStore) -> None:
        self._tasks = tasks
        self.commands: list[tuple[AssignTaskCommand, str]] = []

    async def assign(self, command: AssignTaskCommand, *, idempotency_key: str):
        self.commands.append((command, idempotency_key))
        if existing := await self._tasks.get_by_idempotency_key(idempotency_key):
            return existing[0].to_view()
        task = Task(
            organization_id=command.organization_id,
            project_id=command.project_id,
            repository_id=command.repository_id,
            parent_task_id=command.parent_task_id,
            assigned_by_agent_id=command.assigned_by_agent_id,
            assignee_agent_id=command.assignee_agent_id,
            title=command.title,
            instruction=command.instruction,
            acceptance=command.acceptance,
        )
        await self._tasks.add(
            task,
            idempotency_key=idempotency_key,
            request_fingerprint="sha256:test",
        )
        return task.to_view()


class Environment:
    def __init__(self, repository_count: int = 1) -> None:
        self.organization_id = uuid4()
        self.project_id = uuid4()
        self.organization_leader_id = uuid4()
        principals = [
            AgentPrincipalView(
                id=self.organization_leader_id,
                organization_id=self.organization_id,
                role=AgentRole.ORGANIZATION_LEADER,
                leader_agent_id=None,
                repository_id=None,
                responsibility_paths=(),
                agentteams_resource_name="org-leader",
                status=AgentPrincipalStatus.ACTIVE,
            )
        ]
        teams: list[RepositoryTeamView] = []
        self.repository_ids: list[UUID] = []
        self.leader_ids: list[UUID] = []
        self.worker_ids: list[UUID] = []
        for index in range(repository_count):
            repository_id = uuid4()
            leader_id = uuid4()
            worker_id = uuid4()
            self.repository_ids.append(repository_id)
            self.leader_ids.append(leader_id)
            self.worker_ids.append(worker_id)
            principals.append(
                AgentPrincipalView(
                    id=leader_id,
                    organization_id=self.organization_id,
                    role=AgentRole.REPOSITORY_LEADER,
                    leader_agent_id=self.organization_leader_id,
                    repository_id=repository_id,
                    responsibility_paths=(),
                    agentteams_resource_name=f"repo-leader-{index}",
                    status=AgentPrincipalStatus.ACTIVE,
                )
            )
            principals.append(
                AgentPrincipalView(
                    id=worker_id,
                    organization_id=self.organization_id,
                    role=AgentRole.WORKER,
                    leader_agent_id=leader_id,
                    repository_id=repository_id,
                    responsibility_paths=("src/**",),
                    agentteams_resource_name=f"worker-{index}",
                    status=AgentPrincipalStatus.ACTIVE,
                )
            )
            teams.append(
                RepositoryTeamView(
                    id=uuid4(),
                    project_id=self.project_id,
                    repository_id=repository_id,
                    leader_agent_id=leader_id,
                    worker_agent_ids=(worker_id,),
                    agentteams_team_name=f"team-{index}",
                    runtime_status=ProjectTeamRuntimeStatus.READY,
                    room_id=f"!team-{index}:matrix.local",
                    leader_room_id=f"!leader-{index}:matrix.local",
                )
            )
        self.directory = FakeAgentDirectory(tuple(principals))
        self.topologies = FakeProjectTopologies(
            ProjectAgentTopologyView(
                id=uuid4(),
                organization_id=self.organization_id,
                project_id=self.project_id,
                organization_leader_id=self.organization_leader_id,
                repository_teams=tuple(teams),
            )
        )
        self.tasks = InMemoryTaskStore()
        self.plans = InMemoryExecutionPlanStore()
        self.assigner = RecordingAssigner(self.tasks)
        self.decomposer = DecomposeRepositoryTask(
            self.directory, self.topologies, self.tasks, self.assigner
        )
        self.advancer = AdvanceExecutionPlan(
            self.plans, self.tasks, self.assigner, self.decomposer
        )

    def plan(self, batches: tuple[tuple[int, ...], ...]) -> ExecutionPlan:
        return ExecutionPlan(
            organization_id=self.organization_id,
            project_id=self.project_id,
            created_by_agent_id=self.organization_leader_id,
            batches=tuple(
                tuple(
                    PlannedRepositoryTask(
                        repository_id=self.repository_ids[index],
                        title=f"Deliver repository {index}",
                        instruction=f"Implement the approved scope for repository {index}.",
                        acceptance=("Tests pass",),
                    )
                    for index in batch
                )
                for batch in batches
            ),
        )

    async def assign_repository_task(self, index: int = 0, *, key: str = "repository-task"):
        return await self.assigner.assign(
            AssignTaskCommand(
                organization_id=self.organization_id,
                project_id=self.project_id,
                repository_id=self.repository_ids[index],
                assigned_by_agent_id=self.organization_leader_id,
                assignee_agent_id=self.leader_ids[index],
                title="Deliver pricing",
                instruction="Own the repository-level pricing change.",
                acceptance=("Integration tests pass",),
            ),
            idempotency_key=key,
        )

    async def finish(self, task_id: UUID, status: TaskStatus, summary: str) -> None:
        task = await self.tasks.get(task_id)
        assert task is not None
        await self.tasks.update(task.report(status, summary), expected_version=task.version)

    async def worker_task_of(self, leader_task_id: UUID) -> Task:
        children = await self.tasks.list_by_parent(leader_task_id)
        assert len(children) == 1
        return children[0]

    async def leader_task_id(self, plan_id: UUID, batch_index: int, position: int) -> UUID:
        plan = await self.plans.get(plan_id)
        assert plan is not None
        task_id = plan.batches[batch_index][position].leader_task_id
        assert task_id is not None
        return task_id


@pytest.mark.asyncio
async def test_decompose_assigns_one_worker_task_under_the_repository_task() -> None:
    environment = Environment()
    repository_task = await environment.assign_repository_task()

    worker_tasks = await environment.decomposer.execute(
        repository_task.id, idempotency_key="decompose"
    )

    assert len(worker_tasks) == 1
    worker_task = worker_tasks[0]
    assert worker_task.parent_task_id == repository_task.id
    assert worker_task.assigned_by_agent_id == environment.leader_ids[0]
    assert worker_task.assignee_agent_id == environment.worker_ids[0]
    assert worker_task.title == repository_task.title
    assert worker_task.instruction == repository_task.instruction
    assert worker_task.acceptance == repository_task.acceptance
    assert environment.assigner.commands[-1][1] == (
        f"decompose:worker:{environment.worker_ids[0]}"
    )


@pytest.mark.asyncio
async def test_decompose_reuses_an_in_flight_worker_task() -> None:
    environment = Environment()
    repository_task = await environment.assign_repository_task()
    first = await environment.decomposer.execute(
        repository_task.id, idempotency_key="decompose-1"
    )
    assignments_after_first = len(environment.assigner.commands)

    second = await environment.decomposer.execute(
        repository_task.id, idempotency_key="decompose-2"
    )

    assert second == first
    assert len(environment.assigner.commands) == assignments_after_first


@pytest.mark.asyncio
async def test_decompose_rejects_a_task_that_is_not_owned_by_a_repository_leader() -> None:
    environment = Environment()
    repository_task = await environment.assign_repository_task()
    worker_tasks = await environment.decomposer.execute(
        repository_task.id, idempotency_key="decompose"
    )

    with pytest.raises(TaskDenied, match="repository leader task"):
        await environment.decomposer.execute(
            worker_tasks[0].id, idempotency_key="decompose-worker-task"
        )


@pytest.mark.asyncio
async def test_start_assigns_and_decomposes_the_first_batch() -> None:
    environment = Environment()
    plan = environment.plan(((0,),))

    view = await environment.advancer.start(plan, idempotency_key="plan-start")

    assert view.status is ExecutionPlanStatus.IN_PROGRESS
    leader_task_id = view.batches[0][0].leader_task_id
    assert leader_task_id is not None
    leader_task = await environment.tasks.get(leader_task_id)
    assert leader_task is not None
    assert leader_task.assignee_agent_id == environment.leader_ids[0]
    assert leader_task.assigned_by_agent_id == environment.organization_leader_id
    worker_task = await environment.worker_task_of(leader_task_id)
    assert worker_task.assignee_agent_id == environment.worker_ids[0]
    keys = [key for _, key in environment.assigner.commands]
    assert keys[0] == f"plan-start:b0:{environment.repository_ids[0]}"
    assert keys[1] == (
        f"plan-start:b0:{environment.repository_ids[0]}:decompose"
        f":worker:{environment.worker_ids[0]}"
    )


@pytest.mark.asyncio
async def test_start_is_idempotent() -> None:
    environment = Environment()
    plan = environment.plan(((0,),))

    first = await environment.advancer.start(plan, idempotency_key="plan-start")
    assignments = len(environment.assigner.commands)
    replay = await environment.advancer.start(plan, idempotency_key="plan-start")

    assert replay == first
    assert len(environment.assigner.commands) == assignments


@pytest.mark.asyncio
async def test_worker_success_completes_the_repository_task_and_the_plan() -> None:
    environment = Environment()
    plan = environment.plan(((0,),))
    await environment.advancer.start(plan, idempotency_key="plan-start")
    leader_task_id = await environment.leader_task_id(plan.id, 0, 0)
    worker_task = await environment.worker_task_of(leader_task_id)
    await environment.finish(worker_task.id, TaskStatus.SUCCEEDED, "Pricing implemented.")

    await environment.advancer.on_task_terminal(worker_task.id)

    leader_task = await environment.tasks.get(leader_task_id)
    stored_plan = await environment.plans.get(plan.id)
    assert leader_task is not None and leader_task.status is TaskStatus.SUCCEEDED
    assert stored_plan is not None and stored_plan.status is ExecutionPlanStatus.COMPLETED

    # Replaying the same terminal task must not change anything.
    await environment.advancer.on_task_terminal(worker_task.id)
    assert (await environment.plans.get(plan.id)) == stored_plan


@pytest.mark.asyncio
async def test_worker_failure_fails_the_plan_and_stops_the_next_batch() -> None:
    environment = Environment(repository_count=2)
    plan = environment.plan(((0,), (1,)))
    await environment.advancer.start(plan, idempotency_key="plan-start")
    leader_task_id = await environment.leader_task_id(plan.id, 0, 0)
    worker_task = await environment.worker_task_of(leader_task_id)
    await environment.finish(worker_task.id, TaskStatus.FAILED, "Verification failed.")

    await environment.advancer.on_task_terminal(worker_task.id)

    leader_task = await environment.tasks.get(leader_task_id)
    stored_plan = await environment.plans.get(plan.id)
    assert leader_task is not None and leader_task.status is TaskStatus.FAILED
    assert stored_plan is not None and stored_plan.status is ExecutionPlanStatus.FAILED
    assert stored_plan.current_batch_index == 0
    assert all(
        command.repository_id != environment.repository_ids[1]
        for command, _ in environment.assigner.commands
    )


@pytest.mark.asyncio
async def test_next_batch_starts_only_after_every_leader_task_of_the_batch_succeeds() -> None:
    environment = Environment(repository_count=3)
    plan = environment.plan(((0, 1), (2,)))
    await environment.advancer.start(plan, idempotency_key="plan-start")
    first_leader_task_id = await environment.leader_task_id(plan.id, 0, 0)
    second_leader_task_id = await environment.leader_task_id(plan.id, 0, 1)

    first_worker = await environment.worker_task_of(first_leader_task_id)
    await environment.finish(first_worker.id, TaskStatus.SUCCEEDED, "Repository 0 done.")
    await environment.advancer.on_task_terminal(first_worker.id)

    waiting = await environment.plans.get(plan.id)
    assert waiting is not None and waiting.current_batch_index == 0
    assert all(
        command.repository_id != environment.repository_ids[2]
        for command, _ in environment.assigner.commands
    )

    second_worker = await environment.worker_task_of(second_leader_task_id)
    await environment.finish(second_worker.id, TaskStatus.SUCCEEDED, "Repository 1 done.")
    await environment.advancer.on_task_terminal(second_worker.id)

    advanced = await environment.plans.get(plan.id)
    assert advanced is not None
    assert advanced.current_batch_index == 1
    assert advanced.status is ExecutionPlanStatus.IN_PROGRESS
    next_leader_task_id = advanced.batches[1][0].leader_task_id
    assert next_leader_task_id is not None
    next_worker = await environment.worker_task_of(next_leader_task_id)
    assert next_worker.assignee_agent_id == environment.worker_ids[2]
    keys = [key for _, key in environment.assigner.commands]
    assert f"{plan.id}:b1:{environment.repository_ids[2]}" in keys


@pytest.mark.asyncio
async def test_terminal_task_outside_any_plan_is_ignored() -> None:
    environment = Environment()
    repository_task = await environment.assign_repository_task()
    await environment.finish(repository_task.id, TaskStatus.SUCCEEDED, "Delivered.")

    await environment.advancer.on_task_terminal(repository_task.id)
    await environment.advancer.on_task_terminal(uuid4())

    assert environment.plans.plans == {}
