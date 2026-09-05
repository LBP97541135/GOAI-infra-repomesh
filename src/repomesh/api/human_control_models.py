from uuid import UUID

from pydantic import BaseModel, Field, SecretStr

from repomesh.modules.agent_directory.contracts import AgentRole
from repomesh.modules.agent_runtime.ports.agent_team import ManagerRuntime, WorkerRuntime
from repomesh.modules.project.contracts import (
    CheckpointDecisionKind,
    CodeAccessLevel,
    ConstructionMode,
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


class PolicyDraftGrant(HumanGrantCreate):
    """A draft's human grant, with the one restated rule handed back to the domain.

    ``HumanGrantCreate`` spells ``min_length=1`` on ``control_actions``, which
    is a second copy of ``HumanProjectGrant``'s "human grant requires control
    actions" — and a copy answers in Pydantic's words rather than the domain's.
    A draft is judged twice, once when it is saved and once if materialization
    refuses, and the two occasions have to produce the same sentence about the
    same data; that is the whole reason ``supervision_policy`` exists. The other
    three single-grant rules were never restated here and already reach the
    caller verbatim, so restating this one would make exactly one of the four
    speak a different language.
    """

    control_actions: set[HumanControlAction] = set()


class TopologyPolicyDraftPut(BaseModel):
    """A project's whole supervision policy, overwritten in one call.

    No ``idempotency_key``, unlike the two topology creates below: a
    requirement holds one supervision intent and ``PUT`` of the whole document
    is already idempotent. There is nothing here to accidentally create twice.
    """

    execution_mode: ProjectExecutionMode = ProjectExecutionMode.AUTO
    required_checkpoints: set[ProjectCheckpoint] = set()
    human_grants: list[PolicyDraftGrant] = []


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
    model: str | None = Field(default=None, min_length=1, max_length=100)
    manager_runtime: ManagerRuntime = ManagerRuntime.COPAW
    manager_image: str | None = Field(default=None, min_length=1, max_length=255)
    worker_runtime: WorkerRuntime = WorkerRuntime.COPAW
    leader_agent_id: UUID | None = None
    repository_id: UUID | None = None
    responsibility_paths: list[str] = Field(default_factory=list)
    idempotency_key: str = Field(min_length=1, max_length=200)


class RepositoryAgentTeamOnboard(BaseModel):
    organization_id: UUID
    worker_count: int = Field(default=1, ge=1, le=20)
    model: str | None = Field(default=None, min_length=1, max_length=100)
    #: How this team builds (hosted-native spec D-17). Replaces the
    #: ``leader_runtime`` / ``worker_runtime`` pair: the controller runtime
    #: and whether the controller containerizes the members are derived from
    #: the mode, so there is no second runtime default here to disagree with
    #: the settings. ``None`` takes ``settings.construction_mode_default``.
    construction_mode: ConstructionMode | None = None
    responsibility_paths: list[str] = Field(default_factory=lambda: ["**"])
    idempotency_key: str = Field(min_length=1, max_length=200)


class ManualAgentTeamCreate(BaseModel):
    organization_id: UUID
    name: str = Field(min_length=3, max_length=100, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]+$")
    description: str = Field(default="", max_length=255)
    leader_agent_id: UUID
    member_agent_ids: list[UUID] = Field(default_factory=list, max_length=20)
    idempotency_key: str = Field(min_length=1, max_length=200)
