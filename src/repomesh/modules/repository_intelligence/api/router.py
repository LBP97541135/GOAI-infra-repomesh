import logging
from collections.abc import Callable
from dataclasses import asdict
from secrets import compare_digest
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, PlainTextResponse

from repomesh.modules.change_orchestration import ExecutionPlaneUnavailable
from repomesh.modules.repository_intelligence.application import (
    DependencyGraphService,
    HandoffDocError,
    IssueIntakeActorNotFound,
    IssueIntakeDenied,
    RegisterRepository,
    RepositoryDiscoveryService,
    ScanRegistration,
    identify_url_type,
    register_scanned_profiles,
    render_markdown,
)
from repomesh.modules.repository_intelligence.application.confirmation import (
    ConfirmationResult,
    ConfirmationSummary,
    RepositoryPlan,
)
from repomesh.modules.repository_intelligence.application.handoff_docs import (
    HandoffDocStatus,
)
from repomesh.modules.repository_intelligence.application.plan_integration import (
    ContractSpec,
    IntegratedPlan,
    TaskNode,
)
from repomesh.modules.repository_intelligence.contracts import IssueIntakeCommand
from repomesh.modules.repository_intelligence.domain import AutoCard, RepositoryProfile
from repomesh.modules.repository_intelligence.infrastructure.platform import UrlType
from repomesh.modules.repository_intelligence.ports import RepositoryCatalog
from repomesh.modules.task_orchestration.contracts import ExecutionPlanStatusSnapshot
from repomesh.shared.workflow import WorkflowBlocked

from .models import (
    ConfirmationRequest,
    ConfirmationResultView,
    ConfirmationSummaryView,
    ContractSpecView,
    DependencyGraphView,
    DiscoveryCandidate,
    DiscoveryRequest,
    ExecutionPlanStatusView,
    HandoffDocDecisionRequest,
    HandoffDocView,
    IntegratedPlanView,
    IntegrationRequest,
    IssueIntakeCreate,
    MaterializeRequest,
    MaterializeResponse,
    OrgScanRequest,
    OrgScanResult,
    PlannedTaskStatusView,
    PlanSnapshotSummaryView,
    PlanSnapshotView,
    ReplanRequest,
    ReplanResponse,
    RepoScanRequest,
    RepoScanResult,
    RepositoryCreate,
    RepositoryView,
    RequirementAnalysisRequest,
    RequirementAnalysisView,
    TaskNodeView,
    UrlIdentification,
    WorkerTaskStatusView,
)

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["repository-intelligence"])


def get_catalog(request: Request) -> RepositoryCatalog:
    return request.app.state.container.repository_catalog


CatalogDependency = Annotated[RepositoryCatalog, Depends(get_catalog)]


def _build_auto_card(body_card) -> AutoCard | None:  # noqa: ANN001
    if body_card is None:
        return None
    return AutoCard(
        top_dirs=tuple(body_card.top_dirs),
        deps=tuple(body_card.deps),
        recent_commits=tuple(body_card.recent_commits),
        exposed_apis=tuple(body_card.exposed_apis),
        low_signal=body_card.low_signal,
    )


def require_action_token(request: Request) -> None:
    """Bearer action-token check for every write on this router.

    It started life guarding only ``POST /issues`` (contract v0.3 §3), which
    left the eight other writes here — including the manual approval gate and
    the org scanner — reachable with no credential at all. The check now hangs
    off each write route instead of one handler. It is deliberately not a
    router-level dependency: nine reads share this router, and closing a read
    that is open today is a scope call for whoever owns those consumers, not
    part of stopping the bleeding on the writes.

    Missing configuration fails closed (503). An unset token must not read as
    "no authentication required" — that is how a deployment ends up open.
    """

    from repomesh.settings import get_settings

    expected = get_settings().agent_action_token
    if not expected:
        raise HTTPException(status_code=503, detail="write authentication is not configured")
    presented = request.headers.get("Authorization") or ""
    if not compare_digest(presented, f"Bearer {expected}"):
        raise HTTPException(status_code=401, detail="invalid credentials")


#: Applied to every write route below; reads on this router stay open.
ACTION_TOKEN = Depends(require_action_token)


def _require_scannable_host(url: str) -> None:
    """Refuse org URLs whose host is not on the configured allowlist.

    ``detect_platform`` treats every non-github.com git URL as a self-hosted
    GitLab and the fetcher derives its API base from that same URL, so an
    arbitrary host in the request body becomes an outbound request from this
    server. The allowlist keeps that reachable set to hosts an operator has
    named. Default is github.com only; extend with
    ``REPOMESH_REPOSITORY_SCAN_ALLOWED_HOSTS`` (comma separated).
    """

    from repomesh.settings import get_settings

    host = (urlsplit(url).hostname or "").lower()
    allowed = {
        item.strip().lower()
        for item in (get_settings().repository_scan_allowed_hosts or "").split(",")
        if item.strip()
    }
    if host in allowed:
        return
    _logger.warning("rejected org scan for host outside the allowlist: %s", host)
    raise HTTPException(
        status_code=400,
        detail="organization host is not on the configured scan allowlist",
    )


def build_scan_fetcher(
    url: str,
    *,
    target: str,
    github_token: str = "",
    gitlab_token: str = "",
):  # noqa: ANN201 - PlatformFetcher is imported lazily, see below
    """Validate a scan target and build the fetcher that will reach it.

    Every scan endpoint — native, console, sync, background — goes through
    here, so the two refusals that must happen *before* egress cannot be
    forgotten by the next endpoint someone adds: a local path is not
    scannable, and a host nobody put on the allowlist is not reachable.
    """

    from repomesh.modules.repository_intelligence.infrastructure.platform import (  # noqa: PLC0415
        Platform,
        detect_platform,
        make_fetcher,
    )

    platform = detect_platform(url)
    if platform is Platform.LOCAL:
        raise HTTPException(400, f"URL must be a GitHub/GitLab {target} URL")
    _require_scannable_host(url)
    return make_fetcher(platform, github_token=github_token, gitlab_token=gitlab_token)


def require_single_repo_url(url: str) -> None:
    """Refuse a URL that does not name one repository.

    The same judgement the console badges with, applied server-side: a group
    URL sent to a single-repo scan is a mistake worth naming, not something to
    guess a repository name out of.
    """

    if identify_url_type(url) is not UrlType.SINGLE_REPO:
        raise HTTPException(400, "URL must point at a single repository, not a group")


class ScanFailed(Exception):
    """A scan could not complete.

    Carries the sentence the caller may see and nothing else. Whatever the
    outbound request actually saw is already in the log by the time this is
    raised — that separation is the point (S-2), and it is what lets the
    background scan tasks report a failure without inventing a second, chattier
    error path.
    """


async def perform_org_scan(
    url: str,
    fetcher: object,
    catalog: RepositoryCatalog,
    *,
    max_workers: int,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> ScanRegistration:
    """Scan an organization and register what came back.

    ``on_progress`` receives ``(completed, total, repository_name)`` after each
    repository finishes — it is how the console's background tasks turn a long
    silence into an "n of m" line.
    """

    from repomesh.modules.repository_intelligence.application.scan_remote import (  # noqa: PLC0415
        scan_org,
    )

    try:
        profiles = await scan_org(url, fetcher, max_workers=max_workers, on_progress=on_progress)
    except Exception as exc:
        # The message used to be echoed back, which handed the caller whatever
        # the outbound request saw. Operators read it in the log instead.
        _logger.warning("org scan failed for %s", url, exc_info=exc)
        raise ScanFailed("organization scan failed") from exc

    return await register_scanned_profiles(profiles, catalog)


async def perform_repo_scan(
    url: str,
    fetcher: object,
    catalog: RepositoryCatalog,
) -> ScanRegistration:
    """Scan one repository and register it."""

    from repomesh.modules.repository_intelligence.application.scan_remote import (  # noqa: PLC0415
        scan_single_repo,
    )

    try:
        profile = await scan_single_repo(url, fetcher)
    except Exception as exc:
        _logger.warning("repository scan failed for %s", url, exc_info=exc)
        raise ScanFailed("repository scan failed") from exc

    return await register_scanned_profiles([profile], catalog)


@router.post("/issues", dependencies=[ACTION_TOKEN])
async def create_issue(body: IssueIntakeCreate, request: Request) -> JSONResponse:
    """Contract v0.3 §1: create an issue (= first draft PlanSnapshot).

    201 on first creation, 200 on idempotent replay (Q5) — both bodies are the
    v0.2 §2 single-issue projection produced by the read model (no second
    serializer). Method-disjoint with the read model's GET /issues.

    Authorization status quo, spelled out (v0.3 §6 S-4): the action token is
    a single shared secret carrying no subject and no tenant — the acting
    subject comes from the body's ``created_by_agent_id``, the workspace from
    that agent's directory row. Containment applied: the service rejects a
    body ``organization_id`` that disagrees with the actor's organization
    (403), the idempotency keyspace is workspace-scoped, and a key replay is
    only answered within the owning workspace. Subject-carrying credentials
    (per-principal token / session ticket) are an adopted backlog item shared
    with the delivery write endpoints — an architecture change, not part of
    this containment."""

    service = request.app.state.container.issue_intake_service()
    try:
        receipt = await service.execute(
            IssueIntakeCommand(
                requirement_text=body.requirement_text,
                created_by_agent_id=body.created_by_agent_id,
                idempotency_key=body.idempotency_key,
                organization_id=body.organization_id,
            )
        )
    except IssueIntakeActorNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except IssueIntakeDenied as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    summary = await request.app.state.container.delivery_read_model_service().issue_summary(
        receipt.project_id
    )
    if summary is None:  # pragma: no cover - the snapshot was just persisted
        raise HTTPException(status_code=500, detail="issue projection unavailable")
    # The summary carries UUID/datetime values; the read-model routers lean on
    # FastAPI's default encoding, a manual JSONResponse must encode explicitly.
    return JSONResponse(
        status_code=201 if receipt.created else 200, content=jsonable_encoder(summary)
    )


@router.post(
    "/repositories", response_model=RepositoryView, status_code=201, dependencies=[ACTION_TOKEN]
)
async def register_repository(
    body: RepositoryCreate, catalog: CatalogDependency
) -> RepositoryProfile:
    profile = RepositoryProfile(
        name=body.name,
        url=str(body.url),
        description=body.description,
        topics=tuple(body.topics),
        languages=tuple(body.languages),
        test_commands=tuple(item.strip() for item in body.test_commands if item.strip()),
        auto_card=_build_auto_card(body.auto_card),
    )
    await RegisterRepository(catalog).execute(profile)
    return profile


@router.get("/repositories", response_model=list[RepositoryView])
async def list_repositories(catalog: CatalogDependency) -> list[RepositoryProfile]:
    return await catalog.list()


@router.get("/repositories/url-type", response_model=UrlIdentification)
async def identify_repository_url(url: str) -> UrlIdentification:
    """Say whether a URL points at an organization, a single repo, or neither.

    Read-only and offline: it parses the string and returns, so the console
    can call it on a debounce while the user is still typing. No request goes
    out, nothing is written, and no credential is involved — which is why it
    sits with the reads on this router rather than behind the action token.

    It answers for URLs this server would refuse to scan (a host off the
    allowlist still classifies as ``group``). Telling the user "that is an
    organization URL" and then refusing the scan is honest; making the badge
    double as an allowlist oracle would leak the operator's configuration to
    an endpoint that takes no credential.
    """

    from repomesh.modules.repository_intelligence.application.scan_remote import (  # noqa: PLC0415
        extract_entry_repo_name,
        identify_url_type,
    )
    from repomesh.modules.repository_intelligence.infrastructure.platform import (  # noqa: PLC0415
        detect_platform,
    )

    candidate = url.strip()
    url_type = identify_url_type(candidate)
    return UrlIdentification(
        url=candidate,
        url_type=url_type.value,
        platform=detect_platform(candidate).value,
        repository_name=(
            extract_entry_repo_name(candidate) if url_type is UrlType.SINGLE_REPO else None
        ),
    )


@router.post("/repositories/scan-org", response_model=OrgScanResult, dependencies=[ACTION_TOKEN])
async def scan_organization(body: OrgScanRequest, catalog: CatalogDependency) -> OrgScanResult:
    """Batch-scan all repos under a GitHub/GitLab organization.

    Fetches file trees, dependency files, and commits for every repo,
    builds AutoCards, and registers them in the catalog.

    Synchronous on purpose: this is the scripting/ops entry point and its
    callers want the counts in the response. The console's equivalent
    (``POST /console/repositories/scan-org``) returns 202 and a task instead,
    because a browser cannot hold a request open for a 40-repo organization.
    """
    url = str(body.org_url)
    fetcher = build_scan_fetcher(
        url,
        target="organization",
        github_token=body.github_token,
        gitlab_token=body.gitlab_token,
    )

    try:
        outcome = await perform_org_scan(url, fetcher, catalog, max_workers=body.max_workers)
    except ScanFailed as error:
        raise HTTPException(502, str(error)) from error

    return OrgScanResult(
        org_url=url,
        total_scanned=outcome.total_scanned,
        registered=len(outcome.registered),
        skipped=outcome.skipped,
        failed=outcome.failed,
        repositories=[RepositoryView.model_validate(p) for p in outcome.registered],
    )


@router.post("/repositories/scan-repo", response_model=RepoScanResult, dependencies=[ACTION_TOKEN])
async def scan_single_repository(
    body: RepoScanRequest, catalog: CatalogDependency
) -> RepoScanResult:
    """Scan one repository from its URL and register it.

    The sibling of ``scan-org`` for the case where the user has a single repo
    rather than a whole organization. Same SSRF allowlist, same silence about
    what the outbound request saw.

    Distinct from ``POST /repositories``, which registers a repository from
    fields the caller types out by hand. This one fetches the tree, the
    dependency files and the commits, and builds the AutoCard from what is
    actually in the repo.
    """
    url = str(body.repo_url).rstrip("/")
    require_single_repo_url(url)
    fetcher = build_scan_fetcher(
        url,
        target="repository",
        github_token=body.github_token,
        gitlab_token=body.gitlab_token,
    )

    try:
        outcome = await perform_repo_scan(url, fetcher, catalog)
    except ScanFailed as error:
        raise HTTPException(502, str(error)) from error

    return RepoScanResult(
        repo_url=url,
        total_scanned=outcome.total_scanned,
        registered=len(outcome.registered),
        skipped=outcome.skipped,
        failed=outcome.failed,
        repositories=[RepositoryView.model_validate(p) for p in outcome.registered],
    )


@router.post("/discovery", response_model=list[DiscoveryCandidate], dependencies=[ACTION_TOKEN])
async def discover_repositories(
    body: DiscoveryRequest, catalog: CatalogDependency, request: Request
) -> list[DiscoveryCandidate]:
    client = request.app.state.container.llm_client
    service = RepositoryDiscoveryService(catalog, llm_client=client)
    evidence = await service.discover(
        body.requirement,
        limit=body.limit,
        entry_point=body.entry_point,
    )
    profiles = {profile.id: profile for profile in await catalog.list()}
    return [
        DiscoveryCandidate(
            repository_id=item.repository_id,
            repository_name=profiles[item.repository_id].name,
            score=item.score,
            matched_terms=item.matched_terms,
            rationale=item.rationale,
            is_entry_point=item.is_entry_point,
        )
        for item in evidence
        if item.repository_id in profiles
    ]


@router.post(
    "/requirement-analysis", response_model=RequirementAnalysisView, dependencies=[ACTION_TOKEN]
)
async def analyze_requirement(
    body: RequirementAnalysisRequest, request: Request
) -> RequirementAnalysisView:
    """Evaluate whether a requirement has sufficient business information."""
    container = request.app.state.container
    analyzer = container.requirement_analyzer()
    if analyzer is None:
        raise HTTPException(
            status_code=503,
            detail="LLM not configured — requirement analysis unavailable",
        )
    result = analyzer.analyze(body.requirement)
    return RequirementAnalysisView(
        sufficient=result.sufficient,
        confidence=result.confidence,
        missing_dimensions=list(result.missing_dimensions),
        questions=list(result.questions),
        extracted_keywords=list(result.extracted_keywords),
    )


def _confirmation_summary_to_view(
    summary: ConfirmationSummary,
) -> ConfirmationSummaryView:
    return ConfirmationSummaryView(
        required=[ConfirmationResultView.model_validate(r) for r in summary.required],
        maybe=[ConfirmationResultView.model_validate(r) for r in summary.maybe],
        excluded=[ConfirmationResultView.model_validate(r) for r in summary.excluded],
        supplemented_repos=summary.supplemented_repos,
        final_repos=summary.final_repos,
    )


@router.post("/confirmation", response_model=ConfirmationSummaryView, dependencies=[ACTION_TOKEN])
async def confirm_repositories(
    body: ConfirmationRequest, request: Request
) -> ConfirmationSummaryView:
    """Phase 2: Team Managers confirm involvement and produce plans.

    Two refusals corrected here to match ``/requirement-analysis`` and the
    discovery chain (contract v0.4 Q12/Q13). Both used to reach the caller as
    500s: an unconfigured LLM blew up inside ``chat()``, and a candidate list
    with nothing in the catalog escaped as a bare ``ValueError``. Neither is a
    server fault — one is a deployment that has no model, the other is a
    request naming repositories nobody registered — and reporting them as
    crashes sent people looking for a bug instead of at their configuration or
    their request.
    """
    container = request.app.state.container
    llm = container.llm_client
    if llm is None:
        raise HTTPException(
            status_code=503,
            detail="LLM not configured — repository confirmation unavailable",
        )

    service = await container.confirmation_service(llm)
    profiles = service._profiles  # noqa: SLF001

    candidate_repos = [r for r in body.candidate_repos if r in profiles]
    if not candidate_repos:
        raise HTTPException(
            status_code=422,
            detail="No valid candidate repositories found in catalog",
        )

    evidence = {}
    for repo, val in body.discovery_evidence.items():
        if isinstance(val, (tuple, list)) and len(val) == 2:
            evidence[repo] = (str(val[0]), float(val[1]))
        elif isinstance(val, str):
            evidence[repo] = (val, 0.5)

    summary = service.confirm(
        candidate_repos,
        body.requirement,
        discovery_evidence=evidence,
    )
    return _confirmation_summary_to_view(summary)


def _summary_from_view(view: ConfirmationSummaryView) -> ConfirmationSummary:
    """Rebuild the application-level confirmation summary from its API view."""

    def _to_result(v: ConfirmationResultView) -> ConfirmationResult:
        plan = None
        if v.plan is not None:
            plan = RepositoryPlan(
                changed_apis=v.plan.changed_apis,
                changed_modules=v.plan.changed_modules,
                depends_on=v.plan.depends_on,
                impacts=v.plan.impacts,
                risk=v.plan.risk,
            )
        return ConfirmationResult(
            repository=v.repository,
            status=v.status,
            confidence=v.confidence,
            reason=v.reason,
            plan_summary=v.plan_summary,
            plan=plan,
            missing_dependencies=v.missing_dependencies,
        )

    return ConfirmationSummary(
        required=[_to_result(r) for r in view.required],
        maybe=[_to_result(r) for r in view.maybe],
        excluded=[_to_result(r) for r in view.excluded],
        supplemented_repos=view.supplemented_repos,
    )


@router.post("/integration", response_model=IntegratedPlanView, dependencies=[ACTION_TOKEN])
async def integrate_plan(body: IntegrationRequest, request: Request) -> IntegratedPlanView:
    """Phase 3: Leader integrates per-repo plans into a project-level plan.

    503 rather than a 500 from inside ``chat()`` when no model is configured
    (v0.4 Q12), matching ``/requirement-analysis``.
    """
    container = request.app.state.container
    llm = container.llm_client
    if llm is None:
        raise HTTPException(
            status_code=503,
            detail="LLM not configured — plan integration unavailable",
        )

    summary = _summary_from_view(body.confirmation)

    service = await container.plan_integration_service(llm)
    plan = service.integrate(body.requirement, summary)

    return IntegratedPlanView(
        engineering_spec=plan.engineering_spec,
        contracts=[
            ContractSpecView(
                producer=c.producer,
                consumer=c.consumer,
                interface=c.interface,
                agreement=c.agreement,
            )
            for c in plan.contracts
        ],
        task_dag=[
            TaskNodeView(
                repository=t.repository,
                instruction=t.instruction,
                depends_on=t.depends_on,
                parallelizable_with=t.parallelizable_with,
                tests=list(t.tests),
            )
            for t in plan.task_dag
        ],
        execution_batches=[list(b) for b in plan.execution_batches],
    )


@router.post("/bridge/materialize", response_model=MaterializeResponse, dependencies=[ACTION_TOKEN])
async def materialize_plan(body: MaterializeRequest, request: Request) -> MaterializeResponse:
    """Create Engineering Spec + Contract Specs + an execution plan from an IntegratedPlan."""
    container = request.app.state.container
    bridge = container.plan_execution_bridge()

    plan = IntegratedPlan(
        engineering_spec=body.engineering_spec,
        contracts=[
            ContractSpec(
                producer=c.producer,
                consumer=c.consumer,
                interface=c.interface,
                agreement=c.agreement,
            )
            for c in body.contracts
        ],
        task_dag=[
            TaskNode(
                repository=t.repository,
                instruction=t.instruction,
                depends_on=t.depends_on,
                parallelizable_with=t.parallelizable_with,
                tests=tuple(t.tests),
            )
            for t in body.task_dag
        ],
        execution_batches=[list(b) for b in body.execution_batches],
    )

    try:
        result = await bridge.materialize(
            plan=plan,
            requirement=body.requirement,
            project_id=body.project_id,
            leader_agent_id=body.leader_agent_id,
            idempotency_prefix=body.idempotency_prefix,
            repo_details={
                name: {
                    "changed_apis": tuple(detail.changed_apis),
                    "changed_modules": tuple(detail.changed_modules),
                    "risk": detail.risk,
                }
                for name, detail in body.repo_details.items()
            }
            or None,
        )
    except WorkflowBlocked as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ExecutionPlaneUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    return MaterializeResponse(
        engineering_spec_id=result.engineering_spec.id,
        contract_spec_ids=[s.id for s in result.contract_specs],
        task_ids=[t.id for t in result.tasks],
        skipped_repos=list(result.skipped_repos),
        plan_id=result.plan_id,
        handoff_doc_ids=list(result.handoff_doc_ids),
    )


@router.post("/bridge/replan", response_model=ReplanResponse, dependencies=[ACTION_TOKEN])
async def replan_plan(body: ReplanRequest, request: Request) -> ReplanResponse:
    """Trigger a partial replan based on TM feedback.

    Determines the affected repository set via dependency-graph reverse
    traversal, supersedes the old tasks of those repositories, and (when an
    integration service can be built) starts a new execution plan batch. The
    collaboration interrupt notices are pushed by the API layer using the
    ``affected_repos`` in the response.
    """

    container = request.app.state.container

    # The integration step needs an LLM; without it the bridge still performs
    # impact analysis and supersede, but cannot produce a new local plan.
    llm = container.llm_client
    integration_service = None
    confirmation_summary = None
    if llm is not None:
        integration_service = await container.plan_integration_service(llm)
        if body.confirmation is not None:
            confirmation_summary = _summary_from_view(body.confirmation)

    bridge = container.plan_execution_bridge()

    # Build the dependency graph from the live catalog for impact analysis.
    profiles = await container.repository_catalog.list()
    graph = DependencyGraphService(profiles) if profiles else None

    try:
        result = await bridge.replan(
            project_id=body.project_id,
            leader_agent_id=body.leader_agent_id,
            feedback=body.feedback,
            change_source_repo=body.change_source_repo,
            plan_version=body.plan_version,
            requirement=body.requirement,
            idempotency_prefix=body.idempotency_prefix,
            all_repos=[p.name for p in profiles],
            integration_service=integration_service,
            confirmation_summary=confirmation_summary,
            graph=graph,
        )
    except WorkflowBlocked as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ExecutionPlaneUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    return ReplanResponse(
        new_plan_version=result.new_plan_version,
        superseded_task_ids=[t.id for t in result.superseded_tasks],
        new_task_ids=[t.id for t in result.new_tasks],
        affected_repos=list(result.affected_repos),
        feedback_summary=result.feedback_summary,
        handoff_doc_ids=list(result.handoff_doc_ids),
    )


# ---------------------------------------------------------------------------
# Handoff documents (仓库对接文档 / human approval)
# ---------------------------------------------------------------------------


@router.get("/handoff-docs", response_model=list[HandoffDocView])
async def list_handoff_docs(
    request: Request,
    project_id: UUID,
    plan_version: int | None = None,
    repository: str | None = None,
    status: HandoffDocStatus | None = None,
) -> list[HandoffDocView]:
    """List the handoff documents of a project (optionally filtered).

    Repository owners use these documents to review *their* repository's
    proposed adjustment and approve or reject it.
    """

    container = request.app.state.container
    docs = await container.handoff_doc_service().list_docs(
        project_id=project_id,
        plan_version=plan_version,
        repository=repository,
        status=status,
    )
    return [HandoffDocView.model_validate(doc) for doc in docs]


@router.get("/handoff-docs/{doc_id}", response_model=HandoffDocView)
async def get_handoff_doc(doc_id: UUID, request: Request) -> HandoffDocView:
    """Fetch a single handoff document by id."""
    container = request.app.state.container
    doc = await container.handoff_doc_service().get_doc(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="handoff document not found")
    return HandoffDocView.model_validate(doc)


@router.get("/handoff-docs/{doc_id}/markdown", response_class=PlainTextResponse)
async def get_handoff_doc_markdown(doc_id: UUID, request: Request) -> PlainTextResponse:
    """Render a handoff document as Markdown for a repository owner to read."""
    container = request.app.state.container
    doc = await container.handoff_doc_service().get_doc(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="handoff document not found")
    return PlainTextResponse(render_markdown(doc))


@router.post(
    "/handoff-docs/{doc_id}/decision", response_model=HandoffDocView, dependencies=[ACTION_TOKEN]
)
async def decide_handoff_doc(
    doc_id: UUID, body: HandoffDocDecisionRequest, request: Request
) -> HandoffDocView:
    """Record a repository owner's manual approval or rejection.

    Only PENDING documents can be decided; a document superseded by a newer
    plan version must be re-decided on its regenerated copy.
    """

    container = request.app.state.container
    try:
        doc = await container.handoff_doc_service().decide(
            doc_id=doc_id,
            approved=body.approved,
            decided_by_agent_id=body.decided_by_agent_id,
            reason=body.reason,
        )
    except HandoffDocError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return HandoffDocView.model_validate(doc)


def build_execution_plan_status(
    snapshot: ExecutionPlanStatusSnapshot,
) -> ExecutionPlanStatusView:
    return ExecutionPlanStatusView(
        plan_id=snapshot.plan_id,
        status=snapshot.status.value,
        current_batch_index=snapshot.current_batch_index,
        batches=[
            [
                PlannedTaskStatusView(
                    repository_id=planned.repository_id,
                    leader_task_id=planned.leader_task_id,
                    leader_status=(
                        planned.leader_status.value if planned.leader_status is not None else None
                    ),
                    worker_tasks=[
                        WorkerTaskStatusView(task_id=worker.task_id, status=worker.status.value)
                        for worker in planned.worker_tasks
                    ],
                )
                for planned in batch
            ]
            for batch in snapshot.batches
        ],
    )


@router.get("/bridge/plans/{plan_id}", response_model=ExecutionPlanStatusView)
async def observe_execution_plan(plan_id: UUID, request: Request) -> ExecutionPlanStatusView:
    """Report how far an execution plan has progressed, task status included."""

    snapshot = await request.app.state.container.execution_plan_observer().execute(plan_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="execution plan not found")
    return build_execution_plan_status(snapshot)


@router.get("/dependency-graph", response_model=DependencyGraphView)
async def observe_dependency_graph(request: Request) -> DependencyGraphView:
    profiles = await request.app.state.container.repository_catalog.list()
    graph = DependencyGraphService(profiles)
    edges = [asdict(edge) for edge in graph.edges_in([profile.name for profile in profiles])]
    return DependencyGraphView(
        edges=edges,
        edge_count=graph.edge_count,
        confirmed_edge_count=graph.confirmed_edge_count,
    )


@router.get("/plans/{project_id}/latest", response_model=PlanSnapshotView)
async def latest_plan_snapshot(project_id: UUID, request: Request) -> PlanSnapshotView:
    snapshot = await request.app.state.container.plan_snapshot_store().get_latest(project_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="plan snapshot not found")
    return PlanSnapshotView.model_validate(snapshot)


@router.get("/plans/{project_id}/versions", response_model=list[PlanSnapshotSummaryView])
async def list_plan_snapshots(project_id: UUID, request: Request) -> list[PlanSnapshotSummaryView]:
    snapshots = await request.app.state.container.plan_snapshot_store().list_all(project_id)
    return [PlanSnapshotSummaryView.model_validate(snapshot) for snapshot in snapshots]


@router.get("/plans/{project_id}/versions/{version}", response_model=PlanSnapshotView)
async def get_plan_snapshot(project_id: UUID, version: int, request: Request) -> PlanSnapshotView:
    snapshot = await request.app.state.container.plan_snapshot_store().get_by_version(
        project_id, version
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail="plan snapshot not found")
    return PlanSnapshotView.model_validate(snapshot)
