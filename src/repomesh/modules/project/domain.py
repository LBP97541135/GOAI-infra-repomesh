from dataclasses import dataclass, field, replace
from uuid import UUID

from repomesh.modules.project.contracts import (
    ProjectAgentTopologyView,
    ProjectTeamRuntimeStatus,
    RepositoryTeamView,
)
from repomesh.shared.domain import new_id


class ProjectTopologyError(RuntimeError):
    pass


class ProjectTopologyConflict(ProjectTopologyError):
    pass


class ProjectTopologyViolation(ProjectTopologyError):
    pass


@dataclass(frozen=True, slots=True)
class RepositoryTeam:
    project_id: UUID
    repository_id: UUID
    leader_agent_id: UUID
    worker_agent_ids: tuple[UUID, ...]
    id: UUID = field(default_factory=new_id)
    agentteams_team_name: str | None = None
    runtime_status: ProjectTeamRuntimeStatus = ProjectTeamRuntimeStatus.PENDING
    room_id: str | None = None
    leader_room_id: str | None = None

    def __post_init__(self) -> None:
        if not self.worker_agent_ids:
            raise ProjectTopologyViolation("a repository team requires at least one worker")
        if len(set(self.worker_agent_ids)) != len(self.worker_agent_ids):
            raise ProjectTopologyViolation("repository team workers must be unique")
        if self.leader_agent_id in self.worker_agent_ids:
            raise ProjectTopologyViolation("repository leader cannot also be a team worker")
        if self.agentteams_team_name is None:
            object.__setattr__(
                self,
                "agentteams_team_name",
                f"rm-team-{self.id.hex}",
            )

    def with_runtime(
        self,
        *,
        status: ProjectTeamRuntimeStatus,
        room_id: str | None,
        leader_room_id: str | None,
    ) -> "RepositoryTeam":
        return replace(
            self,
            runtime_status=status,
            room_id=room_id,
            leader_room_id=leader_room_id,
        )

    def to_view(self) -> RepositoryTeamView:
        return RepositoryTeamView(
            id=self.id,
            project_id=self.project_id,
            repository_id=self.repository_id,
            leader_agent_id=self.leader_agent_id,
            worker_agent_ids=self.worker_agent_ids,
            agentteams_team_name=self.agentteams_team_name or "",
            runtime_status=self.runtime_status,
            room_id=self.room_id,
            leader_room_id=self.leader_room_id,
        )


@dataclass(frozen=True, slots=True)
class ProjectAgentTopology:
    organization_id: UUID
    project_id: UUID
    organization_leader_id: UUID
    repository_teams: tuple[RepositoryTeam, ...]
    id: UUID = field(default_factory=new_id)

    def __post_init__(self) -> None:
        if not self.repository_teams:
            raise ProjectTopologyViolation("a project topology requires repository teams")
        repository_ids = [team.repository_id for team in self.repository_teams]
        if len(set(repository_ids)) != len(repository_ids):
            raise ProjectTopologyViolation("a project can have only one team per repository")
        agent_ids = [
            agent_id
            for team in self.repository_teams
            for agent_id in (team.leader_agent_id, *team.worker_agent_ids)
        ]
        if len(set(agent_ids)) != len(agent_ids):
            raise ProjectTopologyViolation("an agent cannot join multiple repository teams")
        if any(team.project_id != self.project_id for team in self.repository_teams):
            raise ProjectTopologyViolation("repository team project must match topology project")

    def to_view(self) -> ProjectAgentTopologyView:
        return ProjectAgentTopologyView(
            id=self.id,
            organization_id=self.organization_id,
            project_id=self.project_id,
            organization_leader_id=self.organization_leader_id,
            repository_teams=tuple(team.to_view() for team in self.repository_teams),
        )
