import os
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from repomesh.modules.repository_intelligence.application import (
    ConfirmationService,
    PlanIntegrationService,
    RegisterRepository,
    RepositoryDiscoveryService,
    make_llm_client,
)
from repomesh.modules.repository_intelligence.application.confirmation import (
    ConfirmationResult,
    ConfirmationSummary,
    RepositoryPlan,
)
from repomesh.modules.repository_intelligence.application.plan_integration import (
    ContractSpec,
    IntegratedPlan,
    TaskNode,
)
from repomesh.modules.repository_intelligence.domain import AutoCard, RepositoryProfile
from repomesh.modules.repository_intelligence.ports import RepositoryCatalog

from .models import (
    ConfirmationRequest,
    ConfirmationResultView,
    ConfirmationSummaryView,
    ContractSpecView,
    DiscoveryCandidate,
    DiscoveryRequest,
    IntegratedPlanView,
    IntegrationRequest,
    MaterializeRequest,
    RepositoryCreate,
    RepositoryView,
    TaskNodeView,
)

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


@router.post("/discovery", response_model=list[DiscoveryCandidate])
async def discover_repositories(
    body: DiscoveryRequest, catalog: CatalogDependency
) -> list[DiscoveryCandidate]:
    client = make_llm_client(
        os.environ.get("DEEPSEEK_API_KEY"),
        base_url=os.environ.get("REPOMESH_DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        model=os.environ.get("REPOMESH_DEEPSEEK_MODEL", "deepseek-chat"),
    )
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


def _make_llm_client():  # noqa: ANN202
    return make_llm_client(
        os.environ.get("DEEPSEEK_API_KEY"),
        base_url=os.environ.get(
            "REPOMESH_DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"
        ),
        model=os.environ.get("REPOMESH_DEEPSEEK_MODEL", "deepseek-chat"),
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
    catalog = request.app.state.container.repository_catalog
    llm = _make_llm_client()

    profiles = {p.name: p for p in await catalog.list()}

    candidate_repos = [r for r in body.candidate_repos if r in profiles]
    if not candidate_repos:
        raise ValueError("No valid candidate repositories found in catalog")

    evidence = {}
    for repo, val in body.discovery_evidence.items():
        if isinstance(val, (tuple, list)) and len(val) == 2:
            evidence[repo] = (str(val[0]), float(val[1]))
        elif isinstance(val, str):
            evidence[repo] = (val, 0.5)

    service = ConfirmationService(llm, profiles)
    summary = service.confirm(
        candidate_repos,
        body.requirement,
        discovery_evidence=evidence,
    )
    return _confirmation_summary_to_view(summary)


@router.post("/integration", response_model=IntegratedPlanView)
async def integrate_plan(body: IntegrationRequest) -> IntegratedPlanView:
    """Phase 3: Leader integrates per-repo plans into a project-level plan."""
    llm = _make_llm_client()

    def _to_result(v: ConfirmationResultView) -> ConfirmationResult:  # noqa: ANN202
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

    summary = ConfirmationSummary(
        required=[_to_result(r) for r in body.confirmation.required],
        maybe=[_to_result(r) for r in body.confirmation.maybe],
        excluded=[_to_result(r) for r in body.confirmation.excluded],
        supplemented_repos=body.confirmation.supplemented_repos,
    )

    service = PlanIntegrationService(llm)
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
            )
            for t in plan.task_dag
        ],
        execution_batches=[list(b) for b in plan.execution_batches],
    )


@router.post("/bridge/materialize")
async def materialize_plan(body: MaterializeRequest, request: Request) -> dict:
    """Create Engineering Spec + Contract Specs + Tasks from an IntegratedPlan."""
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
            )
            for t in body.task_dag
        ],
        execution_batches=[list(b) for b in body.execution_batches],
    )

    result = await bridge.materialize(
        plan=plan,
        requirement=body.requirement,
        project_id=body.project_id,
        leader_agent_id=body.leader_agent_id,
        idempotency_prefix=body.idempotency_prefix,
    )

    return {
        "engineering_spec_id": str(result.engineering_spec.id),
        "contract_spec_ids": [str(s.id) for s in result.contract_specs],
        "task_ids": [str(t.id) for t in result.tasks],
        "skipped_repos": result.skipped_repos,
    }
