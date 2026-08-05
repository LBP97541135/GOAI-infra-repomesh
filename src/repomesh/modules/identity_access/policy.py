from dataclasses import dataclass
from fnmatch import fnmatchcase

from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalStatus,
    AgentPrincipalView,
    AgentRole,
)
from repomesh.modules.context.contracts import ContextObjectType, ContextScope
from repomesh.modules.identity_access.contracts import (
    AuthorizationAction,
    AuthorizationDecision,
    AuthorizationRequest,
)
from repomesh.modules.project.contracts import ProjectAgentTopologyView, RepositoryTeamView

_READ_ACTIONS = frozenset(
    {
        AuthorizationAction.CONTEXT_DISCOVER,
        AuthorizationAction.CONTEXT_READ,
        AuthorizationAction.CONTEXT_MOUNT,
    }
)

_ORGANIZATION_PUBLISH_TYPES = frozenset(ContextObjectType)
_REPOSITORY_LEADER_PUBLISH_TYPES = frozenset(
    {
        ContextObjectType.ENGINEERING_SPEC,
        ContextObjectType.CONTRACT,
        ContextObjectType.DECISION,
        ContextObjectType.REPOSITORY_SCOPE_REVIEW,
        ContextObjectType.TEST_PLAN,
        ContextObjectType.TASK_SPEC,
        ContextObjectType.PROGRESS,
        ContextObjectType.TEST_EVIDENCE,
        ContextObjectType.CHANGE_REQUEST,
        ContextObjectType.IMPACT_ASSESSMENT,
    }
)
_WORKER_PUBLISH_TYPES = frozenset(
    {
        ContextObjectType.TASK_RESULT,
        ContextObjectType.PROGRESS,
        ContextObjectType.TEST_EVIDENCE,
    }
)


@dataclass(frozen=True, slots=True)
class RoleAuthorizationPolicy:
    actions: frozenset[AuthorizationAction]
    context_scopes: frozenset[ContextScope]
    publish_types: frozenset[ContextObjectType]
    approve_types: frozenset[ContextObjectType]


ROLE_AUTHORIZATION_POLICIES = {
    AgentRole.ORGANIZATION_LEADER: RoleAuthorizationPolicy(
        actions=_READ_ACTIONS
        | frozenset(
            {
                AuthorizationAction.AGENT_DISCOVER,
                AuthorizationAction.CONTEXT_PUBLISH,
                AuthorizationAction.CONTEXT_APPROVE,
                AuthorizationAction.PROJECT_MANAGE,
                AuthorizationAction.TEAM_MANAGE,
                AuthorizationAction.TASK_MANAGE,
                AuthorizationAction.COLLABORATION_MESSAGE,
                AuthorizationAction.PULL_REQUEST_CREATE,
                AuthorizationAction.MERGE,
                AuthorizationAction.ROLLBACK_MANAGE,
            }
        ),
        context_scopes=frozenset(
            {ContextScope.ORGANIZATION, ContextScope.PROJECT_SHARED}
        ),
        publish_types=_ORGANIZATION_PUBLISH_TYPES,
        approve_types=frozenset(
            {
                ContextObjectType.ENGINEERING_SPEC,
                ContextObjectType.CONTRACT,
                ContextObjectType.REPOSITORY_SCOPE_REVIEW,
                ContextObjectType.TEST_PLAN,
                ContextObjectType.CHANGE_REQUEST,
                ContextObjectType.IMPACT_ASSESSMENT,
                ContextObjectType.DELIVERY_PLAN,
            }
        ),
    ),
    AgentRole.REPOSITORY_LEADER: RoleAuthorizationPolicy(
        actions=_READ_ACTIONS
        | frozenset(
            {
                AuthorizationAction.AGENT_DISCOVER,
                AuthorizationAction.CONTEXT_PUBLISH,
                AuthorizationAction.CONTEXT_APPROVE,
                AuthorizationAction.TEAM_MANAGE,
                AuthorizationAction.TASK_MANAGE,
                AuthorizationAction.REPOSITORY_READ,
                AuthorizationAction.TEST_RUN,
                AuthorizationAction.COLLABORATION_MESSAGE,
                AuthorizationAction.PULL_REQUEST_CREATE,
            }
        ),
        context_scopes=frozenset(
            {
                ContextScope.PROJECT_SHARED,
                ContextScope.TEAM_PRIVATE,
                ContextScope.TASK_PRIVATE,
            }
        ),
        publish_types=_REPOSITORY_LEADER_PUBLISH_TYPES,
        approve_types=frozenset(
            {ContextObjectType.ENGINEERING_SPEC, ContextObjectType.TASK_SPEC}
        ),
    ),
    AgentRole.WORKER: RoleAuthorizationPolicy(
        actions=_READ_ACTIONS
        | frozenset(
            {
                AuthorizationAction.CONTEXT_PUBLISH,
                AuthorizationAction.REPOSITORY_READ,
                AuthorizationAction.REPOSITORY_WRITE,
                AuthorizationAction.CODING_EXECUTE,
                AuthorizationAction.TEST_RUN,
                AuthorizationAction.COLLABORATION_MESSAGE,
            }
        ),
        context_scopes=frozenset(
            {
                ContextScope.PROJECT_SHARED,
                ContextScope.TASK_PRIVATE,
                ContextScope.RUN_PRIVATE,
            }
        ),
        publish_types=_WORKER_PUBLISH_TYPES,
        approve_types=frozenset(),
    ),
}


class PolicyAuthorizationGateway:
    def authorize(
        self,
        profile: AgentPrincipalView,
        request: AuthorizationRequest,
        *,
        topology: ProjectAgentTopologyView | None = None,
    ) -> AuthorizationDecision:
        return authorize_agent(profile, request, topology=topology)


def authorize_agent(
    profile: AgentPrincipalView,
    request: AuthorizationRequest,
    *,
    topology: ProjectAgentTopologyView | None = None,
) -> AuthorizationDecision:
    if profile.status is not AgentPrincipalStatus.ACTIVE:
        return AuthorizationDecision(False, "agent_disabled")
    if profile.organization_id != request.organization_id:
        return AuthorizationDecision(False, "organization_mismatch")
    policy = ROLE_AUTHORIZATION_POLICIES[profile.role]
    if request.action not in policy.actions:
        return AuthorizationDecision(False, "role_action_denied")

    team = None
    if request.project_id is not None:
        membership = _project_membership(profile, request, topology)
        if isinstance(membership, AuthorizationDecision):
            return membership
        team = membership

    if request.context_scope is ContextScope.SECRET:
        return AuthorizationDecision(False, "secret_gateway_required")
    if request.context_scope is not None and request.context_scope not in policy.context_scopes:
        return AuthorizationDecision(False, "context_scope_denied")
    if (
        request.action is AuthorizationAction.CONTEXT_PUBLISH
        and request.context_object_type not in policy.publish_types
    ):
        return AuthorizationDecision(False, "context_publish_type_denied")
    if (
        request.action is AuthorizationAction.CONTEXT_APPROVE
        and request.context_object_type not in policy.approve_types
    ):
        return AuthorizationDecision(False, "context_approve_type_denied")

    if request.repository_id is not None:
        decision = _authorize_repository(profile, request, team)
        if decision is not None:
            return decision
    if request.action in {
        AuthorizationAction.AGENT_DISCOVER,
        AuthorizationAction.COLLABORATION_MESSAGE,
    }:
        decision = _authorize_agent_reachability(profile, request, topology, team)
        if decision is not None:
            return decision
    return AuthorizationDecision(True, "allowed")


def _project_membership(
    profile: AgentPrincipalView,
    request: AuthorizationRequest,
    topology: ProjectAgentTopologyView | None,
) -> RepositoryTeamView | None | AuthorizationDecision:
    if topology is None or topology.project_id != request.project_id:
        return AuthorizationDecision(False, "project_topology_missing")
    if topology.organization_id != request.organization_id:
        return AuthorizationDecision(False, "project_organization_mismatch")
    if profile.id == topology.organization_leader_id:
        return None
    for team in topology.repository_teams:
        if profile.id == team.leader_agent_id or profile.id in team.worker_agent_ids:
            return team
    return AuthorizationDecision(False, "project_membership_denied")


def _authorize_repository(
    profile: AgentPrincipalView,
    request: AuthorizationRequest,
    team: RepositoryTeamView | None,
) -> AuthorizationDecision | None:
    if profile.role is AgentRole.ORGANIZATION_LEADER:
        if request.action in {
            AuthorizationAction.REPOSITORY_READ,
            AuthorizationAction.REPOSITORY_WRITE,
            AuthorizationAction.CODING_EXECUTE,
            AuthorizationAction.TEST_RUN,
        }:
            return AuthorizationDecision(False, "organization_leader_has_no_code_access")
        return None
    if profile.repository_id != request.repository_id:
        return AuthorizationDecision(False, "repository_mismatch")
    if team is not None and team.repository_id != request.repository_id:
        return AuthorizationDecision(False, "project_repository_membership_denied")
    if request.path is not None and not _matches_path(request.path, profile.responsibility_paths):
        return AuthorizationDecision(False, "responsibility_path_denied")
    return None


def _authorize_agent_reachability(
    profile: AgentPrincipalView,
    request: AuthorizationRequest,
    topology: ProjectAgentTopologyView | None,
    team: RepositoryTeamView | None,
) -> AuthorizationDecision | None:
    target = request.target_agent_id
    if target is None:
        return AuthorizationDecision(False, "target_agent_required")
    if topology is None:
        return AuthorizationDecision(False, "project_topology_missing")
    if profile.role is AgentRole.ORGANIZATION_LEADER:
        allowed = {item.leader_agent_id for item in topology.repository_teams}
    elif profile.role is AgentRole.REPOSITORY_LEADER and team is not None:
        allowed = {topology.organization_leader_id, *team.worker_agent_ids}
    elif profile.role is AgentRole.WORKER and team is not None:
        allowed = {team.leader_agent_id}
    else:
        allowed = set()
    if target not in allowed:
        return AuthorizationDecision(False, "agent_reachability_denied")
    return None


def _matches_path(path: str, patterns: tuple[str, ...]) -> bool:
    normalized = path.replace("\\", "/").lstrip("/")
    return any(fnmatchcase(normalized, pattern) for pattern in patterns)
