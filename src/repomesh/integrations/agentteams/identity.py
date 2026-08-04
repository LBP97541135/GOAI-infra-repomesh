from repomesh.modules.agent_directory.contracts import AgentPrincipalView, AgentRole
from repomesh.modules.agent_runtime.ports.agent_team import AgentTeamControlPlane


class AgentTeamsMatrixIdentityVerifier:
    def __init__(self, control_plane: AgentTeamControlPlane) -> None:
        self._control_plane = control_plane

    async def verify(self, profile: AgentPrincipalView, matrix_user_id: str) -> bool:
        if profile.role is AgentRole.ORGANIZATION_LEADER:
            runtime = await self._control_plane.get_manager(
                profile.agentteams_resource_name
            )
        else:
            runtime = await self._control_plane.get_worker(profile.agentteams_resource_name)
        return runtime is not None and runtime.matrix_user_id == matrix_user_id
