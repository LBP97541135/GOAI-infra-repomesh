from uuid import UUID

from pydantic import BaseModel, Field, SecretStr

from repomesh.modules.agent_directory.contracts import AgentRole
from repomesh.modules.agent_runtime.ports.agent_team import ManagerRuntime, WorkerRuntime
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


class AutomaticProjectTopologyCreate(BaseModel):
    organization_id: UUID
    project_id: UUID
    repository_ids: list[UUID] = Field(min_length=1)
    execution_mode: ProjectExecutionMode = ProjectExecutionMode.AUTO
    required_checkpoints: set[ProjectCheckpoint] = set()
    human_grants: list[HumanGrantCreate] = []
    idempotency_key: str = Field(min_length=1, max_length=200)


class CheckpointDecisionCreate(BaseModel):
    review_request_id: UUID
    decision: CheckpointDecisionKind
    reason: str


class ProjectControlCreate(BaseModel):
    action: HumanControlAction


class NativeAgentCreate(BaseModel):
    organization_id: UUID
    role: AgentRole
    resource_name: str = Field(min_length=3, max_length=100)
    model: str = Field(default="qwen3.6-plus", min_length=1, max_length=100)
    manager_runtime: ManagerRuntime = ManagerRuntime.COPAW
    worker_runtime: WorkerRuntime = WorkerRuntime.COPAW
    leader_agent_id: UUID | None = None
    repository_id: UUID | None = None
    responsibility_paths: list[str] = Field(default_factory=list)
    idempotency_key: str = Field(min_length=1, max_length=200)


class RepositoryAgentTeamOnboard(BaseModel):
    organization_id: UUID
    worker_count: int = Field(default=1, ge=1, le=20)
    model: str | None = Field(default=None, min_length=1, max_length=100)
    leader_runtime: WorkerRuntime = WorkerRuntime.COPAW
    worker_runtime: WorkerRuntime = WorkerRuntime.REPOMESH_RUNNER
    responsibility_paths: list[str] = Field(default_factory=lambda: ["**"])
    idempotency_key: str = Field(min_length=1, max_length=200)
