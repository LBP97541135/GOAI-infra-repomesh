"""Tests for PlanExecutionBridge."""

from __future__ import annotations

import sys
from pathlib import Path
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from repomesh.modules.project.contracts import (  # noqa: E402
    ProjectAgentTopologyView,
    ProjectTeamRuntimeStatus,
    RepositoryTeamView,
)
from repomesh.modules.repository_intelligence.application.plan_execution_bridge import (  # noqa: E402
    MaterializationResult,
    PlanExecutionBridge,
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
from repomesh.modules.task_orchestration.contracts import (  # noqa: E402
    TaskStatus,
    TaskView,
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


def _make_task_view(cmd, counter: int) -> TaskView:
    return TaskView(
        id=UUID(int=counter),
        organization_id=cmd.organization_id,
        project_id=cmd.project_id,
        repository_id=cmd.repository_id,
        parent_task_id=None,
        assigned_by_agent_id=cmd.assigned_by_agent_id,
        assignee_agent_id=cmd.assignee_agent_id,
        title=cmd.title,
        instruction=cmd.instruction,
        acceptance=cmd.acceptance,
        status=TaskStatus.ASSIGNED,
        result_summary=None,
        version=1,
    )


class StubTaskOrchestrator:
    def __init__(self) -> None:
        self.calls: list = []
        self._counter = 1000

    async def assign(self, command, *, idempotency_key):
        self._counter += 1
        self.calls.append((command, idempotency_key))
        return _make_task_view(command, self._counter)


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
) -> IntegratedPlan:
    dag = [TaskNode(repository=r, instruction=f"change {r}") for r in repos]
    return IntegratedPlan(
        engineering_spec="Test engineering spec",
        contracts=contracts or [],
        task_dag=dag,
        execution_batches=[repos],
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
        tasks = StubTaskOrchestrator()
        topo = StubTopologyReader(self.topology)

        bridge = PlanExecutionBridge(specs, tasks, topo, self.catalog)
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
        tasks = StubTaskOrchestrator()
        topo = StubTopologyReader(self.topology)

        bridge = PlanExecutionBridge(specs, tasks, topo, self.catalog)
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

    async def test_task_assignment_with_name_to_uuid_mapping(self):
        """Repo name resolves to UUID via catalog + topology → Task created."""
        specs = StubSpecService()
        tasks = StubTaskOrchestrator()
        topo = StubTopologyReader(self.topology)

        bridge = PlanExecutionBridge(specs, tasks, topo, self.catalog)
        plan = _make_plan(["ts-order-service"])

        result = await bridge.materialize(
            plan=plan,
            requirement="test",
            project_id=self.project_id,
            leader_agent_id=self.leader_id,
            idempotency_prefix="test-004",
        )

        assert len(tasks.calls) == 1
        cmd = tasks.calls[0][0]
        assert cmd.repository_id == self.repo_id
        assert cmd.assignee_agent_id == self.team_leader_id
        assert len(result.skipped_repos) == 0

    async def test_task_skipped_when_repo_not_in_catalog(self):
        """Repo name not in catalog → skipped."""
        specs = StubSpecService()
        tasks = StubTaskOrchestrator()
        topo = StubTopologyReader(self.topology)

        bridge = PlanExecutionBridge(specs, tasks, topo, self.catalog)
        plan = _make_plan(["ts-unknown-service"])

        result = await bridge.materialize(
            plan=plan,
            requirement="test",
            project_id=self.project_id,
            leader_agent_id=self.leader_id,
            idempotency_prefix="test-003",
        )

        assert len(specs.calls) == 1
        assert len(tasks.calls) == 0
        assert "ts-unknown-service" in result.skipped_repos

    async def test_task_skipped_when_repo_not_in_topology(self):
        """Repo in catalog but not in topology → skipped."""
        specs = StubSpecService()
        tasks = StubTaskOrchestrator()
        topo = StubTopologyReader(self.topology)

        extra_repo_id = uuid4()
        catalog_with_extra = StubCatalog({
            "ts-order-service": self.repo_id,
            "ts-extra-service": extra_repo_id,  # in catalog but not in topology
        })

        bridge = PlanExecutionBridge(specs, tasks, topo, catalog_with_extra)
        plan = _make_plan(["ts-extra-service"])

        result = await bridge.materialize(
            plan=plan,
            requirement="test",
            project_id=self.project_id,
            leader_agent_id=self.leader_id,
            idempotency_prefix="test-005",
        )

        assert len(tasks.calls) == 0
        assert "ts-extra-service" in result.skipped_repos

    async def test_idempotency_keys_unique(self):
        specs = StubSpecService()
        tasks = StubTaskOrchestrator()
        topo = StubTopologyReader(self.topology)

        bridge = PlanExecutionBridge(specs, tasks, topo, self.catalog)
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
        tasks = StubTaskOrchestrator()
        topo = StubTopologyReader(self.topology)

        bridge = PlanExecutionBridge(specs, tasks, topo, self.catalog)
        plan = IntegratedPlan(
            engineering_spec="",
            contracts=[],
            task_dag=[],
            execution_batches=[],
        )

        await bridge.materialize(
            plan=plan,
            requirement="empty",
            project_id=self.project_id,
            leader_agent_id=self.leader_id,
            idempotency_prefix="test-empty",
        )

        # Engineering spec still created (even if empty)
        assert len(specs.calls) == 1
        assert len(tasks.calls) == 0

    async def test_topology_not_found_raises(self):
        specs = StubSpecService()
        tasks = StubTaskOrchestrator()

        class NullTopology:
            async def get_view(self, project_id):
                return None

        bridge = PlanExecutionBridge(specs, tasks, NullTopology(), self.catalog)
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
        tasks = StubTaskOrchestrator()
        topo = StubTopologyReader(self.topology)

        bridge = PlanExecutionBridge(specs, tasks, topo, self.catalog)
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
