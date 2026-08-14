from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from repomesh.modules.repository_intelligence.contracts import PlanDiff, PlanGraph


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


class OrgScanRequest(BaseModel):
    """Request body for organization-level batch scanning."""

    org_url: HttpUrl
    github_token: str = Field(default="", description="GitHub access token")
    gitlab_token: str = Field(default="", description="GitLab access token")
    max_workers: int = Field(default=5, ge=1, le=20)


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
    graph: PlanGraph | None = Field(
        default=None,
        description=(
            "The unified plan-layer dependency graph (nodes + edges + "
            "materialised projections). Single source of truth: the top-level "
            "``execution_batches`` / ``contracts`` / ``task_dag`` fields are "
            "projections of this graph, and frontends render from it (PR-5)."
        ),
    )


class MaterializeRequest(BaseModel):
    engineering_spec: str = ""
    contracts: list[ContractSpecView] = Field(default_factory=list)
    task_dag: list[TaskNodeView] = Field(default_factory=list)
    execution_batches: list[list[str]] = Field(default_factory=list)
    graph: PlanGraph | None = Field(
        default=None,
        description=(
            "The unified plan-layer dependency graph produced by integration. "
            "When present, materialization persists this graph as evidence "
            "instead of reconstructing a lossy fallback from projections."
        ),
    )
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
    mode: Literal["auto", "preview", "commit"] = Field(
        default="auto",
        description=(
            "How the replan executes. ``auto`` follows the server setting "
            "``REPOMESH_REPLAN_AUTO_COMMIT`` (default: commit, preserving "
            "pre-PR-4 behaviour). ``preview`` computes impact analysis and "
            "the graph diff with zero side effects; ``commit`` forces the "
            "full execution."
        ),
    )
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
    plan_id: UUID | None = Field(
        default=None,
        description=(
            "Immutable plan-layer snapshot persisted for the re-planned "
            "version. Present only when the replan produced a new plan and "
            "the snapshot store was available."
        ),
    )
    mode: Literal["preview", "commit"] = Field(
        description="The effective mode that was executed (after ``auto`` resolution)."
    )
    diff: PlanDiff | None = Field(
        default=None,
        description=(
            "Plan-layer graph diff between the previous snapshot and the "
            "re-planned version. Present on commit when a new plan was "
            "produced, and on preview (zero side effects). None for "
            "cancel-only replans without a new graph."
        ),
    )


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
