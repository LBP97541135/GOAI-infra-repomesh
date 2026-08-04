from dataclasses import dataclass

from repomesh.modules.agent_directory.contracts import (
    AgentRole,
)


@dataclass(frozen=True, slots=True)
class RolePolicy:
    allowed_parent_roles: frozenset[AgentRole]
    requires_repository: bool


ROLE_POLICIES = {
    AgentRole.ORGANIZATION_LEADER: RolePolicy(
        allowed_parent_roles=frozenset(),
        requires_repository=False,
    ),
    AgentRole.REPOSITORY_LEADER: RolePolicy(
        allowed_parent_roles=frozenset({AgentRole.ORGANIZATION_LEADER}),
        requires_repository=True,
    ),
    AgentRole.WORKER: RolePolicy(
        allowed_parent_roles=frozenset({AgentRole.REPOSITORY_LEADER}),
        requires_repository=True,
    ),
}
