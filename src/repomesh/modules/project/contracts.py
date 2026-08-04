from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class ProjectTeamRuntimeStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RepositoryTeamView:
    id: UUID
    project_id: UUID
    repository_id: UUID
    leader_agent_id: UUID
    worker_agent_ids: tuple[UUID, ...]
    agentteams_team_name: str
    runtime_status: ProjectTeamRuntimeStatus
    room_id: str | None
    leader_room_id: str | None


@dataclass(frozen=True, slots=True)
class ProjectAgentTopologyView:
    id: UUID
    organization_id: UUID
    project_id: UUID
    organization_leader_id: UUID
    repository_teams: tuple[RepositoryTeamView, ...]
