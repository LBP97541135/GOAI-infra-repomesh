from datetime import datetime
from typing import Any, Literal
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


class IssueIntakeCreate(BaseModel):
    """Contract v0.3 §1.2. No title on purpose: it derives from
    requirement_text — accepting it would create a second source of truth.
    ``organization_id`` is an optional cross-check (v0.3 §6 S-4), never a
    source of truth: the workspace of record still derives from the actor,
    and a mismatch is rejected with 403. Key minimum is 8 (§6 S-5): the key
    seeds the workspace-scoped project_id derivation, so one-character keys
    invite collisions; clients send random UUIDs anyway."""

    requirement_text: str = Field(min_length=1, max_length=20000)
    created_by_agent_id: UUID
    idempotency_key: str = Field(min_length=8, max_length=200)
    organization_id: UUID | None = None


class OrgScanRequest(BaseModel):
    """Request body for organization-level batch scanning."""

    org_url: HttpUrl
    github_token: str = Field(default="", description="GitHub access token")
    gitlab_token: str = Field(default="", description="GitLab access token")
    max_workers: int = Field(default=5, ge=1, le=20)


class RepoScanRequest(BaseModel):
    """Request body for single-repository URL scanning."""

    repo_url: HttpUrl
    github_token: str = Field(default="", description="GitHub access token")
    gitlab_token: str = Field(default="", description="GitLab access token")


class ConsoleOrgScanRequest(BaseModel):
    """Console request body for an organization scan.

    A whitelist, not a copy of :class:`OrgScanRequest`: ``github_token`` and
    ``gitlab_token`` are absent on purpose. A browser is not a place to type a
    personal access token, so the console's credentials come from the server's
    env (``REPOMESH_REPOSITORY_SCAN_GITHUB_TOKEN`` / ``..._GITLAB_TOKEN``) and
    from nowhere else. The native RI endpoints keep their token fields for
    scripts and operators; this face closes that bypass.

    ``extra="forbid"`` makes the closure audible: a client that sends a token
    anyway gets a 422 naming the field, rather than having it silently dropped
    and believing the private repo it asked for simply was not there.
    """

    model_config = ConfigDict(extra="forbid")

    org_url: HttpUrl
    max_workers: int = Field(default=5, ge=1, le=20)


class ConsoleRepoScanRequest(BaseModel):
    """Console request body for a single-repository scan.

    Same whitelist rule as :class:`ConsoleOrgScanRequest`: no token fields,
    credentials from the server env only.
    """

    model_config = ConfigDict(extra="forbid")

    repo_url: HttpUrl


class UrlIdentification(BaseModel):
    """The backend's verdict on what a pasted URL points at.

    The console shows this as a badge next to the URL box. It exists so the
    console does not have to reimplement ``detect_platform`` in TypeScript and
    then drift from it — the judgement has exactly one home, and it is here.
    """

    url: str
    url_type: Literal["single_repo", "group", "unknown"] = Field(
        description="What the URL points at: one repository, a group/org, or neither",
    )
    platform: Literal["github", "gitlab", "local"] = Field(
        description="Hosting platform inferred from the URL; 'local' means a filesystem path",
    )
    repository_name: str | None = Field(
        default=None,
        description="The name a single-repo scan would register, so the console can preview it",
    )


class RepositoryView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    url: str
    description: str
    topics: tuple[str, ...]
    languages: tuple[str, ...]
    auto_card: AutoCardView | None = None


class OrgScanResult(BaseModel):
    """Response for organization-level batch scanning."""

    org_url: str
    total_scanned: int
    registered: int
    skipped: int
    failed: int
    repositories: list[RepositoryView] = Field(default_factory=list)


class RepoScanResult(BaseModel):
    """Response for single-repository URL scanning.

    Counts rather than a boolean, even though every one of them is 0 or 1: the
    console renders the org scan, the single-repo scan and the async task view
    with one component, and that only stays true if the shapes agree.
    """

    repo_url: str
    total_scanned: int
    registered: int
    skipped: int
    failed: int
    repositories: list[RepositoryView] = Field(default_factory=list)


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


class RequirementAnalysisRequest(BaseModel):
    requirement: str = Field(min_length=1, max_length=20_000)


class RequirementAnalysisView(BaseModel):
    sufficient: bool
    confidence: float
    missing_dimensions: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    extracted_keywords: list[str] = Field(default_factory=list)


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
    repo_details: dict[str, RepositoryPlanView] = Field(
        default_factory=dict,
        description=(
            "Per-repository adjustment plans from the confirmation phase, "
            "keyed by repository name. Used to enrich the handoff documents "
            "generated for this plan."
        ),
    )


class MaterializeResponse(BaseModel):
    engineering_spec_id: UUID
    contract_spec_ids: list[UUID] = Field(default_factory=list)
    task_ids: list[UUID] = Field(default_factory=list)
    skipped_repos: list[str] = Field(default_factory=list)
    plan_id: UUID | None = None
    handoff_doc_ids: list[UUID] = Field(default_factory=list)


class ReplanRequest(BaseModel):
    """Trigger a partial replan after a BLOCKED task reports an upstream change."""

    project_id: UUID
    leader_agent_id: UUID
    feedback: str = Field(min_length=1, max_length=20_000)
    change_source_repo: str = Field(min_length=1, max_length=200)
    plan_version: int = Field(default=1, ge=1)
    requirement: str = Field(default="", max_length=20_000)
    idempotency_prefix: str = Field(min_length=1, max_length=100)
    confirmation: ConfirmationSummaryView | None = Field(
        default=None,
        description=(
            "Optional confirmation summary used by the Leader to locally "
            "re-integrate a new plan for the affected repositories. When "
            "provided, the replan produces a new plan (and regenerates the "
            "affected repositories' handoff documents); when omitted, the "
            "replan only supersedes the old tasks."
        ),
    )


class ReplanResponse(BaseModel):
    new_plan_version: int
    superseded_task_ids: list[UUID] = Field(default_factory=list)
    new_task_ids: list[UUID] = Field(default_factory=list)
    affected_repos: list[str] = Field(default_factory=list)
    feedback_summary: str = ""
    handoff_doc_ids: list[UUID] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Handoff documents (仓库对接文档 / human approval)
# ---------------------------------------------------------------------------


class HandoffDocView(BaseModel):
    """A repository's adjustment proposal awaiting (or carrying) a human decision."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    plan_version: int
    repository: str
    status: str
    decision: str | None = None
    content: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    created_by_agent_id: UUID | None = None
    decided_by_agent_id: UUID | None = None
    decision_reason: str = ""
    superseded_by_version: int | None = None


class HandoffDocDecisionRequest(BaseModel):
    """Manual decision of a repository owner on a PENDING handoff document."""

    approved: bool
    decided_by_agent_id: UUID
    reason: str = Field(default="", max_length=4000)


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


class DependencyGraphView(BaseModel):
    edges: list[dict[str, str]] = Field(default_factory=list)
    edge_count: int
    confirmed_edge_count: int


class PlanSnapshotView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    plan_version: int
    created_at: datetime
    created_by_agent_id: UUID | None = None
    engineering_spec: str
    contracts: list[dict[str, Any]] = Field(default_factory=list)
    task_dag: list[dict[str, Any]] = Field(default_factory=list)
    execution_batches: list[list[str]] = Field(default_factory=list)
    graph_edges: list[dict[str, Any]] = Field(default_factory=list)
    execution_plan_id: UUID | None = None
    requirement_text: str | None = None
    integration_method: str | None = None


class PlanSnapshotSummaryView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    plan_version: int
    created_at: datetime
    integration_method: str | None = None
    execution_plan_id: UUID | None = None
