"""End-to-end proof of the closed plan execution loop.

One chain test wires the real components of the loop together:

``PlanExecutionBridge`` → ``AdvanceExecutionPlanStarter`` → ``AdvanceExecutionPlan``
→ ``TaskOrchestrator``/``DecomposeRepositoryTask`` → ``RunnerControlGateway``
→ ``AdvanceExecutionPlan.on_task_terminal`` → next batch.

Only the outside world is faked: the agent directory, the project topology, the
repository catalog, the project-level specification creation the bridge performs
and Matrix chat delivery.  Task and execution-plan state lives in a real
SQLite-backed store, the Task Specifications are written by the real
``SpecificationService`` through the container's ``ApprovedTaskSpecificationAuthor``,
and the Runner events travel through the real gateway store, so the seam this
workstream exists to prove -- a Runner result advancing the plan without any
further manual call -- is exercised against real objects.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

from repomesh.bootstrap.container import (
    AdvanceExecutionPlanStarter,
    ApprovedTaskSpecificationAuthor,
)
from repomesh.integrations.runner.gateway import RunnerControlGateway
from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalStatus,
    AgentPrincipalView,
    AgentRole,
)
from repomesh.modules.agent_runtime.runner_store import PostgresRunnerGatewayStore
from repomesh.modules.change_orchestration import (
    MaterializationResult,
    PlanExecutionBridge,
)
from repomesh.modules.collaboration.contracts import (
    CollaborationDeliveryStatus,
    CollaborationMessageView,
    SendCollaborationMessageCommand,
)
from repomesh.modules.context.application import ContextPublicationGateway
from repomesh.modules.context.infrastructure import PostgresContextStore
from repomesh.modules.identity_access import PolicyAuthorizationGateway
from repomesh.modules.project.contracts import (
    ProjectAgentTopologyView,
    ProjectTeamRuntimeStatus,
    RepositoryTeamView,
)
from repomesh.modules.repository_intelligence.api.router import build_execution_plan_status
from repomesh.modules.repository_intelligence.application.plan_integration import (
    IntegratedPlan,
    TaskNode,
)
from repomesh.modules.repository_intelligence.domain import RepositoryProfile
from repomesh.modules.specification import (
    PostgresSpecificationStore,
    SpecificationKind,
    SpecificationService,
)
from repomesh.modules.specification.contracts import (
    CreateSpecificationCommand,
    SpecificationVersionView,
    SpecificationView,
)
from repomesh.modules.specification.domain import Specification, SpecificationStatus
from repomesh.modules.task_orchestration import ObserveExecutionPlan
from repomesh.modules.task_orchestration.application import (
    AdvanceExecutionPlan,
    DecomposeRepositoryTask,
    TaskExecutionState,
    TaskOrchestrator,
)
from repomesh.modules.task_orchestration.contracts import (
    ExecutionPlanStatus,
    PublishedTaskPackage,
    TaskStatus,
    TaskView,
)
from repomesh.modules.task_orchestration.domain import ExecutionPlan, Task
from repomesh.modules.task_orchestration.infrastructure import (
    PostgresExecutionPlanStore,
    PostgresTaskStore,
)
from repomesh.persistence import Database
from repomesh.persistence.base import ALL_SCHEMAS
from repomesh_runner.contracts import (
    ContextBundleRef,
    RepositoryCheckout,
    RunnerPermissions,
    RunnerTask,
)

CONTENT_HASH = "sha256:" + "a" * 64
REPOSITORY_NAMES = ("repo-a", "repo-b")
TEST_COMMANDS = ("python scripts/run_tests.py",)

# ---------------------------------------------------------------------------
# Fakes for everything outside the loop under test
# ---------------------------------------------------------------------------


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


class FakeRepositoryCatalog:
    def __init__(self, name_to_id: dict[str, UUID]) -> None:
        self._name_to_id = name_to_id

    async def list(self) -> list[RepositoryProfile]:
        return [
            RepositoryProfile(name=name, url=f"https://example.test/{name}.git", id=repository_id)
            for name, repository_id in self._name_to_id.items()
        ]


class FakeSpecificationService:
    """Record specification creation without touching the specification store."""

    def __init__(self) -> None:
        self.commands: list[tuple[CreateSpecificationCommand, str]] = []

    async def create(
        self, command: CreateSpecificationCommand, *, idempotency_key: str
    ) -> SpecificationView:
        self.commands.append((command, idempotency_key))
        specification_id = uuid4()
        return SpecificationView(
            id=specification_id,
            organization_id=command.organization_id,
            project_id=command.project_id,
            kind=command.kind,
            status=SpecificationStatus.DRAFT,
            title=command.title,
            repository_id=None,
            task_id=None,
            owner_agent_id=command.created_by_agent_id,
            revision=1,
            current_version=SpecificationVersionView(
                id=uuid4(),
                specification_id=specification_id,
                version=1,
                content_hash=CONTENT_HASH,
                created_by_agent_id=command.created_by_agent_id,
            ),
        )


class FakeCollaboration:
    """Accept the chat delivery the TaskOrchestrator performs after assigning."""

    #: Fixed base so replayed runs produce identical timestamps; each message
    #: is one second after the previous one, matching how ``event_id`` numbers
    #: them, so nothing downstream has to break a tie between two messages.
    FIRST_SENT_AT = datetime(2026, 8, 11, 10, 0, tzinfo=UTC)

    def __init__(self) -> None:
        self.messages: list[SendCollaborationMessageCommand] = []

    async def send(
        self, command: SendCollaborationMessageCommand, *, idempotency_key: str
    ) -> CollaborationMessageView:
        self.messages.append(command)
        return CollaborationMessageView(
            id=uuid4(),
            organization_id=command.organization_id,
            project_id=command.project_id,
            repository_id=command.repository_id,
            task_id=command.task_id,
            sender_agent_id=command.sender_agent_id,
            recipient_agent_id=command.recipient_agent_id,
            kind=command.kind,
            subject=command.subject,
            body=command.body,
            room_id="!room:matrix.local",
            status=CollaborationDeliveryStatus.DELIVERED,
            event_id=f"$event-{len(self.messages)}",
            correlation_id=command.correlation_id or command.sender_agent_id,
            created_at=self.FIRST_SENT_AT + timedelta(seconds=len(self.messages)),
        )


class FakeTaskPublisher:
    """Stand in for the AgentTeams task-package publication."""

    def __init__(self) -> None:
        self.published: list[TaskView] = []

    async def publish(
        self,
        task: TaskView,
        *,
        team_name: str,
        room_id: str,
        assignee_resource_name: str,
        idempotency_key: str,
        package: object | None = None,
    ) -> PublishedTaskPackage:
        self.published.append(task)
        return PublishedTaskPackage(
            team_name=team_name,
            task_path=f"tasks/{task.id}.json",
            content_hash=CONTENT_HASH,
        )


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


class LoopEnvironment:
    """The whole closed loop assembled around one SQLite database."""

    def __init__(self, database: Database) -> None:
        self.organization_id = uuid4()
        self.project_id = uuid4()
        self.organization_leader_id = uuid4()
        self.repository_ids: list[UUID] = []
        self.leader_ids: list[UUID] = []
        self.worker_ids: list[UUID] = []
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
        for name in REPOSITORY_NAMES:
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
                    agentteams_resource_name=f"{name}-leader",
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
                    agentteams_resource_name=f"{name}-worker",
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
                    agentteams_team_name=f"team-{name}",
                    runtime_status=ProjectTeamRuntimeStatus.READY,
                    room_id=f"!{name}:matrix.local",
                    leader_room_id=f"!{name}-leader:matrix.local",
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
        self.catalog = FakeRepositoryCatalog(
            dict(zip(REPOSITORY_NAMES, self.repository_ids, strict=True))
        )

        self.tasks = PostgresTaskStore(database)
        self.plans = PostgresExecutionPlanStore(database)
        self.collaboration = FakeCollaboration()
        self.publisher = FakeTaskPublisher()
        self.orchestrator = TaskOrchestrator(
            self.directory,
            self.topologies,
            self.tasks,
            self.collaboration,
            self.publisher,
        )
        self.states = TaskExecutionState(self.directory, self.tasks)
        self.specification_store = PostgresSpecificationStore(database)
        self.specification_service = SpecificationService(
            self.directory,
            self.topologies,
            self.specification_store,
            ContextPublicationGateway(PostgresContextStore(database)),
            PolicyAuthorizationGateway(),
        )
        self.decomposer = DecomposeRepositoryTask(
            self.directory,
            self.topologies,
            self.tasks,
            self.orchestrator,
            spec_author=ApprovedTaskSpecificationAuthor(
                self.specification_service, self.specification_store
            ),
        )
        self.advancer = AdvanceExecutionPlan(
            self.plans, self.tasks, self.orchestrator, self.decomposer
        )
        self.specifications = FakeSpecificationService()
        self.bridge = PlanExecutionBridge(
            self.specifications,
            AdvanceExecutionPlanStarter(self.advancer, self.tasks),
            self.topologies,
            self.catalog,
        )
        self.runner_store = PostgresRunnerGatewayStore(database)
        self.gateway = RunnerControlGateway(
            self.runner_store, self.tasks, self.advancer.on_task_terminal
        )

    # -- driving the loop ------------------------------------------------

    async def materialize(self, *, idempotency_prefix: str = "loop") -> MaterializationResult:
        """Run the bridge over a two-batch plan where repo-b depends on repo-a."""

        plan = IntegratedPlan(
            engineering_spec="Deliver the approved cross-repository scope.",
            contracts=[],
            task_dag=[
                TaskNode(
                    repository="repo-a",
                    instruction="Publish the new pricing endpoint.",
                    tests=TEST_COMMANDS,
                ),
                TaskNode(
                    repository="repo-b",
                    instruction="Consume the new pricing endpoint.",
                    depends_on=("repo-a",),
                    tests=TEST_COMMANDS,
                ),
            ],
            execution_batches=[["repo-a"], ["repo-b"]],
        )
        return await self.bridge.materialize(
            plan=plan,
            requirement="align pricing across repositories",
            project_id=self.project_id,
            leader_agent_id=self.organization_leader_id,
            idempotency_prefix=idempotency_prefix,
        )

    async def plan_state(self, plan_id: UUID) -> ExecutionPlan:
        plan = await self.plans.get(plan_id)
        assert plan is not None
        return plan

    async def leader_task_id(self, plan_id: UUID, batch_index: int) -> UUID | None:
        plan = await self.plan_state(plan_id)
        return plan.batches[batch_index][0].leader_task_id

    async def task(self, task_id: UUID) -> Task:
        task = await self.tasks.get(task_id)
        assert task is not None
        return task

    async def worker_task(self, leader_task_id: UUID) -> Task:
        children = await self.tasks.list_by_parent(leader_task_id)
        assert len(children) == 1
        return children[0]

    async def task_specification(self, task_id: UUID) -> Specification:
        """The execution permit ``start_assigned_task`` looks up for a Worker task."""

        stored = await self.specification_store.list_by_project(self.project_id)
        bound = [
            specification
            for specification in stored
            if specification.kind is SpecificationKind.TASK and specification.task_id == task_id
        ]
        assert len(bound) == 1
        return bound[0]

    async def report_runner_result(
        self, worker_task: Task, *, event_type: str, summary: str
    ) -> UUID:
        """Play the execution plane for one Worker task and report a result.

        Nothing here touches the execution plan: the only path from the Runner
        event to the next batch is the gateway's terminal callback.
        """

        worker_agent_id = worker_task.assignee_agent_id
        await self.states.start(worker_task.id, agent_id=worker_agent_id)
        run_id = uuid4()
        dispatch = RunnerTask(
            organization_id=worker_task.organization_id,
            project_id=worker_task.project_id,
            task_id=worker_task.id,
            run_id=run_id,
            correlation_id=worker_task.id,
            attempt=1,
            adapter_id="claude",
            instruction="Read the task context and implement the change.",
            repository=RepositoryCheckout(
                worker_task.repository_id, "https://example.test/repo.git", "main"
            ),
            context_bundle=ContextBundleRef(uuid4(), 1, "file:///manifest.json", CONTENT_HASH),
            permissions=RunnerPermissions(),
            idempotency_key=f"run-{worker_task.id}",
            issued_at=datetime.now(UTC),
            worker_agent_id=worker_agent_id,
        )
        await self.gateway.enqueue(dispatch)
        leased = await self.gateway.next_task(worker_agent_id)
        assert leased is not None
        assert leased["runId"] == str(run_id)

        assert await self.gateway.receive_event(
            self._event(dispatch, "runner.accepted", sequence=1, payload={})
        )
        assert await self.gateway.receive_event(
            self._event(
                dispatch,
                event_type,
                sequence=2,
                payload={
                    "status": "succeeded" if event_type == "runner.completed" else "failed",
                    "summary": summary,
                    "changedFiles": ["src/pricing.py"],
                    "testResults": [{"command": "pytest", "exitCode": 0}],
                    "commitSha": "b" * 40,
                },
            )
        )
        return run_id

    async def status_view(self, plan_id: UUID):
        """Aggregate the plan the way the observation endpoint does."""

        snapshot = await ObserveExecutionPlan(self.plans, self.tasks).execute(plan_id)
        assert snapshot is not None
        return build_execution_plan_status(snapshot)

    @staticmethod
    def _event(
        dispatch: RunnerTask, event_type: str, *, sequence: int, payload: dict[str, object]
    ) -> dict[str, object]:
        return {
            "schemaVersion": "runtime.v1",
            "eventId": str(uuid4()),
            "eventType": event_type,
            "organizationId": str(dispatch.organization_id),
            "projectId": str(dispatch.project_id),
            "taskId": str(dispatch.task_id),
            "runId": str(dispatch.run_id),
            "correlationId": str(dispatch.correlation_id),
            "attempt": dispatch.attempt,
            "sequence": sequence,
            "occurredAt": datetime.now(UTC).isoformat(),
            "nativeSessionId": f"session-{sequence}",
            "payload": payload,
        }


@pytest_asyncio.fixture
async def loop(tmp_path: object):
    database = Database(
        f"sqlite+aiosqlite:///{tmp_path.joinpath('plan-loop.db')}",
        schema_translate_map={schema: None for schema in ALL_SCHEMAS},
    )
    await database.create_all_for_tests()
    yield LoopEnvironment(database)
    await database.dispose()


def _evidence(task: Task) -> dict[str, object]:
    assert task.result_summary is not None
    return json.loads(task.result_summary)


# ---------------------------------------------------------------------------
# Scenario 1: the happy path across two batches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_results_drive_the_plan_from_the_first_batch_to_completion(
    loop: LoopEnvironment, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR):
        result = await loop.materialize()

        # --- the bridge materialised specs and started the plan ---------
        assert result.engineering_spec is not None
        assert loop.specifications.commands[0][1] == "loop-eng-spec"
        assert result.plan_id is not None
        assert result.skipped_repos == []
        plan_id = result.plan_id

        started = await loop.plan_state(plan_id)
        assert started.status is ExecutionPlanStatus.IN_PROGRESS
        assert started.current_batch_index == 0

        # --- batch 0 is assigned, batch 1 is not ------------------------
        first_leader_task_id = await loop.leader_task_id(plan_id, 0)
        assert first_leader_task_id is not None
        assert await loop.leader_task_id(plan_id, 1) is None

        first_leader = await loop.task(first_leader_task_id)
        assert first_leader.repository_id == loop.repository_ids[0]
        assert first_leader.assignee_agent_id == loop.leader_ids[0]
        assert first_leader.assigned_by_agent_id == loop.organization_leader_id
        assert first_leader.title == "Implement changes for repo-a"

        first_worker = await loop.worker_task(first_leader_task_id)
        assert first_worker.assignee_agent_id == loop.worker_ids[0]
        assert first_worker.assigned_by_agent_id == loop.leader_ids[0]
        assert first_worker.status is TaskStatus.ASSIGNED
        assert await _repository_tasks(loop, loop.repository_ids[1]) == ()

        # --- the Worker task already carries its execution permit -------
        # Without this the Worker's start_assigned_task call fails with
        # SpecificationNotFound, which is exactly what live verification hit.
        first_specification = await loop.task_specification(first_worker.id)
        assert first_specification.status is SpecificationStatus.FROZEN
        assert first_specification.repository_id == loop.repository_ids[0]
        assert first_specification.owner_agent_id == loop.leader_ids[0]
        content = first_specification.current_version.content
        assert content.goal == first_worker.instruction
        assert content.tests == TEST_COMMANDS
        assert content.allowed_paths == ("src/**",)

        # --- the Runner reports repo-a, and nothing else is called ------
        await loop.report_runner_result(
            first_worker, event_type="runner.completed", summary="pricing endpoint published"
        )

        finished_worker = await loop.task(first_worker.id)
        assert finished_worker.status is TaskStatus.SUCCEEDED
        assert _evidence(finished_worker)["summary"] == "pricing endpoint published"
        assert _evidence(finished_worker)["changedFiles"] == ["src/pricing.py"]
        assert _evidence(finished_worker)["commitSha"] == "b" * 40
        assert (await loop.task(first_leader_task_id)).status is TaskStatus.SUCCEEDED

        advanced = await loop.plan_state(plan_id)
        assert advanced.status is ExecutionPlanStatus.IN_PROGRESS
        assert advanced.current_batch_index == 1

        # --- batch 1 was assigned and decomposed by the loop itself -----
        second_leader_task_id = await loop.leader_task_id(plan_id, 1)
        assert second_leader_task_id is not None
        second_leader = await loop.task(second_leader_task_id)
        assert second_leader.repository_id == loop.repository_ids[1]
        assert second_leader.assignee_agent_id == loop.leader_ids[1]
        second_worker = await loop.worker_task(second_leader_task_id)
        assert second_worker.assignee_agent_id == loop.worker_ids[1]
        assert second_worker.status is TaskStatus.ASSIGNED

        # --- and the advanced batch produced its permit too -------------
        second_specification = await loop.task_specification(second_worker.id)
        assert second_specification.status is SpecificationStatus.FROZEN
        assert second_specification.repository_id == loop.repository_ids[1]
        assert second_specification.owner_agent_id == loop.leader_ids[1]
        assert second_specification.current_version.content.tests == TEST_COMMANDS

        # --- finishing the last batch completes the plan ----------------
        await loop.report_runner_result(
            second_worker, event_type="runner.completed", summary="pricing endpoint consumed"
        )

    assert "Failed to advance the execution plan" not in caplog.text
    assert (await loop.task(second_worker.id)).status is TaskStatus.SUCCEEDED
    assert (await loop.task(second_leader_task_id)).status is TaskStatus.SUCCEEDED
    completed = await loop.plan_state(plan_id)
    assert completed.status is ExecutionPlanStatus.COMPLETED
    assert completed.current_batch_index == 1

    status = await loop.status_view(plan_id)
    assert status.plan_id == plan_id
    assert status.status == "completed"
    assert status.current_batch_index == 1
    assert [
        [
            (planned.leader_status, [worker.status for worker in planned.worker_tasks])
            for planned in batch
        ]
        for batch in status.batches
    ] == [[("succeeded", ["succeeded"])], [("succeeded", ["succeeded"])]]


# ---------------------------------------------------------------------------
# Scenario 2: a failed Runner result halts the plan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failed_runner_result_halts_the_plan_before_the_next_batch(
    loop: LoopEnvironment,
) -> None:
    result = await loop.materialize(idempotency_prefix="loop-failure")
    plan_id = result.plan_id
    assert plan_id is not None
    leader_task_id = await loop.leader_task_id(plan_id, 0)
    assert leader_task_id is not None
    worker_task = await loop.worker_task(leader_task_id)

    await loop.report_runner_result(
        worker_task, event_type="runner.failed", summary="verification failed"
    )

    failed_worker = await loop.task(worker_task.id)
    assert failed_worker.status is TaskStatus.FAILED
    assert _evidence(failed_worker)["summary"] == "verification failed"
    leader_task = await loop.task(leader_task_id)
    assert leader_task.status is TaskStatus.FAILED
    assert "did not succeed" in (leader_task.result_summary or "")

    halted = await loop.plan_state(plan_id)
    assert halted.status is ExecutionPlanStatus.FAILED
    assert halted.current_batch_index == 0
    assert await loop.leader_task_id(plan_id, 1) is None
    assert await _repository_tasks(loop, loop.repository_ids[1]) == ()

    status = await loop.status_view(plan_id)
    assert status.status == "failed"
    assert status.batches[0][0].leader_status == "failed"
    assert status.batches[1][0].leader_task_id is None


# ---------------------------------------------------------------------------
# Scenario 3: the decomposition dedup guard inside the live loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decomposing_a_live_leader_task_again_reuses_the_worker_task(
    loop: LoopEnvironment,
) -> None:
    """The dedup surface exercised here is ``DecomposeRepositoryTask``.

    The plan already decomposed batch 0 when it started, so a second
    decomposition of the same leader task must reuse the in-flight Worker task
    instead of assigning another one.
    """

    result = await loop.materialize(idempotency_prefix="loop-dedup")
    plan_id = result.plan_id
    assert plan_id is not None
    leader_task_id = await loop.leader_task_id(plan_id, 0)
    assert leader_task_id is not None
    worker_task = await loop.worker_task(leader_task_id)
    published_before = len(loop.publisher.published)

    replayed = await loop.decomposer.execute(
        leader_task_id, idempotency_key="loop-dedup:manual-decompose"
    )

    assert [task.id for task in replayed] == [worker_task.id]
    assert len(await loop.tasks.list_by_parent(leader_task_id)) == 1
    assert len(loop.publisher.published) == published_before
    # Healing the permit on replay must not mint a second Task Specification.
    assert (await loop.task_specification(worker_task.id)).status is (SpecificationStatus.FROZEN)


async def _repository_tasks(loop: LoopEnvironment, repository_id: UUID) -> tuple[Task, ...]:
    tasks = await loop.tasks.list_by_project(loop.project_id)
    return tuple(task for task in tasks if task.repository_id == repository_id)
