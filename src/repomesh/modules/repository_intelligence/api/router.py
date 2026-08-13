from dataclasses import asdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from repomesh.modules.change_orchestration import ExecutionPlaneUnavailable
from repomesh.modules.repository_intelligence.application import (
    DependencyGraphService,
    HandoffDocError,
    RegisterRepository,
    RepositoryDiscoveryService,
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
from repomesh.modules.repository_intelligence.contracts import (
    PlanDiff,
    diff_plan_graphs,
)
from repomesh.modules.repository_intelligence.domain import AutoCard, RepositoryProfile
from repomesh.modules.repository_intelligence.infrastructure.plan_snapshot_store import (
    plan_graph_from_snapshot,
)
from repomesh.modules.repository_intelligence.ports import RepositoryCatalog
from repomesh.modules.task_orchestration.contracts import ExecutionPlanStatusSnapshot
from repomesh.settings import get_settings
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
    MaterializeRequest,
    MaterializeResponse,
    OrgScanRequest,
    OrgScanResult,
    PlannedTaskStatusView,
    PlanSnapshotSummaryView,
    PlanSnapshotView,
    ReplanRequest,
    ReplanResponse,
    RepositoryCreate,
    RepositoryView,
    RequirementAnalysisRequest,
    RequirementAnalysisView,
    TaskNodeView,
    WorkerTaskStatusView,
)

router = APIRouter(tags=["repository-intelligence"])


def get_catalog(request: Request) -> RepositoryCatalog:
    return request.app.state.container.repository_catalog


CatalogDependency = Annotated[RepositoryCatalog, Depends(get_catalog)]


def _resolve_replan_mode(requested: str, auto_commit: bool) -> str:
    """Resolve the replan request mode after ``auto`` (PR-4).

    ``auto`` follows the server setting ``REPOMESH_REPLAN_AUTO_COMMIT``:
    ``True`` preserves the pre-PR-4 behaviour (immediate commit); ``False``
    requires an explicit approval round-trip (the ``auto`` request runs in
    ``preview`` mode and a second call with ``mode=commit`` applies it).
    """
    if requested == "auto":
        return "commit" if auto_commit else "preview"
    return requested


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


@router.post("/repositories", response_model=RepositoryView, status_code=201)
async def register_repository(
    body: RepositoryCreate, catalog: CatalogDependency
) -> RepositoryProfile:
    profile = RepositoryProfile(
        name=body.name,
        url=str(body.url),
        description=body.description,
        topics=tuple(body.topics),
        languages=tuple(body.languages),
        auto_card=_build_auto_card(body.auto_card),
    )
    await RegisterRepository(catalog).execute(profile)
    return profile


@router.get("/repositories", response_model=list[RepositoryView])
async def list_repositories(catalog: CatalogDependency) -> list[RepositoryProfile]:
    return await catalog.list()


@router.post("/repositories/scan-org", response_model=OrgScanResult)
async def scan_organization(body: OrgScanRequest, catalog: CatalogDependency) -> OrgScanResult:
    """Batch-scan all repos under a GitHub/GitLab organization.

    Fetches file trees, dependency files, and commits for every repo,
    builds AutoCards, and registers them in the catalog.
    """
    from repomesh.modules.repository_intelligence.application.scan_remote import (
        scan_org,
    )
    from repomesh.modules.repository_intelligence.infrastructure.platform import (
        detect_platform,
        make_fetcher,
    )

    url = str(body.org_url)
    platform = detect_platform(url)
    if platform.value == "local":
        raise HTTPException(400, "URL must be a GitHub/GitLab organization URL")

    fetcher = make_fetcher(
        platform,
        github_token=body.github_token,
        gitlab_token=body.gitlab_token,
    )

    try:
        profiles = await scan_org(url, fetcher, max_workers=body.max_workers)
    except Exception as exc:
        raise HTTPException(502, f"Scan failed: {exc}") from exc

    register = RegisterRepository(catalog)
    registered: list[RepositoryView] = []
    skipped = 0
    failed = 0

    existing = {p.name for p in await catalog.list()}

    for profile in profiles:
        if profile.name in existing:
            skipped += 1
            continue
        try:
            await register.execute(profile)
            registered.append(RepositoryView.model_validate(profile))
        except Exception:
            failed += 1

    return OrgScanResult(
        org_url=url,
        total_scanned=len(profiles),
        registered=len(registered),
        skipped=skipped,
        failed=failed,
        repositories=registered,
    )


@router.post("/discovery", response_model=list[DiscoveryCandidate])
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


@router.post("/requirement-analysis", response_model=RequirementAnalysisView)
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


@router.post("/confirmation", response_model=ConfirmationSummaryView)
async def confirm_repositories(
    body: ConfirmationRequest, request: Request
) -> ConfirmationSummaryView:
    """Phase 2: Team Managers confirm involvement and produce plans."""
    container = request.app.state.container
    llm = container.llm_client

    service = await container.confirmation_service(llm)
    profiles = service._profiles  # noqa: SLF001

    candidate_repos = [r for r in body.candidate_repos if r in profiles]
    if not candidate_repos:
        raise ValueError("No valid candidate repositories found in catalog")

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


@router.post("/integration", response_model=IntegratedPlanView)
async def integrate_plan(body: IntegrationRequest, request: Request) -> IntegratedPlanView:
    """Phase 3: Leader integrates per-repo plans into a project-level plan."""
    container = request.app.state.container
    llm = container.llm_client

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
        # Single source of truth for PR-5 frontends: the graph carries nodes,
        # edges and the materialised projections the top-level fields mirror.
        graph=plan.graph,
    )


@router.post("/bridge/materialize", response_model=MaterializeResponse)
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


@router.post("/bridge/replan", response_model=ReplanResponse)
async def replan_plan(body: ReplanRequest, request: Request) -> ReplanResponse:
    """Trigger a partial replan based on TM feedback.

    The affected repository set is derived from the latest immutable
    plan-layer snapshot (confirmed edges only) when one exists, falling back
    to world-layer dependency-graph reverse traversal otherwise. In ``commit``
    mode (the default when ``REPOMESH_REPLAN_AUTO_COMMIT`` is enabled) old
    tasks of the affected repositories are superseded and, when an
    integration service can be built, a new execution plan batch is started
    and persisted as a new immutable snapshot — the response carries the
    persisted snapshot ``plan_id`` and the graph ``diff``. In ``preview`` mode
    the same change footprint and diff are computed with **zero side
    effects**, so callers can approve before committing. The collaboration
    interrupt notices are pushed by the API layer using the ``affected_repos``
    in the response.
    """

    container = request.app.state.container
    mode = _resolve_replan_mode(body.mode, get_settings().replan_auto_commit)

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

    # World-layer graph as a fallback for impact analysis; the plan-layer
    # snapshot (read inside the bridge) takes precedence when available.
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
            mode=mode,
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
        plan_id=result.plan_id,
        mode=mode,
        diff=result.diff,
    )


@router.get("/plans/{project_id}/diff", response_model=PlanDiff)
async def diff_plan_versions(
    project_id: UUID,
    request: Request,
    from_version: int | None = Query(default=None, alias="from", ge=1),
    to_version: int | None = Query(default=None, alias="to", ge=1),
) -> PlanDiff:
    """Graph diff between two plan-layer snapshot versions (PR-4).

    Pure read: zero side effects and idempotent. Defaults: ``to`` = the
    latest version; ``from`` = the version immediately before ``to``. There
    is no version 0, so a default ``from`` on a one-version project returns
    404; the caller must then name both versions explicitly.
    """

    container = request.app.state.container
    store = container.plan_snapshot_store()
    versions = await store.list_all(project_id)
    if not versions:
        raise HTTPException(status_code=404, detail="no plan snapshots for project")
    latest = versions[0].plan_version

    to = latest if to_version is None else to_version
    from_v = to - 1 if from_version is None else from_version

    from_record = await store.get_by_version(project_id, from_v)
    to_record = await store.get_by_version(project_id, to)
    if to_record is None:
        raise HTTPException(
            status_code=404, detail=f"no plan snapshot v{to} for project"
        )
    if from_record is None:
        raise HTTPException(
            status_code=404, detail=f"no plan snapshot v{from_v} for project"
        )
    diff = diff_plan_graphs(
        plan_graph_from_snapshot(from_record),
        plan_graph_from_snapshot(to_record),
    )
    assert diff is not None  # both records exist, so both graphs exist
    return diff


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


@router.post("/handoff-docs/{doc_id}/decision", response_model=HandoffDocView)
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
