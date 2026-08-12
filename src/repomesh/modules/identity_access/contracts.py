from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from repomesh.modules.agent_directory.contracts import AgentPrincipalView
from repomesh.modules.context.contracts import ContextObjectType, ContextScope
from repomesh.modules.project.contracts import ProjectAgentTopologyView
from repomesh.shared.events import ActorType


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


@dataclass(frozen=True, slots=True)
class OrganizationView:
    """Contract v0.3 §2.2: one workspace row in the registry."""

    organization_id: UUID
    name: str
    created_at: str
    agent_count: int


@dataclass(frozen=True, slots=True)
class CreateOrganizationCommand:
    """Contract v0.3 §2.3. Creating a workspace = organization row plus its
    ORGANIZATION_LEADER registration (a desired-state directory row, not a
    running agent — the roster reports its runtime honestly as absent).

    ``actor_type``/``actor_id`` feed the audit trail (v0.3 §6 S-6): the API
    layer resolves them to the logged-in human session when the request
    carries one, else to a fingerprint of the presented action token — a
    hardcoded label would make workspace creation untraceable."""

    name: str
    idempotency_key: str
    actor_type: ActorType
    actor_id: str
    leader_resource_name: str | None = None


@dataclass(frozen=True, slots=True)
class OrganizationReceipt:
    organization_id: UUID
    name: str
    created_at: str
    leader_agent_id: UUID
    created: bool


class OrganizationRegistry(Protocol):
    async def list_views(
        self, organization_id: UUID | None = None
    ) -> tuple[OrganizationView, ...]: ...

    async def create(self, command: CreateOrganizationCommand) -> OrganizationReceipt: ...
