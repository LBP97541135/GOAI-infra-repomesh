from typing import Annotated

from fastapi import APIRouter, Depends, Request

from repomesh.modules.repository_intelligence.domain import RepositoryProfile
from repomesh.modules.repository_intelligence.ports import RepositoryCatalog
from repomesh.modules.repository_intelligence.service import RepositoryDiscoveryService
from repomesh.modules.runtime.mock import MockCodingAgent
from repomesh.modules.runtime.ports import CodingRunRequest

from .models import (
    CodingRunCreate,
    CodingRunView,
    DiscoveryCandidate,
    DiscoveryRequest,
    RepositoryCreate,
    RepositoryView,
)

router = APIRouter()


def get_catalog(request: Request) -> RepositoryCatalog:
    return request.app.state.repository_catalog


CatalogDependency = Annotated[RepositoryCatalog, Depends(get_catalog)]


@router.get("/health/live", tags=["health"])
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", tags=["health"])
async def readiness() -> dict[str, str]:
    return {"status": "ready"}


@router.post("/api/v1/repositories", response_model=RepositoryView, status_code=201)
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


@router.get("/api/v1/repositories", response_model=list[RepositoryView])
async def list_repositories(
    catalog: CatalogDependency,
) -> list[RepositoryProfile]:
    return await catalog.list()


@router.post("/api/v1/discovery", response_model=list[DiscoveryCandidate])
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


@router.post("/api/v1/coding-runs/mock", response_model=CodingRunView, status_code=202)
async def run_mock_agent(body: CodingRunCreate) -> CodingRunView:
    agent = MockCodingAgent()
    result = await agent.execute(
        CodingRunRequest(
            task_id=body.task_id,
            repository_url=str(body.repository_url),
            instruction=body.instruction,
            base_revision=body.base_revision,
        )
    )
    return CodingRunView(
        run_id=result.run_id,
        status=result.status,
        adapter=agent.name,
        summary=result.summary,
        changed_files=result.changed_files,
        test_command=result.test_command,
    )

