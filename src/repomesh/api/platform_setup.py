import asyncio
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import JSON, DateTime, Integer, String, Text, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.api.human_control import onboard_repository_agent_team
from repomesh.api.human_control_models import RepositoryAgentTeamOnboard
from repomesh.integrations.coding_agents import build_default_registry
from repomesh.modules.repository_intelligence.application import RegisterRepository
from repomesh.modules.repository_intelligence.application.scan_remote import scan_org
from repomesh.modules.repository_intelligence.infrastructure.platform import (
    detect_platform,
    make_fetcher,
)
from repomesh.persistence.base import Base
from repomesh.settings import get_settings

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


class RepositoryOnboardingJobRecord(Base):
    __tablename__ = "repository_onboarding_jobs"
    __table_args__ = ({"schema": "platform"},)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    org_url: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), index=True)
    phase: Mapped[str] = mapped_column(String(30))
    scan_workers: Mapped[int] = mapped_column(Integer)
    default_worker_count: Mapped[int] = mapped_column(Integer)
    requires_auth: Mapped[bool]
    results: Mapped[list[dict[str, object]]] = mapped_column(JSON_DOCUMENT, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

router = APIRouter(prefix="/api/v1/setup", tags=["platform-setup"])


class OrganizationRepositoryOnboard(BaseModel):
    organization_id: UUID
    org_url: HttpUrl
    github_token: str = ""
    gitlab_token: str = ""
    scan_workers: int = Field(default=5, ge=1, le=20)
    default_worker_count: int = Field(default=1, ge=1, le=20)


def _job_view(record: RepositoryOnboardingJobRecord) -> dict:
    return {
        "id": str(record.id),
        "organization_id": str(record.organization_id),
        "org_url": record.org_url,
        "status": record.status,
        "phase": record.phase,
        "requires_auth": record.requires_auth,
        "results": record.results,
        "error": record.error,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


async def _run_onboarding_job(
    job_id: UUID,
    body: OrganizationRepositoryOnboard,
    request: Request,
) -> None:
    database = request.app.state.container.database
    async def update(**values: object) -> None:
        async with database.transaction() as session:
            record = await session.get(RepositoryOnboardingJobRecord, job_id)
            if record:
                for key, value in values.items():
                    setattr(record, key, value)
                record.updated_at = datetime.now(UTC)
    try:
        await update(status="running", phase="scanning", error=None)
        platform = detect_platform(str(body.org_url))
        if platform.value == "local":
            raise ValueError("organization URL must be GitHub or GitLab")
        fetcher = make_fetcher(
            platform,
            github_token=body.github_token,
            gitlab_token=body.gitlab_token,
        )
        profiles = await scan_org(
            str(body.org_url), fetcher, max_workers=body.scan_workers
        )
        await update(phase="registering")
        catalog = request.app.state.container.repository_catalog
        existing = {item.url: item for item in await catalog.list()}
        registered_repositories = []
        for profile in profiles:
            registered = existing.get(profile.url)
            if registered is None:
                await RegisterRepository(catalog).execute(profile)
                registered = profile
            registered_repositories.append((registered, registered is profile))
        await update(phase="teaming")
        results: list[dict[str, object]] = []
        for registered, created in registered_repositories:
            try:
                await onboard_repository_agent_team(
                    registered.id,
                    RepositoryAgentTeamOnboard(
                        organization_id=body.organization_id,
                        worker_count=body.default_worker_count,
                        responsibility_paths=["**"],
                        idempotency_key=f"repository-onboarding:{registered.id}",
                    ),
                    request,
                )
                results.append(
                    {
                        "repository_id": str(registered.id),
                        "repository_name": registered.name,
                        "scan": "created" if created else "reused",
                        "agent_team": "ready",
                    }
                )
            except HTTPException as error:
                results.append(
                    {
                        "repository_id": str(registered.id),
                        "repository_name": registered.name,
                        "scan": "created" if created else "reused",
                        "agent_team": "failed",
                        "detail": str(error.detail),
                    }
                )
            await update(results=results)
        await update(status="completed", phase="done", results=results)
    except Exception as error:
        await update(
            status="failed",
            phase="authorization" if body.github_token or body.gitlab_token else "failed",
            error=str(error),
        )


@router.post("/repositories/onboarding-jobs", status_code=202)
async def create_onboarding_job(
    body: OrganizationRepositoryOnboard,
    request: Request,
    tasks: BackgroundTasks,
) -> dict:
    actor = await _authenticated_account(request)
    if not actor.is_admin:
        raise HTTPException(status_code=403, detail="local administrator permission is required")
    job_id = uuid4()
    now = datetime.now(UTC)
    async with request.app.state.container.database.transaction() as session:
        session.add(
            RepositoryOnboardingJobRecord(
                id=job_id,
                organization_id=body.organization_id,
                org_url=str(body.org_url),
                status="queued",
                phase="queued",
                scan_workers=body.scan_workers,
                default_worker_count=body.default_worker_count,
                requires_auth=bool(body.github_token or body.gitlab_token),
                results=[],
                error=None,
                created_at=now,
                updated_at=now,
            )
        )
    tasks.add_task(_run_onboarding_job, job_id, body, request)
    return {"id": str(job_id), "status": "queued", "phase": "queued"}


@router.get("/repositories/onboarding-jobs/{job_id}")
async def get_onboarding_job(job_id: UUID, request: Request) -> dict:
    await _authenticated_account(request)
    async with request.app.state.container.database.transaction() as session:
        record = await session.get(RepositoryOnboardingJobRecord, job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="onboarding job does not exist")
    return _job_view(record)


@router.get("/repositories/onboarding-jobs")
async def list_onboarding_jobs(request: Request) -> list[dict]:
    await _authenticated_account(request)
    async with request.app.state.container.database.transaction() as session:
        records = (
            await session.scalars(
                select(RepositoryOnboardingJobRecord).order_by(
                    RepositoryOnboardingJobRecord.updated_at.desc()
                )
            )
        ).all()
    return [_job_view(record) for record in records]


@router.post("/repositories/onboarding-jobs/{job_id}/retry", status_code=202)
async def retry_onboarding_job(
    job_id: UUID,
    body: OrganizationRepositoryOnboard,
    request: Request,
    tasks: BackgroundTasks,
) -> dict:
    actor = await _authenticated_account(request)
    if not actor.is_admin:
        raise HTTPException(status_code=403, detail="local administrator permission is required")
    async with request.app.state.container.database.transaction() as session:
        record = await session.get(RepositoryOnboardingJobRecord, job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="onboarding job does not exist")
        if record.status not in {"failed", "interrupted"}:
            raise HTTPException(status_code=409, detail="only failed jobs can be retried")
        if record.organization_id != body.organization_id or record.org_url != str(body.org_url):
            raise HTTPException(
                status_code=422,
                detail="retry parameters must match the original job",
            )
        record.status = "queued"
        record.phase = "queued"
        record.error = None
        record.updated_at = datetime.now(UTC)
    tasks.add_task(_run_onboarding_job, job_id, body, request)
    return {"id": str(job_id), "status": "queued", "phase": "queued"}


@router.post("/repositories/onboard")
async def onboard_organization_repositories(
    body: OrganizationRepositoryOnboard,
    request: Request,
) -> dict:
    actor = await _authenticated_account(request)
    if not actor.is_admin:
        raise HTTPException(status_code=403, detail="local administrator permission is required")
    platform = detect_platform(str(body.org_url))
    if platform.value == "local":
        raise HTTPException(status_code=400, detail="organization URL must be GitHub or GitLab")
    fetcher = make_fetcher(
        platform,
        github_token=body.github_token,
        gitlab_token=body.gitlab_token,
    )
    profiles = await scan_org(str(body.org_url), fetcher, max_workers=body.scan_workers)
    catalog = request.app.state.container.repository_catalog
    existing = {item.url: item for item in await catalog.list()}
    results = []
    for profile in profiles:
        registered = existing.get(profile.url)
        if registered is None:
            await RegisterRepository(catalog).execute(profile)
            registered = profile
        try:
            team = await onboard_repository_agent_team(
                registered.id,
                RepositoryAgentTeamOnboard(
                    organization_id=body.organization_id,
                    worker_count=body.default_worker_count,
                    responsibility_paths=["**"],
                    idempotency_key=f"repository-onboarding:{registered.id}",
                ),
                request,
            )
            results.append(
                {
                    "repository_id": str(registered.id),
                    "repository_name": registered.name,
                    "scan": "created" if registered is profile else "reused",
                    "agent_team": "ready",
                    "team": team,
                }
            )
        except HTTPException as error:
            results.append(
                {
                    "repository_id": str(registered.id),
                    "repository_name": registered.name,
                    "scan": "created" if registered is profile else "reused",
                    "agent_team": "failed",
                    "detail": str(error.detail),
                }
            )
    return {"organization_id": str(body.organization_id), "repositories": results}


async def _authenticated_account(request: Request):
    authorization = request.headers.get("Authorization", "")
    token = (
        authorization.removeprefix("Bearer ").strip()
        if authorization.startswith("Bearer ")
        else request.cookies.get("repomesh_session")
    )
    if not token:
        raise HTTPException(status_code=401, detail="local authentication is required")
    try:
        return await request.app.state.container.local_account_service().authenticate(token)
    except Exception as error:
        raise HTTPException(status_code=401, detail="invalid local session") from error


@router.get("/coding-agents")
async def probe_coding_agents() -> dict:
    """Probe the environment where the RepoMesh API process is running."""
    registry = build_default_registry()
    probes = await asyncio.gather(
        *(registry.resolve(item.id).probe() for item in registry.list_manifests())
    )
    manifests = {item.id: item for item in registry.list_manifests()}
    return {
        "environment": "repomesh-api",
        "note": "Runner containers must expose their own probe before remote execution.",
        "adapters": [
            {
                **asdict(probe),
                "display_name": manifests[probe.adapter_id].display_name,
                "execution_status": manifests[probe.adapter_id].execution_status,
                "runnable_by_verified_driver": (
                    manifests[probe.adapter_id].execution_status == "superseded_by_driver"
                ),
            }
            for probe in probes
        ],
    }


@router.get("/status")
async def setup_status(request: Request) -> dict:
    settings = get_settings()
    container = request.app.state.container
    accounts = await container.local_account_service().list_accounts()
    agents = await container.agent_directory.list_views()
    repositories = await container.repository_catalog.list()
    checks = {
        "model": bool(settings.deepseek_api_key),
        "database": await container.database.is_ready(),
        "agentteams": await container.is_agentteams_ready(),
        "matrix": container.agent_team_messenger is not None,
        "internal_auth": bool(settings.runner_control_token and settings.agent_action_token),
        "github_app": bool(
            settings.github_app_id
            and (
                settings.github_app_private_key_file
                or settings.github_app_private_key_base64
            )
        ),
        "administrator": bool(accounts),
        "agent_directory": bool(agents),
        "repositories": bool(repositories),
    }
    required = ("model", "database", "agentteams", "matrix", "internal_auth")
    return {
        "ready_for_project_creation": all(checks[name] for name in required),
        "checks": checks,
        "counts": {
            "accounts": len(accounts),
            "agents": len(agents),
            "repositories": len(repositories),
        },
        "next_actions": [name for name, passed in checks.items() if not passed],
    }
