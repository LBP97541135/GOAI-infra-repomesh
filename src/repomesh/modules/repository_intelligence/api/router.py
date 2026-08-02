from typing import Annotated

from fastapi import APIRouter, Depends, Request

from repomesh.modules.repository_intelligence.application import RepositoryDiscoveryService
from repomesh.modules.repository_intelligence.domain import RepositoryProfile
from repomesh.modules.repository_intelligence.ports import RepositoryCatalog

from .models import DiscoveryCandidate, DiscoveryRequest, RepositoryCreate, RepositoryView

router = APIRouter(tags=["repository-intelligence"])


def get_catalog(request: Request) -> RepositoryCatalog:
    return request.app.state.container.repository_catalog


CatalogDependency = Annotated[RepositoryCatalog, Depends(get_catalog)]


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
    )
    await catalog.add(profile)
    return profile


@router.get("/repositories", response_model=list[RepositoryView])
async def list_repositories(catalog: CatalogDependency) -> list[RepositoryProfile]:
    return await catalog.list()


@router.post("/discovery", response_model=list[DiscoveryCandidate])
async def discover_repositories(
    body: DiscoveryRequest, catalog: CatalogDependency
) -> list[DiscoveryCandidate]:
    evidence = await RepositoryDiscoveryService(catalog).discover(body.requirement, body.limit)
    candidates: list[DiscoveryCandidate] = []
    for item in evidence:
        profile = await catalog.get(item.repository_id)
        if profile is not None:
            candidates.append(
                DiscoveryCandidate(
                    repository_id=item.repository_id,
                    repository_name=profile.name,
                    score=item.score,
                    matched_terms=item.matched_terms,
                    rationale=item.rationale,
                )
            )
    return candidates
