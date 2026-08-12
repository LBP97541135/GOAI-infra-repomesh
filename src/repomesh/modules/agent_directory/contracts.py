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

    async def list_views(self) -> tuple[AgentPrincipalView, ...]: ...


@dataclass(frozen=True, slots=True)
class AgentCreated:
    principal: AgentPrincipalView
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class RepositoryAgentTeamCreated:
    repository_id: UUID
    leader: AgentPrincipalView
    workers: tuple[AgentPrincipalView, ...]
