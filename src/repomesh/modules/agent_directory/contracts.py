from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class AgentRole(StrEnum):
    ORGANIZATION_LEADER = "organization_leader"
    REPOSITORY_LEADER = "repository_leader"
    WORKER = "worker"


class AgentPrincipalStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class AgentPrincipalView:
    id: UUID
    organization_id: UUID
    role: AgentRole
    leader_agent_id: UUID | None
    repository_id: UUID | None
    responsibility_paths: tuple[str, ...]
    agentteams_resource_name: str
    status: AgentPrincipalStatus


class AgentPrincipalReader(Protocol):
    async def get_view(self, agent_id: UUID) -> AgentPrincipalView | None: ...


@dataclass(frozen=True, slots=True)
class AgentCreated:
    principal: AgentPrincipalView
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class RepositoryAgentTeamCreated:
    repository_id: UUID
    leader: AgentPrincipalView
    workers: tuple[AgentPrincipalView, ...]


class RepositoryAgentTeamProvisioner(Protocol):
    """Give a repository the leader-and-worker pair a project topology needs.

    Published as a contract because the caller is another module: building a
    topology (``project``) requires principals (``agent_directory``), and the
    alternative to this protocol is the project module importing this one's
    application layer.

    ``provision`` is *ensure*, not *create*: a repository's leader is a
    directory singleton (``repository:{id}:leader``), so a repository that
    already has a team must yield that team rather than a conflict. Two
    projects touching the same repository is ordinary, not an error.
    """

    async def provision(
        self,
        *,
        organization_id: UUID,
        organization_leader_id: UUID,
        repository_id: UUID,
        idempotency_key: str,
    ) -> RepositoryAgentTeamCreated: ...
