from dataclasses import replace
from uuid import UUID

from repomesh.modules.agent_directory.contracts import AgentPrincipalReader
from repomesh.modules.agent_runtime.ports.agent_team import (
    AgentTeamControlPlane,
    TeamMemberProjection,
    TeamProjection,
    TeamRole,
)
from repomesh.modules.project.contracts import (
    ProjectAgentTopologyView,
    ProjectTeamRuntimeStatus,
)
from repomesh.modules.project.domain import ProjectTopologyViolation
from repomesh.modules.project.ports import ProjectTopologyStore


class ReconcileProjectAgentTopology:
    """Create AgentTeams Teams from already registered native Manager/Worker resources."""

    def __init__(
        self,
        directory: AgentPrincipalReader,
        store: ProjectTopologyStore,
        control_plane: AgentTeamControlPlane,
    ) -> None:
        self._directory = directory
        self._store = store
        self._control_plane = control_plane

    async def execute(self, project_id: UUID) -> ProjectAgentTopologyView:
        topology = await self._store.get(project_id)
        if topology is None:
            raise ProjectTopologyViolation(f"project topology does not exist: {project_id}")
        if await self._directory.get_view(topology.organization_leader_id) is None:
            raise ProjectTopologyViolation("organization leader binding does not exist")
        reconciled_teams = []
        for team in topology.repository_teams:
            members = []
            for agent_id, role in (
                (team.leader_agent_id, TeamRole.LEADER),
                *((worker_id, TeamRole.WORKER) for worker_id in team.worker_agent_ids),
            ):
                principal = await self._directory.get_view(agent_id)
                if principal is None:
                    raise ProjectTopologyViolation(f"agent binding does not exist: {agent_id}")
                members.append(
                    TeamMemberProjection(principal.agentteams_resource_name, role)
                )
            runtime_team = await self._control_plane.ensure_team(
                TeamProjection(
                    name=team.agentteams_team_name or "",
                    members=tuple(members),
                    description=(
                        f"RepoMesh project {project_id} repository {team.repository_id}"
                    ),
                ),
                idempotency_key=f"project:{project_id}:repository:{team.repository_id}:team",
            )
            reconciled_teams.append(
                team.with_runtime(
                    status=(
                        ProjectTeamRuntimeStatus.READY
                        if runtime_team.phase.lower() == "ready"
                        else ProjectTeamRuntimeStatus.PENDING
                    ),
                    room_id=runtime_team.team_room_id,
                    leader_room_id=runtime_team.leader_room_id,
                )
            )
        topology = replace(topology, repository_teams=tuple(reconciled_teams))
        await self._store.save(topology)
        return topology.to_view()
