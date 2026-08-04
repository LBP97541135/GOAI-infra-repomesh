from dataclasses import dataclass

from repomesh.modules.agent_directory.application import CreateAgent, CreateAgentRequest
from repomesh.modules.agent_directory.contracts import AgentCreated, AgentRole
from repomesh.modules.agent_directory.ports import AgentDirectory
from repomesh.modules.agent_runtime.ports.agent_team import (
    AgentTeamControlPlane,
    ManagerProjection,
    WorkerProjection,
)


@dataclass(frozen=True, slots=True)
class RegisterNativeAgentRequest:
    principal: CreateAgentRequest
    manager: ManagerProjection | None = None
    worker: WorkerProjection | None = None

    def __post_init__(self) -> None:
        if (self.manager is None) == (self.worker is None):
            raise ValueError("exactly one native Manager or Worker projection is required")
        projection = self.manager or self.worker
        if projection is None or projection.name != self.principal.agentteams_resource_name:
            raise ValueError("native resource name must match the principal binding")
        if self.principal.role is AgentRole.ORGANIZATION_LEADER and self.manager is None:
            raise ValueError("organization leader requires a native AgentTeams Manager")
        if self.principal.role is not AgentRole.ORGANIZATION_LEADER and self.worker is None:
            raise ValueError("repository leaders and workers require native AgentTeams Workers")


class RegisterNativeAgent:
    """Ensure an AgentTeams resource, then register only its RepoMesh business binding."""

    def __init__(
        self,
        control_plane: AgentTeamControlPlane,
        directory: AgentDirectory,
    ) -> None:
        self._control_plane = control_plane
        self._register = CreateAgent(directory)

    async def execute(
        self,
        request: RegisterNativeAgentRequest,
        *,
        idempotency_key: str,
    ) -> AgentCreated:
        key = idempotency_key.strip()
        if not key:
            raise ValueError("idempotency_key is required")
        if request.manager is not None:
            await self._control_plane.ensure_manager(
                request.manager,
                idempotency_key=f"{key}:agentteams",
            )
        elif request.worker is not None:
            await self._control_plane.ensure_worker(
                request.worker,
                idempotency_key=f"{key}:agentteams",
            )
        return await self._register.execute(
            request.principal,
            idempotency_key=f"{key}:principal",
        )
