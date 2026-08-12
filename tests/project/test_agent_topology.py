from uuid import uuid4

import pytest

from repomesh.integrations.agentteams.project_topology import ReconcileProjectAgentTopology
from repomesh.modules.agent_directory.application import (
    CreateAgent,
    CreateAgentRequest,
    CreateRepositoryAgentTeam,
    CreateRepositoryAgentTeamRequest,
)
from repomesh.modules.agent_directory.contracts import AgentRole
from repomesh.modules.agent_directory.infrastructure import InMemoryAgentDirectory
from repomesh.modules.agent_runtime.ports.agent_team import (
    ManagerRuntimeRef,
    TeamProjection,
    TeamRuntimeRef,
    WorkerRuntimeRef,
)
from repomesh.modules.project import (
    CodeAccessLevel,
    CreateAutomaticProjectTopology,
    CreateAutomaticProjectTopologyRequest,
    CreateProjectAgentTopology,
    CreateProjectAgentTopologyRequest,
    HumanAuthorizationRequest,
    HumanControlAction,
    HumanProjectGrantInput,
    HumanProjectRole,
    ProjectCheckpoint,
    ProjectExecutionMode,
    RepositoryTeamAssignment,
    authorize_human,
    requires_human_checkpoint,
)
from repomesh.modules.project.domain import ProjectTopologyConflict, ProjectTopologyViolation
from repomesh.modules.project.infrastructure import InMemoryProjectTopologyStore


@pytest.mark.asyncio
async def test_automatic_topology_resolves_long_lived_repository_team() -> None:
    directory, organization_id, organization_leader, teams = await build_agents(1)
    store = InMemoryProjectTopologyStore()
    service = CreateAutomaticProjectTopology(
        directory,
        CreateProjectAgentTopology(directory, store),
    )

    first = await service.execute(
        CreateAutomaticProjectTopologyRequest(
            organization_id=organization_id,
            project_id=uuid4(),
            repository_ids=(teams[0].repository_id,),
        ),
        idempotency_key="automatic-project-one",
    )
    second = await service.execute(
        CreateAutomaticProjectTopologyRequest(
            organization_id=organization_id,
            project_id=uuid4(),
            repository_ids=(teams[0].repository_id,),
        ),
        idempotency_key="automatic-project-two",
    )

    first_team = first.repository_teams[0]
    second_team = second.repository_teams[0]
    assert first.organization_leader_id == organization_leader.id
    assert first_team.leader_agent_id == teams[0].leader.id
    assert first_team.worker_agent_ids == tuple(
        sorted((worker.id for worker in teams[0].workers), key=str)
    )
    assert first_team.agentteams_team_name == second_team.agentteams_team_name
    assert first_team.agentteams_team_name == f"rm-repo-{teams[0].repository_id.hex}"


@pytest.mark.asyncio
async def test_automatic_topology_rejects_repository_without_registered_team() -> None:
    directory, organization_id, _, _ = await build_agents(1)
    service = CreateAutomaticProjectTopology(
        directory,
        CreateProjectAgentTopology(directory, InMemoryProjectTopologyStore()),
    )

    with pytest.raises(ProjectTopologyViolation, match="repository leader"):
        await service.execute(
            CreateAutomaticProjectTopologyRequest(
                organization_id=organization_id,
                project_id=uuid4(),
                repository_ids=(uuid4(),),
            ),
            idempotency_key="automatic-missing-repository",
        )


@pytest.mark.asyncio
async def test_supervised_project_persists_human_scope_and_permissions() -> None:
    organization_id = uuid4()
    project_id = uuid4()
    repository_id = uuid4()
    human_id = uuid4()
    directory, organization_id, organization_leader, teams = await build_agents(1)
    repository_id = teams[0].repository_id
    repository_leader = teams[0].leader
    worker = teams[0].workers[0]
    service = CreateProjectAgentTopology(directory, InMemoryProjectTopologyStore())

    topology = await service.execute(
        CreateProjectAgentTopologyRequest(
            organization_id=organization_id,
            project_id=project_id,
            organization_leader_id=organization_leader.id,
            repository_teams=(
                RepositoryTeamAssignment(
                    repository_id,
                    repository_leader.id,
                    (worker.id,),
                ),
            ),
            execution_mode=ProjectExecutionMode.SUPERVISED,
            required_checkpoints=frozenset(
                {ProjectCheckpoint.SPECIFICATION, ProjectCheckpoint.DELIVERY}
            ),
            human_grants=(
                HumanProjectGrantInput(
                    human_principal_id=human_id,
                    role=HumanProjectRole.REPOSITORY_SUPERVISOR,
                    code_access=CodeAccessLevel.READ,
                    control_actions=frozenset(
                        {
                            HumanControlAction.VIEW_DECISIONS,
                            HumanControlAction.APPROVE_CHECKPOINT,
                            HumanControlAction.REQUEST_CHANGES,
                        }
                    ),
                    repository_id=repository_id,
                    path_patterns=("src/**",),
                ),
            ),
        ),
        idempotency_key="supervised-project",
    )

    assert requires_human_checkpoint(topology, ProjectCheckpoint.DELIVERY)
    assert authorize_human(
        topology,
        HumanAuthorizationRequest(
            human_principal_id=human_id,
            repository_id=repository_id,
            path="src/pricing.py",
            code_access=CodeAccessLevel.READ,
        ),
    ).allowed
    assert not authorize_human(
        topology,
        HumanAuthorizationRequest(
            human_principal_id=human_id,
            repository_id=repository_id,
            path="src/pricing.py",
            code_access=CodeAccessLevel.WRITE,
        ),
    ).allowed


@pytest.mark.asyncio
async def test_manual_controlled_project_requires_every_checkpoint() -> None:
    directory, organization_id, organization_leader, teams = await build_agents(1)
    team = teams[0]
    base = dict(
        organization_id=organization_id,
        project_id=uuid4(),
        organization_leader_id=organization_leader.id,
        repository_teams=(
            RepositoryTeamAssignment(
                team.repository_id, team.leader.id, (team.workers[0].id,)
            ),
        ),
        execution_mode=ProjectExecutionMode.MANUAL_CONTROLLED,
        human_grants=(
            HumanProjectGrantInput(
                human_principal_id=uuid4(),
                role=HumanProjectRole.PROJECT_SUPERVISOR,
                code_access=CodeAccessLevel.READ,
                control_actions=frozenset({HumanControlAction.APPROVE_CHECKPOINT}),
            ),
        ),
    )
    service = CreateProjectAgentTopology(directory, InMemoryProjectTopologyStore())
    with pytest.raises(ProjectTopologyViolation, match="every human checkpoint"):
        await service.execute(
            CreateProjectAgentTopologyRequest(
                **base,
                required_checkpoints=frozenset({ProjectCheckpoint.EXECUTION}),
            ),
            idempotency_key="incomplete-manual-project",
        )

    accepted = await service.execute(
        CreateProjectAgentTopologyRequest(
            **{**base, "project_id": uuid4()},
            required_checkpoints=frozenset(ProjectCheckpoint),
        ),
        idempotency_key="complete-manual-project",
    )
    assert accepted.required_checkpoints == frozenset(ProjectCheckpoint)


async def build_agents(repository_count: int = 2):
    directory = InMemoryAgentDirectory()
    create = CreateAgent(directory)
    organization_id = uuid4()
    organization_leader = await create.execute(
        CreateAgentRequest(
            organization_id=organization_id,
            role=AgentRole.ORGANIZATION_LEADER,
            agentteams_resource_name="native-project-manager",
        ),
        idempotency_key="organization-leader",
    )
    teams = []
    for index in range(repository_count):
        repository_id = uuid4()
        team = await CreateRepositoryAgentTeam(directory).execute(
            CreateRepositoryAgentTeamRequest(
                organization_id=organization_id,
                organization_leader_id=organization_leader.principal.id,
                repository_id=repository_id,
                leader_agentteams_resource_name=f"native-repository-{index}-leader",
                worker_agentteams_resource_names=(
                    f"native-repository-{index}-worker-01",
                    f"native-repository-{index}-worker-02",
                ),
            ),
            idempotency_key=f"repository-team-{index}",
        )
        teams.append(team)
    return directory, organization_id, organization_leader.principal, teams


@pytest.mark.asyncio
async def test_project_creates_one_agentteams_team_per_repository() -> None:
    directory, organization_id, organization_leader, teams = await build_agents()
    store = InMemoryProjectTopologyStore()
    project_id = uuid4()
    request = CreateProjectAgentTopologyRequest(
        organization_id=organization_id,
        project_id=project_id,
        organization_leader_id=organization_leader.id,
        repository_teams=tuple(
            RepositoryTeamAssignment(
                repository_id=team.repository_id,
                leader_agent_id=team.leader.id,
                worker_agent_ids=tuple(worker.id for worker in team.workers),
            )
            for team in teams
        ),
    )

    first = await CreateProjectAgentTopology(directory, store).execute(
        request, idempotency_key=f"project:{project_id}:topology"
    )
    replayed = await CreateProjectAgentTopology(directory, store).execute(
        request, idempotency_key=f"project:{project_id}:topology"
    )

    assert first == replayed
    assert len(first.repository_teams) == 2
    assert len({team.agentteams_team_name for team in first.repository_teams}) == 2


@pytest.mark.asyncio
async def test_project_rejects_worker_from_another_repository() -> None:
    directory, organization_id, organization_leader, teams = await build_agents()
    with pytest.raises(ProjectTopologyViolation, match="another repository"):
        await CreateProjectAgentTopology(directory, InMemoryProjectTopologyStore()).execute(
            CreateProjectAgentTopologyRequest(
                organization_id=organization_id,
                project_id=uuid4(),
                organization_leader_id=organization_leader.id,
                repository_teams=(
                    RepositoryTeamAssignment(
                        repository_id=teams[0].repository_id,
                        leader_agent_id=teams[0].leader.id,
                        worker_agent_ids=(teams[1].workers[0].id,),
                    ),
                ),
            ),
            idempotency_key="invalid-cross-repository-team",
        )


@pytest.mark.asyncio
async def test_project_rejects_changed_idempotent_topology() -> None:
    directory, organization_id, organization_leader, teams = await build_agents()
    store = InMemoryProjectTopologyStore()
    project_id = uuid4()
    request = CreateProjectAgentTopologyRequest(
        organization_id=organization_id,
        project_id=project_id,
        organization_leader_id=organization_leader.id,
        repository_teams=(
            RepositoryTeamAssignment(
                repository_id=teams[0].repository_id,
                leader_agent_id=teams[0].leader.id,
                worker_agent_ids=(teams[0].workers[0].id,),
            ),
        ),
    )
    service = CreateProjectAgentTopology(directory, store)
    await service.execute(request, idempotency_key="stable-project-topology")

    with pytest.raises(ProjectTopologyConflict, match="different project topology"):
        await service.execute(
            CreateProjectAgentTopologyRequest(
                organization_id=organization_id,
                project_id=project_id,
                organization_leader_id=organization_leader.id,
                repository_teams=(
                    RepositoryTeamAssignment(
                        repository_id=teams[0].repository_id,
                        leader_agent_id=teams[0].leader.id,
                        worker_agent_ids=tuple(worker.id for worker in teams[0].workers),
                    ),
                ),
            ),
            idempotency_key="stable-project-topology",
        )


class RecordingControlPlane:
    def __init__(self) -> None:
        self.managers: list[str] = []
        self.workers: list[str] = []
        self.teams: list[TeamProjection] = []

    async def ensure_manager(self, projection, *, idempotency_key: str) -> ManagerRuntimeRef:
        self.managers.append(projection.name)
        return ManagerRuntimeRef(projection.name, "Ready")

    async def ensure_worker(self, projection, *, idempotency_key: str) -> WorkerRuntimeRef:
        self.workers.append(projection.name)
        return WorkerRuntimeRef(projection.name, "Ready")

    async def ensure_team(
        self, projection: TeamProjection, *, idempotency_key: str
    ) -> TeamRuntimeRef:
        self.teams.append(projection)
        return TeamRuntimeRef(
            name=projection.name,
            phase="Ready",
            team_room_id=f"!{projection.name}:matrix.local",
            leader_room_id=f"!leader-{projection.name}:matrix.local",
            leader_name=projection.members[0].name,
            ready_workers=len(projection.members),
            total_workers=len(projection.members),
        )

    async def get_worker(self, name: str) -> WorkerRuntimeRef | None:
        return None

    async def ensure_worker_ready(
        self, name: str, *, idempotency_key: str
    ) -> WorkerRuntimeRef:
        return WorkerRuntimeRef(name, "Ready")


@pytest.mark.asyncio
async def test_reconcile_provisions_manager_workers_and_repository_teams() -> None:
    directory, organization_id, organization_leader, teams = await build_agents()
    store = InMemoryProjectTopologyStore()
    project_id = uuid4()
    await CreateProjectAgentTopology(directory, store).execute(
        CreateProjectAgentTopologyRequest(
            organization_id=organization_id,
            project_id=project_id,
            organization_leader_id=organization_leader.id,
            repository_teams=tuple(
                RepositoryTeamAssignment(
                    repository_id=team.repository_id,
                    leader_agent_id=team.leader.id,
                    worker_agent_ids=tuple(worker.id for worker in team.workers),
                )
                for team in teams
            ),
        ),
        idempotency_key="reconciled-project",
    )
    control_plane = RecordingControlPlane()

    result = await ReconcileProjectAgentTopology(
        directory, store, control_plane  # type: ignore[arg-type]
    ).execute(project_id)

    assert len(control_plane.managers) == 0
    assert len(control_plane.workers) == 0
    assert len(control_plane.teams) == 2
    assert all(team.members[0].role.value == "team_leader" for team in control_plane.teams)
    assert all(team.runtime_status.value == "ready" for team in result.repository_teams)
    assert all(team.room_id for team in result.repository_teams)
