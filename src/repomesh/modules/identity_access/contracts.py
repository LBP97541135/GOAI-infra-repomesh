from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from repomesh.modules.agent_directory.contracts import AgentPrincipalView
from repomesh.modules.context.contracts import ContextObjectType, ContextScope
from repomesh.modules.project.contracts import ProjectAgentTopologyView


class AuthorizationAction(StrEnum):
    AGENT_DISCOVER = "agent.discover"
    CONTEXT_DISCOVER = "context.discover"
    CONTEXT_READ = "context.read"
    CONTEXT_MOUNT = "context.mount"
    CONTEXT_PUBLISH = "context.publish"
    CONTEXT_APPROVE = "context.approve"
    PROJECT_MANAGE = "project.manage"
    TEAM_MANAGE = "team.manage"
    TASK_MANAGE = "task.manage"
    REPOSITORY_READ = "repository.read"
    REPOSITORY_WRITE = "repository.write"
    CODING_EXECUTE = "coding.execute"
    TEST_RUN = "test.run"
    COLLABORATION_MESSAGE = "collaboration.message"
    PULL_REQUEST_CREATE = "pull_request.create"
    MERGE = "merge"
    ROLLBACK_MANAGE = "rollback.manage"


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    action: AuthorizationAction
    organization_id: UUID
    project_id: UUID | None = None
    repository_id: UUID | None = None
    target_agent_id: UUID | None = None
    path: str | None = None
    context_scope: ContextScope | None = None
    context_object_type: ContextObjectType | None = None


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    allowed: bool
    reason: str


class AgentAuthorizationGateway(Protocol):
    def authorize(
        self,
        profile: AgentPrincipalView,
        request: AuthorizationRequest,
        *,
        topology: ProjectAgentTopologyView | None = None,
    ) -> AuthorizationDecision: ...
