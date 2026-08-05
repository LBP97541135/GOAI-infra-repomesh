from dataclasses import dataclass
from uuid import UUID

from repomesh.modules.agent_directory.contracts import AgentRole, RepositoryAgentTeamCreated
from repomesh.modules.agent_directory.ports import AgentDirectory

from .create import CreateAgent, CreateAgentRequest


@dataclass(frozen=True, slots=True)
class CreateRepositoryAgentTeamRequest:
    organization_id: UUID
    organization_leader_id: UUID
    repository_id: UUID
    leader_agentteams_resource_name: str
    worker_agentteams_resource_names: tuple[str, ...]
    leader_responsibility_paths: tuple[str, ...] = ("**",)
    worker_responsibility_paths: tuple[str, ...] = ("**",)

    def __post_init__(self) -> None:
        if not 1 <= len(self.worker_agentteams_resource_names) <= 20:
            raise ValueError("worker resources must contain between 1 and 20 names")
        resources = (
            self.leader_agentteams_resource_name,
            *self.worker_agentteams_resource_names,
        )
        if any(not name.strip() for name in resources):
            raise ValueError("AgentTeams resource names are required")
        if len(set(resources)) != len(resources):
            raise ValueError("AgentTeams resource names must be unique")


class CreateRepositoryAgentTeam:
    """Register business principals for an existing AgentTeams repository team."""

    def __init__(self, directory: AgentDirectory) -> None:
        self._create = CreateAgent(directory)

    async def execute(
        self,
        request: CreateRepositoryAgentTeamRequest,
        *,
        idempotency_key: str,
    ) -> RepositoryAgentTeamCreated:
        key = idempotency_key.strip()
        if not key:
            raise ValueError("idempotency_key is required")
        leader = await self._create.execute(
            CreateAgentRequest(
                organization_id=request.organization_id,
                role=AgentRole.REPOSITORY_LEADER,
                agentteams_resource_name=request.leader_agentteams_resource_name,
                leader_agent_id=request.organization_leader_id,
                repository_id=request.repository_id,
                responsibility_paths=request.leader_responsibility_paths,
            ),
            idempotency_key=f"{key}:leader",
        )
        workers = []
        for index, resource_name in enumerate(
            request.worker_agentteams_resource_names, start=1
        ):
            worker = await self._create.execute(
                CreateAgentRequest(
                    organization_id=request.organization_id,
                    role=AgentRole.WORKER,
                    agentteams_resource_name=resource_name,
                    leader_agent_id=leader.principal.id,
                    repository_id=request.repository_id,
                    responsibility_paths=request.worker_responsibility_paths,
                ),
                idempotency_key=f"{key}:worker:{index:02d}",
            )
            workers.append(worker.principal)
        return RepositoryAgentTeamCreated(
            repository_id=request.repository_id,
            leader=leader.principal,
            workers=tuple(workers),
        )
