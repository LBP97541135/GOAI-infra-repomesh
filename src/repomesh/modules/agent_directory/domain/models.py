from dataclasses import dataclass, field
from pathlib import PurePosixPath
from uuid import UUID

from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalStatus,
    AgentPrincipalView,
    AgentRole,
)
from repomesh.shared.domain import new_id


def _validate_path_pattern(value: str) -> None:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not value or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ValueError("responsibility paths must be normalized repository-relative patterns")


@dataclass(frozen=True, slots=True)
class AgentPrincipal:
    organization_id: UUID
    role: AgentRole
    leader_agent_id: UUID | None
    singleton_key: str | None
    repository_id: UUID | None
    responsibility_paths: tuple[str, ...]
    agentteams_resource_name: str
    status: AgentPrincipalStatus = AgentPrincipalStatus.ACTIVE
    id: UUID = field(default_factory=new_id)

    def __post_init__(self) -> None:
        if not self.agentteams_resource_name.strip():
            raise ValueError("AgentTeams resource name is required")
        for path in self.responsibility_paths:
            _validate_path_pattern(path)

    def to_view(self) -> AgentPrincipalView:
        return AgentPrincipalView(
            id=self.id,
            organization_id=self.organization_id,
            role=self.role,
            leader_agent_id=self.leader_agent_id,
            repository_id=self.repository_id,
            responsibility_paths=self.responsibility_paths,
            agentteams_resource_name=self.agentteams_resource_name,
            status=self.status,
        )
