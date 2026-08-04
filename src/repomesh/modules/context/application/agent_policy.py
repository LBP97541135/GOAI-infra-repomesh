from uuid import UUID

from repomesh.modules.agent_directory.contracts import AgentPrincipalView, AgentRole
from repomesh.modules.context.contracts import ContextAction, ContextScope
from repomesh.modules.context.domain import PermissionLayer
from repomesh.modules.project.contracts import ProjectAgentTopologyView


def agent_permission_layer(profile: AgentPrincipalView) -> PermissionLayer:
    scopes = {
        AgentRole.ORGANIZATION_LEADER: frozenset(
            {ContextScope.ORGANIZATION, ContextScope.PROJECT_SHARED}
        ),
        AgentRole.REPOSITORY_LEADER: frozenset(
            {
                ContextScope.PROJECT_SHARED,
                ContextScope.TEAM_PRIVATE,
                ContextScope.TASK_PRIVATE,
            }
        ),
        AgentRole.WORKER: frozenset(
            {
                ContextScope.PROJECT_SHARED,
                ContextScope.TASK_PRIVATE,
                ContextScope.RUN_PRIVATE,
            }
        ),
    }[profile.role]
    return PermissionLayer(
        name="agent_policy",
        actions=frozenset(
            {
                ContextAction.DISCOVER,
                ContextAction.READ,
                ContextAction.MOUNT,
            }
        ),
        scopes=scopes,
        repository_ids=(
            frozenset({profile.repository_id}) if profile.repository_id is not None else None
        ),
        tools=None,
    )


def project_membership_permission_layer(
    profile: AgentPrincipalView,
    topology: ProjectAgentTopologyView,
    *,
    context_object_ids: frozenset[UUID],
) -> PermissionLayer:
    repository_id = None
    if profile.id != topology.organization_leader_id:
        for team in topology.repository_teams:
            if profile.id == team.leader_agent_id or profile.id in team.worker_agent_ids:
                repository_id = team.repository_id
                break
        else:
            return PermissionLayer(
                name="project_membership",
                actions=frozenset(),
                scopes=frozenset(),
            )
    if profile.organization_id != topology.organization_id:
        return PermissionLayer(
            name="project_membership",
            actions=frozenset(),
            scopes=frozenset(),
        )
    scopes = {
        AgentRole.ORGANIZATION_LEADER: frozenset(
            {ContextScope.ORGANIZATION, ContextScope.PROJECT_SHARED}
        ),
        AgentRole.REPOSITORY_LEADER: frozenset(
            {
                ContextScope.PROJECT_SHARED,
                ContextScope.TEAM_PRIVATE,
                ContextScope.TASK_PRIVATE,
            }
        ),
        AgentRole.WORKER: frozenset(
            {
                ContextScope.PROJECT_SHARED,
                ContextScope.TASK_PRIVATE,
                ContextScope.RUN_PRIVATE,
            }
        ),
    }[profile.role]
    return PermissionLayer(
        name="project_membership",
        actions=frozenset(
            {ContextAction.DISCOVER, ContextAction.READ, ContextAction.MOUNT}
        ),
        scopes=scopes,
        context_object_ids=context_object_ids,
        repository_ids=(frozenset({repository_id}) if repository_id is not None else None),
        path_patterns=profile.responsibility_paths or None,
        tools=None,
    )
