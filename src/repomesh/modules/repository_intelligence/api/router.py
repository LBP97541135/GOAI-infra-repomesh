import os
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from repomesh.modules.repository_intelligence.application import (
    RegisterRepository,
    RepositoryDiscoveryService,
    make_llm_client,
)
from repomesh.modules.repository_intelligence.domain import AutoCard, RepositoryProfile
from repomesh.modules.repository_intelligence.ports import RepositoryCatalog

from .models import DiscoveryCandidate, DiscoveryRequest, RepositoryCreate, RepositoryView

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
