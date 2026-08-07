from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class AutoCardCreate(BaseModel):
    top_dirs: list[str] = Field(default_factory=list)
    deps: list[str] = Field(default_factory=list)
    recent_commits: list[str] = Field(default_factory=list)
    exposed_apis: list[str] = Field(default_factory=list)
    low_signal: bool = False


class AutoCardView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    top_dirs: tuple[str, ...] = ()
    deps: tuple[str, ...] = ()
    recent_commits: tuple[str, ...] = ()
    exposed_apis: tuple[str, ...] = ()
    low_signal: bool = False


class RepositoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    url: HttpUrl
    description: str = Field(default="", max_length=4000)
    topics: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    auto_card: AutoCardCreate | None = None


class RepositoryView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    url: str
    description: str
    topics: tuple[str, ...]
    languages: tuple[str, ...]
    auto_card: AutoCardView | None = None


class DiscoveryRequest(BaseModel):
    requirement: str = Field(min_length=3, max_length=20_000)
    limit: int = Field(default=5, ge=1, le=50)
    entry_point: str | None = None


class DiscoveryCandidate(BaseModel):
    repository_id: UUID
    repository_name: str
    score: float
    matched_terms: tuple[str, ...]
    rationale: str
    is_entry_point: bool = False


# ---------------------------------------------------------------------------
# Confirmation / Integration / Bridge models
# ---------------------------------------------------------------------------


class ConfirmationRequest(BaseModel):
    requirement: str = Field(min_length=3, max_length=20_000)
    candidate_repos: list[str] = Field(min_length=1)
    discovery_evidence: dict[str, list] = Field(default_factory=dict)
    limit: int = Field(default=15, ge=1, le=50)


class RepositoryPlanView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    changed_apis: tuple[str, ...] = ()
    changed_modules: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    impacts: tuple[str, ...] = ()
    risk: str = "medium"


class ConfirmationResultView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    repository: str
    status: str
    confidence: float
    reason: str
    plan_summary: str
    plan: RepositoryPlanView | None = None
    missing_dependencies: list[str] = Field(default_factory=list)


class ConfirmationSummaryView(BaseModel):
    required: list[ConfirmationResultView]
    maybe: list[ConfirmationResultView]
    excluded: list[ConfirmationResultView]
    supplemented_repos: list[str]
    final_repos: list[str]


class IntegrationRequest(BaseModel):
    requirement: str = Field(min_length=3, max_length=20_000)
    confirmation: ConfirmationSummaryView


class ContractSpecView(BaseModel):
    producer: str
    consumer: str
    interface: str
    agreement: str


class TaskNodeView(BaseModel):
    repository: str
    instruction: str
    depends_on: tuple[str, ...] = ()
    parallelizable_with: tuple[str, ...] = ()
    tests: list[str] = Field(default_factory=list)
    """Verification commands the Worker runs; they become the Runner test commands."""


class IntegratedPlanView(BaseModel):
    engineering_spec: str
    contracts: list[ContractSpecView]
    task_dag: list[TaskNodeView]
    execution_batches: list[list[str]]


class MaterializeRequest(BaseModel):
    engineering_spec: str = ""
    contracts: list[ContractSpecView] = Field(default_factory=list)
    task_dag: list[TaskNodeView] = Field(default_factory=list)
    execution_batches: list[list[str]] = Field(default_factory=list)
    requirement: str = ""
    project_id: UUID
    leader_agent_id: UUID
    idempotency_prefix: str = Field(default="manual")


class MaterializeResponse(BaseModel):
    engineering_spec_id: UUID
    contract_spec_ids: list[UUID] = Field(default_factory=list)
    task_ids: list[UUID] = Field(default_factory=list)
    skipped_repos: list[str] = Field(default_factory=list)
    plan_id: UUID | None = None


class WorkerTaskStatusView(BaseModel):
    task_id: UUID
    status: str


class PlannedTaskStatusView(BaseModel):
    repository_id: UUID
    leader_task_id: UUID | None = None
    leader_status: str | None = None
    worker_tasks: list[WorkerTaskStatusView] = Field(default_factory=list)


class ExecutionPlanStatusView(BaseModel):
    plan_id: UUID
    status: str
    current_batch_index: int
    batches: list[list[PlannedTaskStatusView]] = Field(default_factory=list)
