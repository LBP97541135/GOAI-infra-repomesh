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
    await RegisterRepository(cat