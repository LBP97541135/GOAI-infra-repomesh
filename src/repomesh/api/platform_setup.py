import asyncio
from dataclasses import asdict
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, HttpUrl

from repomesh.api.human_control import onboard_repository_agent_team
from repomesh.api.human_control_models import RepositoryAgentTeamOnboard
from repomesh.integrations.coding_agents import build_default_registry
from repomesh.modules.repository_intelligence.application import RegisterRepository
from repomesh.modules.repository_intelligence.application.scan_remote import scan_org
from repomesh.modules.repository_intelligence.infrastructure.platform import (
    detect_platform,
    make_fetcher,
)
from repomesh.settings import get_settings

router = APIRouter(prefix="/api/v1/setup", tags=["platform-setup"])


class OrganizationRepositoryOnboard(BaseModel):
    organization_id: UUID
    org_url: HttpUrl
    github_token: str = ""
    gitlab_token: str = ""
    scan_workers: int = Field(default=5, ge=1, le=20)
    default_worker_count: int = Field(default=1, ge=1, le=20)


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
    try:
        profiles = await scan_org(
            str(body.org_url),
            fetcher,
            max_workers=body.scan_workers,
            include_forks=get_settings().repository_scan_include_forks,
        )
    finally:
        # The fetcher owns a pooled HTTP client; release it once the scan
        # that drives hundreds of API calls has finished.
        await fetcher.aclose()
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
