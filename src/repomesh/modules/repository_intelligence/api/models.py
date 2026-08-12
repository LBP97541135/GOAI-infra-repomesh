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


class ScanTaskView(BaseModel):
    """A console scan in flight, or the record of one that finished.

    Honest about what it is: an in-process record. It does not survive a
    restart of the API, and polling one that vanished is answered with a 404
    that says so rather than one that implies a bad id.
    """

    task_id: UUID
    kind: Literal["organization", "repository"]
    url: str
    status: Literal["running", "succeeded", "failed"] = Field(
        description="While 'running' the counts below are partial, not a result",
    )
    total: int = Field(
        default=0,
        description="Repositories the scan found to work through; 0 until the listing returns",
    )
    scanned: int = Field(default=0, description="How many of them have been scanned so far")
    last_scanned_repository: str | None = Field(
        default=None,
        description="Most recently *finished* repository, not the one in progress",
    )
    registered: int = Field(
        default=0, description="Newly added to the catalog; only final once status is 'succeeded'"
    )
    skipped: int = Field(
        default=0,
        description="Already in the catalog under this name — what makes a re-scan idempotent",
    )
    failed: int = Field(
        default=0,
        description=(
            "Could not be registered. A count, not a list: which ones failed and why is in the "
            "server log, because echoing outbound failures back to a caller is the bug this "
            "codebase already fixed once. Retry granularity is the whole scan, not these rows."
        ),
    )
    error: str | None = Field(
        default=None,
        description="Why the scan as a whole failed, in the same generic terms a 502 would use",
    )
    started_at: datetime
    finished_at: datetime | None = None


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


# ---------------------------------------------------------------------------
# Discovery chain (contract v0.4 §4 / §5)
# ---------------------------------------------------------------------------


class DiscoveryAnswer(BaseModel):
    """One answer to one follow-up question raised by Step 0."""

    question: str = Field(min_length=1, max_length=2000)
    answer: str = Field(min_length=1, max_length=20_000)


class DiscoveryAnalysisRequest(BaseModel):
    """Step 0. No ``requirement`` field, deliberately (§4.3).

    The text of record is the draft snapshot's ``requirement_text``. Accepting
    it here would let the browser submit something other than what is on
    screen, and would make two places the requirement lives.
    """

    created_by_agent_id: UUID
    idempotency_key: str = Field(min_length=8, max_length=200)
    answers: list[DiscoveryAnswer] = Field(default_factory=list)
    force_continue: bool = Field(
        default=False,
        description=(
            "Record 'continuing, ignoring N follow-up questions' against the "
            "existing analysis. Does not call the model again — the user is "
            "not asking for a new opinion, they are overriding the one on file."
        ),
    )


class DiscoveryCandidatesRequest(BaseModel):
    """Step 1."""

    created_by_agent_id: UUID
    idempotency_key: str = Field(min_length=8, max_length=200)
    limit: int = Field(default=10, ge=1, le=50)
    entry_point: str | None = Field(default=None, max_length=200)


class DiscoveryClassificationRequest(BaseModel):
    """Step 2. Candidates and evidence are read from the snapshot (§4.3).

    The scripted ``POST /confirmation`` takes ``candidate_repos`` and
    ``discovery_evidence`` from its caller. This one must not: a browser that
    hands back the candidate set becomes the source of truth for it, and the
    set it sends can differ from the one the user was looking at.
    """

    created_by_agent_id: UUID
    idempotency_key: str = Field(min_length=8, max_length=200)


class DiscoveryPlanRequest(BaseModel):
    """Step 3."""

    created_by_agent_id: UUID
    idempotency_key: str = Field(min_length=8, max_length=200)


class DiscoveryTierAdjustment(BaseModel):
    """One inline retier made by the approver."""

    repository: str = Field(min_length=1, max_length=200)
    tier: Literal["required", "maybe", "excluded"]


class DiscoveryApprovalRequest(BaseModel):
    """§5.2. Adjustments and decision arrive together on purpose.

    Splitting them would persist a "retiered but not decided" state that
    nothing displays and that a second approver could race.
    """

    decided_by_agent_id: UUID
    idempotency_key: str = Field(min_length=8, max_length=200)
    decision: Literal["approved", "changes_requested"]
    reason: str = Field(default="", max_length=20_000)
    adjustments: list[DiscoveryTierAdjustment] = Field(default_factory=list)
    evidence_version: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "The classification fingerprint the approver actually read, from "
            "GET /issues/{id}/discovery. A mismatch is a 409: the tiering has "
            "been re-run since, and approving it now would release something "
            "nobody reviewed."
        ),
    )


class DiscoveryTriggerView(BaseModel):
    """The answer to a step trigger.

    202 with a ``task_id`` when work was started; 200 with ``status`` of
    ``replayed`` and no task when the same idempotency key already produced a
    result. A replay carries no task id on purpose — the original task record
    is in-process and may be long gone, and inventing one would promise a poll
    that cannot answer. Either way the panel's next move is the same: re-read
    ``GET /issues/{id}/discovery``.
    """

    task_id: UUID | None = None
    step: int = Field(description="GUI stepper cell 1..4 (contract §3.2)")
    status: Literal["accepted", "replayed"]


class DiscoveryTaskProgress(BaseModel):
    done: int = 0
    total: int = 1
    label: str | None = Field(
        default=None,
        description="Most recently finished candidate, not the one in flight",
    )


class DiscoveryTaskView(BaseModel):
    """A discovery step in flight, or the record of one that finished.

    In-process and lost on restart, like the console's scan tasks — but the
    consequence is milder here: the *result* is in the snapshot, so a lost task
    costs the caller a re-read, not a re-run.

    It never projects the step's result. That would be a second serialisation
    of data the discovery projection already owns; once ``status`` is
    ``succeeded`` the panel re-reads ``GET /issues/{id}/discovery``.
    """

    task_id: UUID
    issue_id: UUID
    step: int
    status: Literal["running", "succeeded", "failed"]
    progress: DiscoveryTaskProgress = Field(default_factory=DiscoveryTaskProgress)
    error: str | None = Field(
        default=None,
        description="Failure text, the same one recorded on the step in the snapshot",
    )
    started_at: datetime
    finished_at: datetime | None = None
