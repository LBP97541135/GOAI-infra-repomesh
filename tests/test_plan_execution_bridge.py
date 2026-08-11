"""Tests for PlanExecutionBridge."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from repomesh.bootstrap.container import AdvanceExecutionPlanStarter  # noqa: E402
from repomesh.modules.agent_directory.contracts import (  # noqa: E402
    AgentPrincipalStatus,
    AgentPrincipalView,
    AgentRole,
)
from repomesh.modules.change_orchestration import (  # noqa: E402
    ExecutionPlaneUnavailable,
    MaterializationResult,
    PlanExecutionBridge,
    StartedExecutionPlan,
)
from repomesh.modules.project.contracts import (  # noqa: E402
    ProjectAgentTopologyView,
    ProjectTeamRuntimeStatus,
    RepositoryTeamView,
)
from repomesh.modules.repository_intelligence.application.plan_integration import (  # noqa: E402
    ContractSpec,
    IntegratedPlan,
    TaskNode,
)
from repomesh.modules.repository_intelligence.domain import RepositoryProfile  # noqa: E402
from repomesh.modules.specification.contracts import (  # noqa: E402
    SpecificationKind,
    SpecificationVersionView,
    SpecificationView,
)
from repomesh.modules.specification.domain import SpecificationStatus  # noqa: E402
from repomesh.modules.task_orchestration.application import (  # noqa: E402
    AdvanceExecutionPlan,
    DecomposeRepositoryTask,
)
from repomesh.modules.task_orchestration.contracts import (  # noqa: E402
    AssignTaskCommand,
    ExecutionPlanStatus,
    ExecutionPlanView,
    PlannedRepositoryTaskView,
    TaskStatus,
    TaskView,
)
from repomesh.modules.task_orchestration.domain import Task  # noqa: E402
from repomesh.modules.task_orchestration.infrastructure import (  # noqa: E402
    InMemoryExecutionPlanStore,
    InMemoryTaskStore,
)

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


def _make_spec_view(cmd, counter: int) -> SpecificationView:
    return SpecificationView(
        id=UUID(int=counter),
        organization_id=cmd.organization_id,
        project_id=cmd.project_id,
        kind=cmd.kind,
        status=SpecificationStatus.DRAFT,
        title=cmd.title,
        repository_id=None,
        task_id=None,
        owner_agent_id=cmd.created_by_agent_id,
        revision=1,
        current_version=SpecificationVersionView(
            id=UUID(int=counter + 10000),
            specification_id=UUID(int=counter),
            version=1,
            content_hash="abc123",
            created_by_agent_id=cmd.created_by_agent_id,
        ),
    )


class StubSpecService:
    def __init__(self) -> None:
        self.calls: list = []
        self._counter = 0

    async def create(self, command, *, idempotency_key):
        self._counter += 1
        self.calls.append((command, idempotency_key))
        return _make_spec_view(command, self._counter)


class StubPlanStarter:
    """Record what the bridge would hand to task_orchestration."""

    def __init__(self) -> None:
        self.calls: list = []

    async def start_plan(
        self,
        *,
        organization_id: UUID,
        project_id: UUID,
        created_by_agent_id: UUID,
        batches: Sequence[Sequence[PlannedRepositoryTaskView]],
        idempotency_key: str,
    ) -> StartedExecutionPlan:
        self.calls.append((organization_id, project_id, created_by_agent_id, batches,
                           idempotency_key))
        return StartedExecutionPlan(
            plan=ExecutionPlanView(
                id=UUID(int=7777),
                organization_id=organization_id,
                project_id=project_id,
                created_by_agent_id=created_by_agent_id,
                status=ExecutionPlanStatus.IN_PROGRESS,
                current_batch_index=0,
                batches=tuple(tuple(batch) for batch in batches),
            ),
            tasks=(),
        )


class StubProjectTaskReader:
    def __init__(self, tasks: tuple[TaskView, ...]) -> None:
        self.tasks = tasks

    async def list_project_tasks(self, project_id: UUID) -> tuple[TaskView, ...]:
        return tuple(task for task in self.tasks if task.project_id == project_id)


class RecordingSuperseder:
    def __init__(self, tasks: tuple[TaskView, ...]) -> None:
        self.tasks = tasks
        self.calls = []

    async def supersede(self, command, *, idempotency_key):
        self.calls.append((command, idempotency_key))
        task = next(task for task in self.tasks if task.id == command.task_id)
        return TaskView(
            id=task.id,
            organization_id=task.organization_id,
            project_id=task.project_id,
            repository_id=task.repository_id,
            parent_task_id=task.parent_task_id,
            assigned_by_agent_id=task.assigned_by_agent_id,
            assignee_agent_id=task.assignee_agent_id,
            title=task.title,
            instruction=task.instruction,
            acceptance=task.acceptance,
            status=TaskStatus.SUPERSEDED,
            result_summary=f"SUPERSEDED: {command.reason}",
            version=task.version + 1,
        )


def _task_view(
    *, project_id: UUID, repository_id: UUID, status: TaskStatus = TaskStatus.ASSIGNED
) -> TaskView:
    return TaskView(
        id=uuid4(),
        organization_id=uuid4(),
        project_id=project_id,
        repository_id=repository_id,
        parent_task_id=None,
        assigned_by_agent_id=uuid4(),
        assignee_agent_id=uuid4(),
        title="repository task",
        instruction="implement approved change",
        acceptance=("tests pass",),
        status=status,
        result_summary=None,
        version=1,
    )


class StubTopologyReader:
    def __init__(self, topology: ProjectAgentTopologyView) -> None:
        self._topology = topology

    async def get_view(self, project_id):
        return self._topology


class StubCatalog:
    """Returns a list of RepositoryProfile with name → id mapping."""

    def __init__(self, name_to_id: dict[str, UUID]) -> None:
        self._name_to_id = name_to_id

    async def list(self) -> list:
        return [
            RepositoryProfile(name=name, url=f"https://github.com/test/{name}", id=rid)
            for name, rid in self._name_to_id.items()
        ]


def _make_topology(
    org_id: UUID,
    project_id: UUID,
    leader_id: UUID,
    teams: list[tuple[UUID, UUID]],
) -> ProjectAgentTopologyView:
    return ProjectAgentTopologyView(
        id=uuid4(),
        organization_id=org_id,
        project_id=project_id,
        organization_leader_id=leader_id,
        repository_teams=tuple(
            RepositoryTeamView(
                id=uuid4(),
                project_id=project_id,
                repository_id=repo_id,
                leader_agent_id=team_leader,
                worker_agent_ids=(),
                agentteams_team_name=f"team-{i}",
                runtime_status=ProjectTeamRuntimeStatus.READY,
                room_id=None,
                leader_room_id=None,
            )
            for i, (repo_id, team_leader) in enumerate(teams)
        ),
    )


def _make_plan(
    repos: list[str],
    contracts: list[ContractSpec] | None = None,
    batches: list[list[str]] | None = None,
    tests: tuple[str, ...] = (),
) -> IntegratedPlan:
    dag = [TaskNode(repository=r, instruction=f"change {r}", tests=tests) for r in repos]
    return IntegratedPlan(
        engineering_spec="Test engineering spec",
        contracts=contracts or [],
        task_dag=dag,
        execution_batches=batches if batches is not None else [repos],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPlanExecutionBridge:
    def setup_method(self):
        self.org_id = uuid4()
        self.project_id = uuid4()
        self.leader_id = uuid4()
        self.repo_id = uuid4()
        self.team_leader_id = uuid4()

        self.topology = _make_topology(
            self.org_id,
            self.project_id,
            self.leader_id,
            [(self.repo_id, self.team_leader_id)],
        )
        # catalog maps repo name → repo UUID
        self.catalog = StubCatalog({"ts-order-service": self.repo_id})

    async def test_creates_engineering_spec(self):
        specs = StubSpecService()
        plans = StubPlanStarter()
        topo = StubTopologyReader(self.topology)

        bridge = PlanExecutionBridge(specs, plans, topo, self.catalog)
        plan = _make_plan(["ts-order-service"])

        await bridge.materialize(
            plan=plan,
            requirement="fix order bug",
            project_id=self.project_id,
            leader_agent_id=self.leader_id,
            idempotency_prefix="test-001",
        )

        assert len(specs.calls) >= 1
        eng_cmd = specs.calls[0][0]
        assert eng_cmd.kind == SpecificationKind.ENGINEERING
        assert eng_cmd.created_by_agent_id == self.leader_id

    async def test_creates_contract_specs(self):
        specs = StubSpecService()
        plans = StubPlanStarter()
        topo = StubTopologyReader(self.topology)

        bridge = PlanExecutionBridge(specs, plans, topo, self.catalog)
        plan = _make_plan(
            ["ts-a", "ts-b"],
            contracts=[
                ContractSpec(
                    producer="ts-a",
                    consumer="ts-b",
                    interface="POST /api/v1/foo",
                    agreement="ts-a guarantees response format",
                ),
            ],
        )

        await bridge.materialize(
            plan=plan,
            requirement="test",
            project_id=self.project_id,
            leader_agent_id=self.leader_id,
            idempotency_prefix="test-002",
        )

        # 1 engineering + 1 contract = 2 spec calls
        assert len(specs.calls) == 2
        contract_cmd = specs.calls[1][0]
        assert contract_cmd.kind == SpecificationKind.CONTRACT
        assert "ts-a" in contract_cmd.title
        assert "ts-b" in contract_cmd.title

    async def test_planned_task_with_name_to_uuid_mapping(self):
        """Repo name resolves to UUID via catalog + topology → planned task."""
        specs = StubSpecService()
        plans = StubPlanStarter()
        topo = StubTopologyReader(self.topology)

        bridge = PlanExecutionBridge(specs, plans, topo, self.catalog)
        plan = _make_plan(["ts-order-service"])

        result = await bridge.materialize(
            plan=plan,
            requirement="test",
            project_id=self.project_id,
            leader_agent_id=self.leader_id,
            idempotency_prefix="test-004",
        )

        assert len(plans.calls) == 1
        org_id, project_id, created_by, batches, key = plans.calls[0]
        assert org_id == self.org_id
        assert project_id == self.project_id
        assert created_by == self.leader_id
        assert key == "test-004-plan"
        assert len(batches) == 1
        planned = batches[0][0]
        assert planned.repository_id == self.repo_id
        assert planned.title == "Implement changes for ts-order-service"
        assert planned.instruction == "change ts-order-service"
        assert planned.leader_task_id is None
        assert len(result.skipped_repos) == 0
        assert result.plan_id == UUID(int=7777)

    async def test_planned_task_carries_the_verification_commands(self):
        """The task DAG's tests reach the plan: they become the Runner commands."""
        specs = StubSpecService()
        plans = StubPlanStarter()
        topo = StubTopologyReader(self.topology)

        bridge = PlanExecutionBridge(specs, plans, topo, self.catalog)
        plan = _make_plan(["ts-order-service"], tests=("python scripts/run_tests.py",))

        await bridge.materialize(
            plan=plan,
            requirement="test",
            project_id=self.project_id,
            leader_agent_id=self.leader_id,
            idempotency_prefix="test-tests",
        )

        batches = plans.calls[0][3]
        assert batches[0][0].tests == ("python scripts/run_tests.py",)

    async def test_planned_task_without_verification_commands_stays_empty(self):
        """A plan whose DAG carries no commands still materialises."""
        specs = StubSpecService()
        plans = StubPlanStarter()
        topo = StubTopologyReader(self.topology)

        bridge = PlanExecutionBridge(specs, plans, topo, self.catalog)

        await bridge.materialize(
            plan=_make_plan(["ts-order-service"]),
            requirement="test",
            project_id=self.project_id,
            leader_agent_id=self.leader_id,
            idempotency_prefix="test-no-tests",
        )

        assert plans.calls[0][3][0][0].tests == ()

    async def test_task_skipped_when_repo_not_in_catalog(self):
        """Repo name not in catalog → skipped."""
        specs = StubSpecService()
        plans = StubPlanStarter()
        topo = StubTopologyReader(self.topology)

        bridge = PlanExecutionBridge(specs, plans, topo, self.catalog)
        plan = _make_plan(["ts-unknown-service"])

        result = await bridge.materialize(
            plan=plan,
            requirement="test",
            project_id=self.project_id,
            leader_agent_id=self.leader_id,
            idempotency_prefix="test-003",
        )

        assert len(specs.calls) == 1
        assert len(plans.calls) == 0
        assert "ts-unknown-service" in result.skipped_repos
        assert result.plan_id is None

    async def test_task_skipped_when_repo_not_in_topology(self):
        """Repo in catalog but not in topology → skipped."""
        specs = StubSpecService()
        plans = StubPlanStarter()
        topo = StubTopologyReader(self.topology)

        extra_repo_id = uuid4()
        catalog_with_extra = StubCatalog({
            "ts-order-service": self.repo_id,
            "ts-extra-service": extra_repo_id,  # in catalog but not in topology
        })

        bridge = PlanExecutionBridge(specs, plans, topo, catalog_with_extra)
        plan = _make_plan(["ts-extra-service"])

        result = await bridge.materialize(
            plan=plan,
            requirement="test",
            project_id=self.project_id,
            leader_agent_id=self.leader_id,
            idempotency_prefix="test-005",
        )

        assert len(plans.calls) == 0
        assert "ts-extra-service" in result.skipped_repos

    async def test_unexecutable_repo_is_filtered_out_of_the_plan(self):
        """A batch keeps only the repositories the platform can execute."""
        specs = StubSpecService()
        plans = StubPlanStarter()
        topo = StubTopologyReader(self.topology)

        bridge = PlanExecutionBridge(specs, plans, topo, self.catalog)
        plan = _make_plan(
            ["ts-order-service", "ts-unknown-service"],
            batches=[["ts-order-service", "ts-unknown-service"]],
        )

        result = await bridge.materialize(
            plan=plan,
            requirement="test",
            project_id=self.project_id,
            leader_agent_id=self.leader_id,
            idempotency_prefix="test-filter",
        )

        batches = plans.calls[0][3]
        assert len(batches) == 1
        assert [planned.repository_id for planned in batches[0]] == [self.repo_id]
        assert result.skipped_repos == ["ts-unknown-service"]

    async def test_materialize_without_task_orchestrator_fails_closed(self):
        """No execution plane configured → refuse before any side effect.

        The former "specs only" skip mode returned a 200-shaped result whose
        ``task_ids`` was empty — indistinguishable from success in live use.
        """
        specs = StubSpecService()
        topo = StubTopologyReader(self.topology)

        bridge = PlanExecutionBridge(specs, None, topo, self.catalog)
        plan = _make_plan(["ts-order-service"])

        with pytest.raises(ExecutionPlaneUnavailable):
            await bridge.materialize(
                plan=plan,
                requirement="test",
                project_id=self.project_id,
                leader_agent_id=self.leader_id,
                idempotency_prefix="test-skip",
            )

        assert specs.calls == []  # fail-closed means no partial state either

    async def test_idempotency_keys_unique(self):
        specs = StubSpecService()
        plans = StubPlanStarter()
        topo = StubTopologyReader(self.topology)

        bridge = PlanExecutionBridge(specs, plans, topo, self.catalog)
        plan = _make_plan(
            ["ts-a", "ts-b"],
            contracts=[
                ContractSpec("ts-a", "ts-b", "API", "ok"),
            ],
        )

        await bridge.materialize(
            plan=plan,
            requirement="test",
            project_id=self.project_id,
            leader_agent_id=self.leader_id,
            idempotency_prefix="tt-001",
        )

        keys = [call[1] for call in specs.calls]
        assert len(keys) == len(set(keys)), "idempotency keys must be unique"

    async def test_empty_plan(self):
        specs = StubSpecService()
        plans = StubPlanStarter()
        topo = StubTopologyReader(self.topology)

        bridge = PlanExecutionBridge(specs, plans, topo, self.catalog)
        plan = IntegratedPlan(
            engineering_spec="",
            contracts=[],
            task_dag=[],
            execution_batches=[],
        )

        result = await bridge.materialize(
            plan=plan,
            requirement="empty",
            project_id=self.project_id,
            leader_agent_id=self.leader_id,
            idempotency_prefix="test-empty",
        )

        # Engineering spec still created (even if empty)
        assert len(specs.calls) == 1
        assert len(plans.calls) == 0
        assert result.plan_id is None

    async def test_topology_not_found_raises(self):
        specs = StubSpecService()
        plans = StubPlanStarter()

        class NullTopology:
            async def get_view(self, project_id):
                return None

        bridge = PlanExecutionBridge(specs, plans, NullTopology(), self.catalog)
        plan = _make_plan(["ts-a"])

        try:
            await bridge.materialize(
                plan=plan,
                requirement="test",
                project_id=self.project_id,
                leader_agent_id=self.leader_id,
                idempotency_prefix="test-null",
            )
            raise AssertionError("Should have raised")
        except ValueError as e:
            assert "topology" in str(e).lower()

    async def test_materialization_result_structure(self):
        specs = StubSpecService()
        plans = StubPlanStarter()
        topo = StubTopologyReader(self.topology)

        bridge = PlanExecutionBridge(specs, plans, topo, self.catalog)
        plan = _make_plan(["ts-order-service"])

        result = await bridge.materialize(
            plan=plan,
            requirement="test",
            project_id=self.project_id,
            leader_agent_id=self.leader_id,
            idempotency_prefix="test-result",
        )

        assert isinstance(result, MaterializationResult)
        assert result.engineering_spec is not None
        assert isinstance(result.contract_specs, list)
        assert isinstance(result.tasks, list)
        assert isinstance(result.skipped_repos, list)
        assert result.plan_id is not None


# ---------------------------------------------------------------------------
# Advancer-backed path (real task_orchestration application services)
# ---------------------------------------------------------------------------


class FakeAgentDirectory:
    def __init__(self, principals: tuple[AgentPrincipalView, ...]) -> None:
        self._principals = {principal.id: principal for principal in principals}

    async def get_view(self, agent_id: UUID) -> AgentPrincipalView | None:
        return self._principals.get(agent_id)


class RecordingAssigner:
    """Persist assignments like TaskOrchestrator does, without chat delivery."""

    def __init__(self, tasks: InMemoryTaskStore) -> None:
        self._tasks = tasks
        self.commands: list[tuple[AssignTaskCommand, str]] = []

    async def assign(self, command: AssignTaskCommand, *, idempotency_key: str) -> TaskView:
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
            task, idempotency_key=idempotency_key, request_fingerprint="sha256:test"
        )
        return task.to_view()


class ExecutionEnvironment:
    """Bridge wired to the real execution plan services through in-memory stores."""

    def __init__(self, repository_names: list[str]) -> None:
        self.organization_id = uuid4()
        self.project_id = uuid4()
        self.organization_leader_id = uuid4()
        self.repository_names = repository_names
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
        for index, _name in enumerate(repository_names):
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
        self.topologies = StubTopologyReader(
            ProjectAgentTopologyView(
                id=uuid4(),
                organization_id=self.organization_id,
                project_id=self.project_id,
                organization_leader_id=self.organization_leader_id,
                repository_teams=tuple(teams),
            )
        )
        self.catalog = StubCatalog(dict(zip(repository_names, self.repository_ids, strict=True)))
        self.directory = FakeAgentDirectory(tuple(principals))
        self.tasks = InMemoryTaskStore()
        self.plans = InMemoryExecutionPlanStore()
        self.assigner = RecordingAssigner(self.tasks)
        self.advancer = AdvanceExecutionPlan(
            self.plans,
            self.tasks,
            self.assigner,
            DecomposeRepositoryTask(
                self.directory, self.topologies, self.tasks, self.assigner
            ),
        )
        self.specs = StubSpecService()
        self.bridge = PlanExecutionBridge(
            self.specs,
            AdvanceExecutionPlanStarter(self.advancer, self.tasks),
            self.topologies,
            self.catalog,
        )

    async def materialize(
        self,
        batches: list[list[str]],
        *,
        idempotency_prefix: str = "bridge",
        tests: tuple[str, ...] = (),
    ) -> MaterializationResult:
        return await self.bridge.materialize(
            plan=_make_plan(self.repository_names, batches=batches, tests=tests),
            requirement="deliver the approved scope",
            project_id=self.project_id,
            leader_agent_id=self.organization_leader_id,
            idempotency_prefix=idempotency_prefix,
        )


async def test_materialize_starts_a_plan_and_assigns_only_the_first_batch() -> None:
    environment = ExecutionEnvironment(["ts-a", "ts-b"])

    result = await environment.materialize([["ts-a"], ["ts-b"]])

    assert result.plan_id is not None
    stored = await environment.plans.get(result.plan_id)
    assert stored is not None
    assert stored.status is ExecutionPlanStatus.IN_PROGRESS
    assert stored.current_batch_index == 0
    leader_task_id = stored.batches[0][0].leader_task_id
    assert leader_task_id is not None
    # The second batch stays unassigned until the Runner reports batch 0.
    assert stored.batches[1][0].leader_task_id is None
    assert all(
        command.repository_id != environment.repository_ids[1]
        for command, _ in environment.assigner.commands
    )


async def test_materialize_decomposes_the_first_batch_into_worker_tasks() -> None:
    environment = ExecutionEnvironment(["ts-a"])

    result = await environment.materialize([["ts-a"]])

    assert result.plan_id is not None
    stored = await environment.plans.get(result.plan_id)
    assert stored is not None
    leader_task_id = stored.batches[0][0].leader_task_id
    assert leader_task_id is not None
    leader_task = await environment.tasks.get(leader_task_id)
    assert leader_task is not None
    assert leader_task.assignee_agent_id == environment.leader_ids[0]
    assert leader_task.assigned_by_agent_id == environment.organization_leader_id
    assert leader_task.title == "Implement changes for ts-a"
    workers = await environment.tasks.list_by_parent(leader_task_id)
    assert len(workers) == 1
    assert workers[0].assignee_agent_id == environment.worker_ids[0]
    assert workers[0].status is TaskStatus.ASSIGNED


async def test_materialize_result_reports_the_assigned_leader_and_worker_tasks() -> None:
    environment = ExecutionEnvironment(["ts-a"])

    result = await environment.materialize([["ts-a"]])

    stored = await environment.plans.get(result.plan_id)
    assert stored is not None
    leader_task_id = stored.batches[0][0].leader_task_id
    workers = await environment.tasks.list_by_parent(leader_task_id)
    assert {task.id for task in result.tasks} == {leader_task_id, workers[0].id}


async def test_replan_supersedes_only_tasks_in_affected_repositories() -> None:
    project_id = uuid4()
    affected_repository_id = uuid4()
    unaffected_repository_id = uuid4()
    affected = _task_view(
        project_id=project_id, repository_id=affected_repository_id
    )
    unaffected = _task_view(
        project_id=project_id, repository_id=unaffected_repository_id
    )
    tasks = (affected, unaffected)
    superseder = RecordingSuperseder(tasks)
    topology = ProjectAgentTopologyView(
        id=uuid4(),
        organization_id=uuid4(),
        project_id=project_id,
        organization_leader_id=uuid4(),
        repository_teams=(),
    )
    bridge = PlanExecutionBridge(
        StubSpecService(),
        StubPlanStarter(),
        StubTopologyReader(topology),
        StubCatalog(
            {
                "affected": affected_repository_id,
                "unaffected": unaffected_repository_id,
            }
        ),
        superseder=superseder,
        task_reader=StubProjectTaskReader(tasks),
    )

    result = await bridge.replan(
        project_id=project_id,
        leader_agent_id=topology.organization_leader_id,
        feedback="the public contract changed",
        change_source_repo="affected",
        plan_version=1,
        requirement="implement the feature",
        idempotency_prefix="replan-2",
        all_repos=["affected", "unaffected"],
    )

    assert [task.id for task in result.superseded_tasks] == [affected.id]
    assert [call[0].task_id for call in superseder.calls] == [affected.id]
    assert superseder.calls[0][1] == f"replan-2:supersede:{affected.id}"


async def test_materialize_stores_the_verification_commands_on_the_planned_task() -> None:
    """The composition root carries ``tests`` into the execution plan aggregate."""

    environment = ExecutionEnvironment(["ts-a"])

    result = await environment.materialize(
        [["ts-a"]], tests=("python scripts/run_tests.py",)
    )

    assert result.plan_id is not None
    stored = await environment.plans.get(result.plan_id)
    assert stored is not None
    assert stored.batches[0][0].tests == ("python scripts/run_tests.py",)


async def test_materialize_is_idempotent_for_the_same_prefix() -> None:
    environment = ExecutionEnvironment(["ts-a"])

    first = await environment.materialize([["ts-a"]], idempotency_prefix="tt-009")
    assignments = len(environment.assigner.commands)
    replay = await environment.materialize([["ts-a"]], idempotency_prefix="tt-009")

    assert replay.plan_id == first.plan_id
    assert len(environment.assigner.commands) == assignments
