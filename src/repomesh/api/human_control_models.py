from uuid import UUID

from pydantic import BaseModel, Field, SecretStr

from repomesh.modules.project.contracts import (
    CheckpointDecisionKind,
    CodeAccessLevel,
    HumanControlAction,
    HumanProjectRole,
    ProjectCheckpoint,
    ProjectExecutionMode,
)


class AccountCredentials(BaseModel):
    username: str
    password: SecretStr


class AccountCreate(AccountCredentials):
    display_name: str
    is_admin: bool = False


class BootstrapAdmin(AccountCredentials):
    display_name: str


class RepositoryTeamCreate(BaseModel):
    repository_id: UUID
    leader_agent_id: UUID
    worker_agent_ids: list[UUID] = Field(min_length=1)


class HumanGrantCreate(BaseModel):
    human_principal_id: UUID
    role: HumanProjectRole
    code_access: CodeAccessLevel
    control_actions: set[HumanControlAction] = Field(min_length=1)
    repository_id: UUID | None = None
    path_patterns: list[str] = []


class ProjectTopologyCreate(BaseModel):
    organization_id: UUID
    project_id: UUID
    organization_leader_id: UUID
    repository_teams: list[RepositoryTeamCreate] = Field(min_length=1)
    execution_mode: ProjectExecutionMode = ProjectExecutionMode.AUTO
    required_checkpoints: set[ProjectCheckpoint] = set()
    human_grants: list[HumanGrantCreate] = []
    idempotency_key: str


class CheckpointDecisionCreate(BaseModel):
    review_request_id: UUID
    decision: CheckpointDecisionKind
    reason: str


class ProjectControlCreate(BaseModel):
    action: HumanControlAction
