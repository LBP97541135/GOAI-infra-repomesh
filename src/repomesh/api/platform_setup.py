import asyncio
from dataclasses import asdict
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, HttpUrl

from repomesh.api.human_control import onboard_repository_agent_team
from repomesh.api.human_control_models import RepositoryAgentTeamOnboard
from repomesh.integrations.coding_agents import build_default_registry
from repomesh.modules.platform_config import (
    GITHUB_APP_ID,
    GITHUB_PRIVATE_KEY,
    MODEL_API_KEY,
    BootstrapState,
)
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
    credentials = await container.platform_credential_store().get_many(
        {MODEL_API_KEY, GITHUB_APP_ID, GITHUB_PRIVATE_KEY}
    )
    bootstrap_operation = await container.bootstrap_operation_store().latest()
    checks = {
        "model": bool(credentials.get(MODEL_API_KEY) or settings.deepseek_api_key),
        "database": await container.database.is_ready(),
        "agentteams": await container.is_agentteams_available(),
        "matrix": container.agent_team_messenger is not None,
        "internal_auth": bool(settings.runner_control_token and settings.agent_action_token),
        "github_app": bool(
            (credentials.get(GITHUB_APP_ID) and credentials.get(GITHUB_PRIVATE_KEY))
            or (
                settings.github_app_id
                and (
                    settings.github_app_private_key_file
                    or settings.github_app_private_key_base64
                )
            )
        ),
        "administrator": bool(accounts),
        "agent_directory": bool(agents),
        "repositories": bool(repositories),
    }
    required = (
        "model",
        "database",
        "agentteams",
        "matrix",
        "internal_auth",
        "administrator",
    )

    def dependency(
        dependency_id: str,
        *,
        owner: str,
        remediation: str,
        required_for_project: bool,
        message: str,
        state_override: str | None = None,
    ) -> dict:
        ready = checks[dependency_id]
        if state_override is not None and not ready:
            state = state_override
        elif ready:
            state = "ready"
        elif remediation == "user_input":
            state = "waiting_for_user"
        elif remediation == "optional":
            state = "optional"
        elif remediation == "workflow":
            state = "pending_onboarding"
        else:
            state = "missing"
        return {
            "id": dependency_id,
            "state": state,
            "owner": owner,
            "remediation": remediation,
            "required": required_for_project,
            "message": message,
        }

    bootstrap_dependency_state = None
    if bootstrap_operation is not None:
        if bootstrap_operation.state in {BootstrapState.PENDING, BootstrapState.RUNNING}:
            bootstrap_dependency_state = "repairing"
        elif bootstrap_operation.state in {
            BootstrapState.RETRYABLE_FAILURE,
            BootstrapState.TERMINAL_FAILURE,
        }:
            bootstrap_dependency_state = "failed"

    dependencies = [
        dependency(
            "database",
            owner="system",
            remediation="automatic",
            required_for_project=True,
            message="managed by the RepoMesh product launcher",
        ),
        dependency(
            "agentteams",
            owner="system",
            remediation="automatic",
            required_for_project=True,
            message="installed and started automatically",
            state_override=bootstrap_dependency_state,
        ),
        dependency(
            "matrix",
            owner="system",
            remediation="automatic",
            required_for_project=True,
            message="managed by the AgentTeams installation",
            state_override=bootstrap_dependency_state,
        ),
        dependency(
            "internal_auth",
            owner="system",
            remediation="automatic",
            required_for_project=True,
            message="generated and synchronized automatically",
        ),
        dependency(
            "model",
            owner="user",
            remediation="user_input",
            required_for_project=True,
            message="a model API key is required",
        ),
        dependency(
            "administrator",
            owner="user",
            remediation="user_input",
            required_for_project=True,
            message="the first local administrator is created at sign-in",
        ),
        dependency(
            "github_app",
            owner="user",
            remediation="optional",
            required_for_project=False,
            message="optional unless GitHub delivery is enabled",
        ),
        dependency(
            "repositories",
            owner="onboarding",
            remediation="workflow",
            required_for_project=False,
            message="created during repository onboarding",
        ),
        dependency(
            "agent_directory",
            owner="onboarding",
            remediation="workflow",
            required_for_project=False,
            message="created when repository teams are onboarded",
        ),
    ]
    return {
        "ready_for_project_creation": all(checks[name] for name in required),
        "checks": checks,
        "dependencies": dependencies,
        "counts": {
            "accounts": len(accounts),
            "agents": len(agents),
            "repositories": len(repositories),
        },
        "next_actions": [name for name, passed in checks.items() if not passed],
    }
