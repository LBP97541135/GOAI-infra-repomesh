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


def _build_auto_card(body_card) -> AutoCard | None:  # type: ignore[no-untyped-def]
    """Convert the API-layer ``AutoCardCreate`` to a domain ``AutoCard``."""

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
    # Read DeepSeek configuration from environment so the API stays stateless.
    api_key = os.environ.get("REPOMESH_DEEP