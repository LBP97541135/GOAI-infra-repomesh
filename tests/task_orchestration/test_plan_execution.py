from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalStatus,
    AgentPrincipalView,
    AgentRole,
)
from repomesh.modules.collaboration.contracts import CollaborationRouteUnavailable
from repomesh.modules.project.contracts import (
    ProjectAgentTopologyView,
    ProjectTeamRuntimeStatus,
    RepositoryTeamView,
)
from repomesh.modules.task_orchestration.application import (
    AdvanceExecutionPlan,
    DecomposeRepositoryTask,
    ObserveExecutionPlan,
)
from repomesh.modules.task_orchestration.contracts import (
    AssignTaskCommand,
    BatchDeliveryRefused,
    DeliveryGatedRepositoryView,
    DeliveryStatePort,
    ExecutionPlanStatus,
    TaskOrigin,
    TaskPublicationUnavailable,
    TaskStatus,
    TaskView,
)
from repomesh.modules.task_orchestration.domain import (
    ExecutionPlan,
    PlannedRepositoryTask,
    Task,
    TaskConflict,
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
        #: Keys whose delivery fails once, the way a repository team with no
        #: Matrix room does: the task row is written, the assignment message is
        #: not, and the caller sees the refusal. Empty unless a test says
        #: otherwise, so every other test keeps its old behaviour.
        self.undeliverable: set[str] = set()
        #: The same position in the sequence, a different refusal: the task row
        #: is written and the *package* never reaches the store (defect A-10).
        #: Kept apart from ``undeliverable`` so a test says which of the two
        #: halves of delivery it is breaking.
        self.unpublishable: set[str] = set()
        #: Every key this assigner was asked to deliver, in order — including
        #: the replays that found an existing row. Delivery is what A-10 broke,
        #: so it has to be observable separately from row creation.
        self.delivered: list[str] = []

    async def assign(
        self,
        command: AssignTaskCommand,
        *,
        idempotency_key: str,
        origin: TaskOrigin = TaskOrigin.PLANNED,
    ):
        self.commands.append((command, idempotency_key))
        if existing := await self._tasks.get_by_idempotency_key(idempotency_key):
            self._deliver(idempotency_key)
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
            origin=origin,
        )
        await self._tasks.add(
            task,
            idempotency_key=idempotency_key,
            request_fingerprint="sha256:test",
        )
        self._deliver(idempotency_key)
        return task.to_view()

    def _deliver(self, idempotency_key: str) -> None:
        if idempotency_key in self.unpublishable:
            self.unpublishable.discard(idempotency_key)
            raise TaskPublicationUnavailable(
                "S3 operation failed; code: InvalidAccessKeyId, message: The "
                "Access Key Id you provided does not exist in our records."
            )
        self.delivered.append(idempotency_key)
        if idempotency_key in self.undeliverable:
            self.undeliverable.discard(idempotency_key)
            raise CollaborationRouteUnavailable("AgentTeams room is not ready")


@dataclass(frozen=True)
class SpecificationCall:
    task: TaskView
    allowed_paths: tuple[str, ...]
    tests: tuple[str, ...]
    idempotency_key: str


class RecordingSpecificationAuthor:
    """Stand in for the Specification module while recording the execution permits."""

    def __init__(self) -> None:
        self.calls: list[SpecificationCall] = []

    async def ensure_approved(
        self,
        task: TaskView,
        *,
        allowed_paths: tuple[str, ...],
        tests: tuple[str, ...],
        idempotency_key: str,
    ) -> None:
        self.calls.append(
            SpecificationCall(
                task=task,
                allowed_paths=allowed_paths,
                tests=tests,
                idempotency_key=idempotency_key,
            )
        )


class FakeDeliveryState:
    """Mutable delivery gate: merged flags per repository of the project."""

    def __init__(self, merged: dict[UUID, bool] | None = None) -> None:
        self.merged: dict[UUID, bool] = dict(merged or {})

    async def repository_states(self, project_id: UUID) -> tuple[DeliveryGatedRepositoryView, ...]:
        return tuple(
            DeliveryGatedRepositoryView(repository_id=repository_id, merged=merged)
            for repository_id, merged in self.merged.items()
        )


class Environment:
    def __init__(
        self,
        repository_count: int = 1,
        *,
        with_spec_author: bool = True,
        worker_paths: tuple[str, ...] = ("src/**",),
        delivery_state: DeliveryStatePort | None = None,
        on_batch_deliver=None,
    ) -> None:
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
                    responsibility_paths=worker_paths,
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
        self.spec_author = RecordingSpecificationAuthor() if with_spec_author else None
        self.decomposer = DecomposeRepositoryTask(
            self.directory, self.topologies, self.tasks, self.assigner, self.spec_author
        )
        self.advancer = AdvanceExecutionPlan(
            self.plans,
            self.tasks,
            self.assigner,
            self.decomposer,
            delivery_state=delivery_state,
            on_batch_deliver=on_batch_deliver,
        )

    @property
    def recorded_specifications(self) -> list[SpecificationCall]:
        assert self.spec_author is not None
        return self.spec_author.calls

    def plan(
        self,
        batches: tuple[tuple[int, ...], ...],
        *,
        tests: dict[int, tuple[str, ...]] | None = None,
    ) -> ExecutionPlan:
        verification = tests or {}
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
                        tests=verification.get(index, ()),
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
    assert environment.assigner.commands[-1][1] == (f"decompose:worker:{environment.worker_ids[0]}")


@pytest.mark.asyncio
async def test_decompose_reuses_an_in_flight_worker_task() -> None:
    environment = Environment()
    repository_task = await environment.assign_repository_task()
    first = await environment.decomposer.execute(repository_task.id, idempotency_key="decompose-1")
    assignments_after_first = len(environment.assigner.commands)

    second = await environment.decomposer.execute(repository_task.id, idempotency_key="decompose-2")

    assert second == first
    assert len(environment.assigner.commands) == assignments_after_first


@pytest.mark.asyncio
async def test_decompose_authors_the_execution_permit_of_the_new_worker_task() -> None:
    environment = Environment()
    repository_task = await environment.assign_repository_task()

    worker_tasks = await environment.decomposer.execute(
        repository_task.id,
        idempotency_key="decompose",
        tests=("uv run pytest -q tests/checkout",),
    )

    assert len(environment.recorded_specifications) == 1
    call = environment.recorded_specifications[0]
    assert call.task == worker_tasks[0]
    assert call.allowed_paths == ("src/**",)
    assert call.tests == ("uv run pytest -q tests/checkout",)
    assert call.idempotency_key == f"decompose:spec:{environment.worker_ids[0]}"


@pytest.mark.asyncio
async def test_decompose_replay_heals_the_permit_of_an_in_flight_worker_task() -> None:
    environment = Environment()
    repository_task = await environment.assign_repository_task()
    first = await environment.decomposer.execute(
        repository_task.id, idempotency_key="decompose-1", tests=("uv run pytest -q",)
    )
    assignments_after_first = len(environment.assigner.commands)

    second = await environment.decomposer.execute(
        repository_task.id, idempotency_key="decompose-2", tests=("uv run pytest -q",)
    )

    assert second == first
    assert len(environment.assigner.commands) == assignments_after_first
    assert len(environment.recorded_specifications) == 2
    healed = environment.recorded_specifications[1]
    assert healed.task == first[0]
    assert healed.tests == ("uv run pytest -q",)
    assert healed.idempotency_key == f"decompose-2:spec:{environment.worker_ids[0]}"


@pytest.mark.asyncio
async def test_decompose_without_a_specification_author_keeps_assigning() -> None:
    environment = Environment(with_spec_author=False)
    repository_task = await environment.assign_repository_task()

    worker_tasks = await environment.decomposer.execute(
        repository_task.id, idempotency_key="decompose"
    )

    assert len(worker_tasks) == 1
    assert worker_tasks[0].assignee_agent_id == environment.worker_ids[0]


@pytest.mark.asyncio
async def test_a_worker_without_responsibility_paths_may_touch_the_whole_repository() -> None:
    environment = Environment(worker_paths=())
    repository_task = await environment.assign_repository_task()

    await environment.decomposer.execute(repository_task.id, idempotency_key="decompose")

    assert environment.recorded_specifications[0].allowed_paths == ("**",)


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
    """A replay creates nothing twice — asserted over the rows, not the calls.

    This used to assert that a replay made no further ``assign`` calls, and
    that assertion is what let defect A-10 through: it made "did not try" the
    definition of idempotent, when the property that matters is "did not
    duplicate". A replay now *does* re-drive the batch, deliberately — that is
    how a round whose task package upload was refused ever gets published — and
    every write it drives is keyed, so the second pass finds the first pass's
    rows instead of writing new ones. Counting attempts cannot tell those two
    apart; counting tasks can.
    """

    environment = Environment()
    plan = environment.plan(((0,),))

    first = await environment.advancer.start(plan, idempotency_key="plan-start")
    tasks_after_first = await environment.tasks.list_by_project(environment.project_id)

    replay = await environment.advancer.start(plan, idempotency_key="plan-start")

    assert replay == first
    tasks_after_replay = await environment.tasks.list_by_project(environment.project_id)
    assert {task.id for task in tasks_after_replay} == {task.id for task in tasks_after_first}
    # One repository task and the one Worker task under it, both times.
    assert len(tasks_after_replay) == 2
    # And the replay re-drove exactly the keys the first pass used, which is
    # what makes the re-drive a lookup rather than a second round.
    keys = [key for _, key in environment.assigner.commands]
    assert keys[2:] == keys[:2]


@pytest.mark.asyncio
async def test_start_finishes_a_batch_the_execution_plane_refused() -> None:
    """A replay repairs the plan the first attempt stranded.

    ``start`` is two writes — the plan row, then the batch's assignments — and
    only the second one talks to the execution plane, so it is the one that
    fails. Recognising the key and handing the row back, which is what a replay
    used to do, reported success for a round that had assigned nobody: the
    console showed a materialised issue with no tasks and no rooms, and the
    operator had no move left. Found live by the final acceptance walk
    (2026-08-12).
    """

    environment = Environment(repository_count=2)
    plan = environment.plan(((0, 1),))
    environment.assigner.undeliverable = {
        f"plan-start:b0:{environment.repository_ids[0]}"
    }

    with pytest.raises(CollaborationRouteUnavailable):
        await environment.advancer.start(plan, idempotency_key="plan-start")
    stranded = await environment.plans.get(plan.id)
    assert stranded is not None
    assert stranded.leader_task_ids(0) == ()

    view = await environment.advancer.start(plan, idempotency_key="plan-start")

    assert view.status is ExecutionPlanStatus.IN_PROGRESS
    assert all(planned.leader_task_id is not None for planned in view.batches[0])
    for position in range(2):
        leader_task_id = await environment.leader_task_id(plan.id, 0, position)
        worker_task = await environment.worker_task_of(leader_task_id)
        assert worker_task.assignee_agent_id == environment.worker_ids[position]


@pytest.mark.asyncio
async def test_resuming_a_batch_reuses_the_repositories_that_got_through() -> None:
    """Only the assignments that never happened are made on the replay."""

    environment = Environment(repository_count=2)
    plan = environment.plan(((0, 1),))
    environment.assigner.undeliverable = {
        f"plan-start:b0:{environment.repository_ids[1]}"
    }

    with pytest.raises(CollaborationRouteUnavailable):
        await environment.advancer.start(plan, idempotency_key="plan-start")
    survivor = await environment.tasks.get_by_idempotency_key(
        f"plan-start:b0:{environment.repository_ids[0]}"
    )
    assert survivor is not None

    await environment.advancer.start(plan, idempotency_key="plan-start")

    assert await environment.leader_task_id(plan.id, 0, 0) == survivor[0].id
    leader_tasks = [
        task
        for task in await environment.tasks.list_by_project(environment.project_id)
        if task.parent_task_id is None
    ]
    assert len(leader_tasks) == 2


@pytest.mark.asyncio
async def test_a_batch_whose_package_upload_failed_is_finished_by_the_replay() -> None:
    """Defect A-10, the acceptance criterion: the next press completes the round.

    This refusal lands further down the chain than the room one above, and that
    is the whole difficulty. ``_assign_batch`` writes the leader tasks, records
    them on the plan, and only *then* decomposes each into the Worker task whose
    package is uploaded — so a refused upload leaves a plan whose batch is fully
    assigned, and the replay's old "is every leader task assigned?" test said
    yes and did nothing at all. Live, that was a materialize answering 200 over
    an empty bucket, with no move left for the operator.
    """

    environment = Environment()
    plan = environment.plan(((0,),))
    worker_key = (
        f"plan-start:b0:{environment.repository_ids[0]}:decompose"
        f":worker:{environment.worker_ids[0]}"
    )
    environment.assigner.unpublishable = {worker_key}

    with pytest.raises(TaskPublicationUnavailable):
        await environment.advancer.start(plan, idempotency_key="plan-start")

    # The state the failure leaves: the batch *looks* finished, and the Worker
    # task exists with nothing published for it.
    stranded = await environment.plans.get(plan.id)
    assert stranded is not None
    assert len(stranded.leader_task_ids(0)) == len(stranded.batches[0])
    leader_task_id = await environment.leader_task_id(plan.id, 0, 0)
    worker_task = await environment.worker_task_of(leader_task_id)
    assert worker_key not in environment.assigner.delivered

    view = await environment.advancer.start(plan, idempotency_key="plan-start")

    assert view.status is ExecutionPlanStatus.IN_PROGRESS
    # The replay drove the delivery the refusal ate.
    assert worker_key in environment.assigner.delivered
    # Duplication proof, over rows rather than call counts: the same Worker
    # task, still the only one, still under the same repository task.
    assert (await environment.worker_task_of(leader_task_id)).id == worker_task.id
    tasks = await environment.tasks.list_by_project(environment.project_id)
    assert len(tasks) == 2
    assert await environment.leader_task_id(plan.id, 0, 0) == leader_task_id


@pytest.mark.asyncio
async def test_a_replay_does_not_touch_a_worker_task_another_attempt_owns() -> None:
    """The decomposer's guard survives: only a task *this* key wrote is re-driven.

    A replay under a prefix that never created the in-flight Worker task must
    not assign a second one beside it. That guard used to be unconditional,
    which is what stopped A-10's round from ever being published; narrowing it
    to "someone else's task" keeps its point and drops its damage.
    """

    environment = Environment()
    repository_task = await environment.assign_repository_task()
    first = await environment.decomposer.execute(repository_task.id, idempotency_key="decompose-1")

    second = await environment.decomposer.execute(repository_task.id, idempotency_key="decompose-2")

    assert second == first
    children = await environment.tasks.list_by_parent(repository_task.id)
    assert len(children) == 1
    # A different prefix's Worker task is not re-delivered under this key.
    assert f"decompose-2:worker:{environment.worker_ids[0]}" not in environment.assigner.delivered


@pytest.mark.asyncio
async def test_replaying_a_failed_plan_does_not_reassign_its_batch() -> None:
    """A plan that has already been settled is history, not unfinished work."""

    environment = Environment()
    plan = environment.plan(((0,),))
    await environment.advancer.start(plan, idempotency_key="plan-start")
    leader_task_id = await environment.leader_task_id(plan.id, 0, 0)
    worker_task = await environment.worker_task_of(leader_task_id)
    await environment.finish(worker_task.id, TaskStatus.FAILED, "compile error")
    await environment.advancer.on_task_terminal(worker_task.id)
    assignments = len(environment.assigner.commands)

    view = await environment.advancer.start(plan, idempotency_key="plan-start")

    assert view.status is ExecutionPlanStatus.FAILED
    assert len(environment.assigner.commands) == assignments


@pytest.mark.asyncio
async def test_plan_observation_reads_all_project_tasks_once() -> None:
    environment = Environment(repository_count=2)
    plan = environment.plan(((0, 1),))
    await environment.advancer.start(plan, idempotency_key="plan-observe")
    calls = 0
    original = environment.tasks.list_by_project

    async def counted(project_id):  # noqa: ANN001
        nonlocal calls
        calls += 1
        return await original(project_id)

    environment.tasks.list_by_project = counted

    snapshot = await ObserveExecutionPlan(environment.plans, environment.tasks).execute(plan.id)

    assert snapshot is not None
    assert calls == 1
    assert len(snapshot.batches[0]) == 2
    assert all(len(item.worker_tasks) == 1 for item in snapshot.batches[0])


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
async def test_concurrent_terminal_notifications_release_the_next_batch_once() -> None:
    import asyncio

    environment = Environment(repository_count=2)
    plan = environment.plan(((0,), (1,)))
    await environment.advancer.start(plan, idempotency_key="plan-concurrent")
    leader_task_id = await environment.leader_task_id(plan.id, 0, 0)
    worker_task = await environment.worker_task_of(leader_task_id)
    await environment.finish(worker_task.id, TaskStatus.SUCCEEDED, "Repository 0 done.")

    await asyncio.gather(
        environment.advancer.on_task_terminal(worker_task.id),
        environment.advancer.on_task_terminal(worker_task.id),
    )

    stored = await environment.plans.get(plan.id)
    assert stored is not None and stored.current_batch_index == 1
    second_repository_assignments = [
        command
        for command, _ in environment.assigner.commands
        if command.repository_id == environment.repository_ids[1]
    ]
    assert len(second_repository_assignments) == 2  # one Leader and one Worker task


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


async def failed_plan_with_a_replanned_leader(environment, plan):
    """Fail a plan on batch 0, then point that batch at a fresh leader task.

    Rewriting leader task ids is the one mutation a failed plan already allows,
    and it is how a replan re-staffs the batch that died.
    """

    leader_task_id = await environment.leader_task_id(plan.id, 0, 0)
    worker_task = await environment.worker_task_of(leader_task_id)
    await environment.finish(worker_task.id, TaskStatus.FAILED, "Verification failed.")
    await environment.advancer.on_task_terminal(worker_task.id)

    replacement = await environment.assign_repository_task(0, key="repair-repository-task")
    failed = await environment.plans.get(plan.id)
    assert failed is not None and failed.status is ExecutionPlanStatus.FAILED
    await environment.plans.update(
        failed.with_leader_tasks(0, (replacement.id,)), expected_version=failed.version
    )
    return replacement


@pytest.mark.asyncio
async def test_a_repaired_batch_reopens_its_failed_plan_and_carries_on() -> None:
    environment = Environment(repository_count=2)
    plan = environment.plan(((0,), (1,)))
    await environment.advancer.start(plan, idempotency_key="plan-start")
    replacement = await failed_plan_with_a_replanned_leader(environment, plan)

    await environment.finish(replacement.id, TaskStatus.SUCCEEDED, "Repaired.")
    await environment.advancer.reconsider_task(replacement.id)

    reopened = await environment.plans.get(plan.id)
    assert reopened is not None
    assert reopened.status is ExecutionPlanStatus.IN_PROGRESS
    # Reopening is not progress by itself; the ordinary advance path moved it.
    assert reopened.current_batch_index == 1
    assert any(
        command.repository_id == environment.repository_ids[1]
        for command, _ in environment.assigner.commands
    )


@pytest.mark.asyncio
async def test_a_plan_whose_batch_is_still_failed_does_not_reopen() -> None:
    """Reopening on an unrepaired batch would only fail again on the next event."""

    environment = Environment(repository_count=2)
    plan = environment.plan(((0,), (1,)))
    await environment.advancer.start(plan, idempotency_key="plan-start")
    replacement = await failed_plan_with_a_replanned_leader(environment, plan)

    # The replacement leader has not succeeded yet.
    await environment.advancer.reconsider_task(replacement.id)

    still_failed = await environment.plans.get(plan.id)
    assert still_failed is not None
    assert still_failed.status is ExecutionPlanStatus.FAILED
    assert still_failed.current_batch_index == 0
    assert all(
        command.repository_id != environment.repository_ids[1]
        for command, _ in environment.assigner.commands
    )


@pytest.mark.asyncio
async def test_a_completed_plan_is_history_and_is_never_reopened() -> None:
    environment = Environment(repository_count=1)
    plan = environment.plan(((0,),))
    await environment.advancer.start(plan, idempotency_key="plan-start")
    leader_task_id = await environment.leader_task_id(plan.id, 0, 0)
    worker_task = await environment.worker_task_of(leader_task_id)
    await environment.finish(worker_task.id, TaskStatus.SUCCEEDED, "Done.")
    await environment.advancer.on_task_terminal(worker_task.id)

    completed = await environment.plans.get(plan.id)
    assert completed is not None and completed.status is ExecutionPlanStatus.COMPLETED
    with pytest.raises(TaskConflict, match="only a failed execution plan"):
        completed.reopen()


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
async def test_batch_waits_for_merged_delivery_before_advancing() -> None:
    delivery = FakeDeliveryState()
    delivered: list[UUID] = []

    async def _deliver(plan) -> None:
        delivered.append(plan.id)

    environment = Environment(
        repository_count=2,
        delivery_state=delivery,
        on_batch_deliver=_deliver,
    )
    plan = environment.plan(((0,), (1,)))
    await environment.advancer.start(plan, idempotency_key="plan-start")

    first_leader_task_id = await environment.leader_task_id(plan.id, 0, 0)
    first_worker = await environment.worker_task_of(first_leader_task_id)
    await environment.finish(first_worker.id, TaskStatus.SUCCEEDED, "Repo 0 done.")
    await environment.advancer.on_task_terminal(first_worker.id)

    # The batch succeeded and was delivered, but its PR is not merged yet.
    waiting = await environment.plans.get(plan.id)
    assert waiting is not None and waiting.current_batch_index == 0
    assert delivered == [plan.id]

    # The merge is observed: re-evaluation advances the plan.
    delivery.merged = {environment.repository_ids[0]: True}
    await environment.advancer.reconsider_task(first_worker.id)

    advanced = await environment.plans.get(plan.id)
    assert advanced is not None and advanced.current_batch_index == 1
    assert advanced.status is ExecutionPlanStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_unmerged_repository_keeps_the_batch_waiting() -> None:
    delivery = FakeDeliveryState()
    environment = Environment(repository_count=2, delivery_state=delivery)
    plan = environment.plan(((0,), (1,)))
    await environment.advancer.start(plan, idempotency_key="plan-start")

    first_leader_task_id = await environment.leader_task_id(plan.id, 0, 0)
    first_worker = await environment.worker_task_of(first_leader_task_id)
    await environment.finish(first_worker.id, TaskStatus.SUCCEEDED, "Repo 0 done.")
    await environment.advancer.on_task_terminal(first_worker.id)

    # A different repository merged does not unblock this batch.
    delivery.merged = {uuid4(): True}
    await environment.advancer.reconsider_task(first_worker.id)

    waiting = await environment.plans.get(plan.id)
    assert waiting is not None and waiting.current_batch_index == 0

    delivery.merged = {environment.repository_ids[0]: True}
    await environment.advancer.reconsider_task(first_worker.id)
    advanced = await environment.plans.get(plan.id)
    assert advanced is not None and advanced.current_batch_index == 1


@pytest.mark.asyncio
async def test_batch_delivery_is_not_triggered_before_the_batch_succeeds() -> None:
    delivered: list[UUID] = []

    async def _deliver(plan) -> None:
        delivered.append(plan.id)

    environment = Environment(repository_count=2, on_batch_deliver=_deliver)
    plan = environment.plan(((0,), (1,)))
    await environment.advancer.start(plan, idempotency_key="plan-start")

    # Batch 0 has a single repository; until its leader task reports success
    # the batch delivery callback must not fire.
    first_leader_task_id = await environment.leader_task_id(plan.id, 0, 0)
    first_worker = await environment.worker_task_of(first_leader_task_id)
    await environment.advancer.on_task_terminal(first_worker.id)
    assert delivered == []

    await environment.finish(first_worker.id, TaskStatus.SUCCEEDED, "Repo 0 done.")
    await environment.advancer.on_task_terminal(first_worker.id)
    assert delivered == [plan.id]


@pytest.mark.asyncio
async def test_every_batch_permits_its_worker_with_the_verification_of_that_batch() -> None:
    environment = Environment(repository_count=2)
    plan = environment.plan(
        ((0,), (1,)),
        tests={0: ("uv run pytest tests/checkout",), 1: ("uv run pytest tests/billing",)},
    )

    await environment.advancer.start(plan, idempotency_key="plan-start")

    assert len(environment.recorded_specifications) == 1
    first = environment.recorded_specifications[0]
    assert first.task.assignee_agent_id == environment.worker_ids[0]
    assert first.tests == ("uv run pytest tests/checkout",)
    assert first.idempotency_key == (
        f"plan-start:b0:{environment.repository_ids[0]}:decompose:spec:{environment.worker_ids[0]}"
    )

    leader_task_id = await environment.leader_task_id(plan.id, 0, 0)
    worker_task = await environment.worker_task_of(leader_task_id)
    await environment.finish(worker_task.id, TaskStatus.SUCCEEDED, "Checkout delivered.")
    await environment.advancer.on_task_terminal(worker_task.id)

    assert len(environment.recorded_specifications) == 2
    second = environment.recorded_specifications[1]
    assert second.task.assignee_agent_id == environment.worker_ids[1]
    assert second.tests == ("uv run pytest tests/billing",)
    assert second.idempotency_key == (
        f"{plan.id}:b1:{environment.repository_ids[1]}:decompose:spec:{environment.worker_ids[1]}"
    )


@pytest.mark.asyncio
async def test_terminal_task_outside_any_plan_is_ignored() -> None:
    environment = Environment()
    repository_task = await environment.assign_repository_task()
    await environment.finish(repository_task.id, TaskStatus.SUCCEEDED, "Delivered.")

    await environment.advancer.on_task_terminal(repository_task.id)
    await environment.advancer.on_task_terminal(uuid4())

    assert environment.plans.plans == {}


# ---------------------------------------------------------------------------
# A refused delivery is a state, not a background traceback (A-19)
# ---------------------------------------------------------------------------


class RefusingDelivery:
    """``on_batch_deliver`` that refuses until it is told to stop refusing.

    Models the real sequence rather than a single failure: the batch is refused
    because its Runner evidence carries no test results, and later — after a
    re-dispatch with the repository's verification commands actually in the
    payload — the same call succeeds. Both halves are the defect.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        # Set by the test once the environment exists; the environment needs
        # the callback at construction time and the callback needs its ids.
        self.repository_id: UUID | None = None
        self.task_id: UUID | None = None
        self.refusing = True
        self.calls = 0

    async def __call__(self, plan) -> None:
        self.calls += 1
        if self.refusing:
            raise BatchDeliveryRefused(
                self.reason, repository_id=self.repository_id, task_id=self.task_id
            )


async def _succeed_first_batch(environment: Environment, plan) -> UUID:
    """Carry batch 0's only worker to SUCCEEDED and re-enter the advancer."""

    leader_task_id = await environment.leader_task_id(plan.id, 0, 0)
    worker = await environment.worker_task_of(leader_task_id)
    await environment.finish(worker.id, TaskStatus.SUCCEEDED, "Repo 0 done.")
    await environment.advancer.on_task_terminal(worker.id)
    return worker.id


@pytest.mark.asyncio
async def test_a_refused_delivery_is_recorded_on_the_round_rather_than_thrown_away() -> None:
    """Defect A-19's silent twin: the refusal used to reach nobody.

    ``_candidates_for_batch`` refuses a candidate whose Runner evidence has no
    test results — correctly. But it refused by raising into
    ``_advance_if_ready``, which runs under the Runner ingest's best-effort
    handler, so the exception was logged and dropped on every terminal event.
    Nothing was written and nothing was projected: the console showed green
    tasks beside a change set that would stay empty forever.

    So the assertion is not "it did not crash". It is that the round now
    *carries* the refusal, in the delivering side's own words, naming the
    repository — while still refusing to advance, which is the part that was
    right all along.
    """

    delivery = RefusingDelivery("Runner evidence has no test results")
    environment = Environment(repository_count=2, on_batch_deliver=delivery)
    delivery.repository_id = environment.repository_ids[0]
    plan = environment.plan(((0,), (1,)))
    await environment.advancer.start(plan, idempotency_key="plan-start")

    await _succeed_first_batch(environment, plan)

    refused = await environment.plans.get(plan.id)
    assert refused is not None
    assert refused.delivery_refusal is not None
    assert refused.delivery_refusal.reason == "Runner evidence has no test results"
    assert refused.delivery_refusal.repository_id == environment.repository_ids[0]
    assert refused.delivery_refusal.batch_index == 0
    # Refused, not failed and not advanced. Delivery declining unverified work
    # is correct and nothing here weakens it.
    assert refused.status is ExecutionPlanStatus.IN_PROGRESS
    assert refused.current_batch_index == 0


@pytest.mark.asyncio
async def test_a_repeated_refusal_does_not_rewrite_the_round() -> None:
    """The crash-loop's shape, gone: many events, one recorded refusal.

    Every terminal Runner event and every delivery observation re-enters the
    advancer, so an unresolved refusal is restated constantly. If each
    restatement wrote a new version, the round's history would be the loop
    rather than the reason for it.
    """

    delivery = RefusingDelivery("Runner evidence has no test results")
    environment = Environment(repository_count=2, on_batch_deliver=delivery)
    delivery.repository_id = environment.repository_ids[0]
    plan = environment.plan(((0,), (1,)))
    await environment.advancer.start(plan, idempotency_key="plan-start")

    worker_id = await _succeed_first_batch(environment, plan)
    first = await environment.plans.get(plan.id)
    assert first is not None and first.delivery_refusal is not None

    await environment.advancer.reconsider_task(worker_id)
    await environment.advancer.reconsider_task(worker_id)

    again = await environment.plans.get(plan.id)
    assert again is not None
    assert delivery.calls == 3  # it really was asked three times
    assert again.version == first.version
    assert again.delivery_refusal.at == first.delivery_refusal.at


@pytest.mark.asyncio
async def test_evidence_that_finally_carries_test_results_clears_the_refusal() -> None:
    """Convergence, and what re-triggers it.

    Nothing new: the same entry points that were already re-firing during the
    crash-loop — ``on_task_terminal`` for a terminal Runner event,
    ``reconsider_task`` for a delivery observation — reach the advance path.
    Once delivery stops refusing, the recorded refusal is cleared and the plan
    advances in the same call, with no operator action and no second code path.
    """

    delivery = RefusingDelivery("Runner evidence has no test results")
    environment = Environment(repository_count=2, on_batch_deliver=delivery)
    delivery.repository_id = environment.repository_ids[0]
    plan = environment.plan(((0,), (1,)))
    await environment.advancer.start(plan, idempotency_key="plan-start")

    worker_id = await _succeed_first_batch(environment, plan)
    assert (await environment.plans.get(plan.id)).delivery_refusal is not None

    # The re-dispatched Worker reported evidence with test results in it, so
    # the delivering side accepts the batch this time.
    delivery.refusing = False
    await environment.advancer.reconsider_task(worker_id)

    advanced = await environment.plans.get(plan.id)
    assert advanced is not None
    assert advanced.delivery_refusal is None
    assert advanced.current_batch_index == 1
    assert advanced.status is ExecutionPlanStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_a_fault_in_the_delivering_side_is_not_recorded_as_its_verdict() -> None:
    """Only a *stated* refusal is caught, and the reverse-proof of that.

    An adapter that cannot reach GitHub is a fault, not a judgement about the
    evidence. Recording it on the round would put a sentence in front of the
    operator that delivery never said. It still escapes — which is also what
    keeps this handling from being a blanket ``except Exception`` that would
    hide the next bug the way the old code hid this one.
    """

    async def _explode(plan) -> None:
        raise RuntimeError("github: connection reset")

    environment = Environment(repository_count=2, on_batch_deliver=_explode)
    plan = environment.plan(((0,), (1,)))
    await environment.advancer.start(plan, idempotency_key="plan-start")

    leader_task_id = await environment.leader_task_id(plan.id, 0, 0)
    worker = await environment.worker_task_of(leader_task_id)
    await environment.finish(worker.id, TaskStatus.SUCCEEDED, "Repo 0 done.")

    with pytest.raises(RuntimeError, match="connection reset"):
        await environment.advancer.on_task_terminal(worker.id)

    untouched = await environment.plans.get(plan.id)
    assert untouched is not None and untouched.delivery_refusal is None
