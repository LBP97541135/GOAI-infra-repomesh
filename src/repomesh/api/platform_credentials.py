import asyncio
import os

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from repomesh.modules.platform_config import (
    ALLOWED_KEYS,
    GITHUB_APP_ID,
    GITHUB_PRIVATE_KEY,
    GITHUB_WEBHOOK_SECRET,
    MODEL_API_KEY,
    MODEL_BASE_URL,
    MODEL_NAME,
)
from repomesh.settings import get_settings

router = APIRouter(prefix="/api/v1/setup/credentials", tags=["platform-credentials"])


class ModelCredentialUpdate(BaseModel):
    api_key: str = Field(min_length=1, max_length=8192)
    base_url: str | None = Field(default=None, max_length=2048)
    model: str | None = Field(default=None, max_length=255)

    @field_validator("api_key")
    @classmethod
    def api_key_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("api_key must not be blank")
        return value.strip()


class GitHubAppCredentialUpdate(BaseModel):
    app_id: int = Field(gt=0)
    private_key_pem: str = Field(min_length=1, max_length=65536)
    webhook_secret: str | None = Field(default=None, max_length=8192)

    @field_validator("private_key_pem")
    @classmethod
    def valid_private_key(cls, value: str) -> str:
        normalized = value.strip()
        if "-----BEGIN" not in normalized or "PRIVATE KEY-----" not in normalized:
            raise ValueError("private_key_pem must be a PEM private key")
        return normalized + "\n"


async def _admin(request: Request):
    authorization = request.headers.get("Authorization", "")
    token = (
        authorization.removeprefix("Bearer ").strip()
        if authorization.startswith("Bearer ")
        else request.cookies.get("repomesh_session")
    )
    if not token:
        raise HTTPException(status_code=401, detail="local authentication is required")
    try:
        actor = await request.app.state.container.local_account_service().authenticate(token)
    except Exception as error:
        raise HTTPException(status_code=401, detail="invalid local session") from error
    if not actor.is_admin:
        raise HTTPException(status_code=403, detail="local administrator permission is required")
    return actor


def _mask(value: str) -> str:
    if len(value) <= 4:
        return "****"
    return f"****{value[-4:]}"


def _summary(values) -> dict:
    def item(key: str, *, mask: bool = False) -> dict:
        stored = values.get(key)
        return {
            "set": stored is not None,
            "masked": _mask(stored.value) if stored is not None and mask else None,
            "updated_at": stored.updated_at.isoformat() if stored is not None else None,
        }

    return {
        "model": {
            "api_key": item(MODEL_API_KEY, mask=True),
            "base_url": item(MODEL_BASE_URL),
            "model": item(MODEL_NAME),
        },
        "github_app": {
            "app_id": item(GITHUB_APP_ID, mask=True),
            "private_key": item(GITHUB_PRIVATE_KEY, mask=True),
            "webhook_secret": item(GITHUB_WEBHOOK_SECRET, mask=True),
        },
    }


@router.get("")
async def credential_status(request: Request) -> dict:
    await _admin(request)
    values = await request.app.state.container.platform_credential_store().get_many(ALLOWED_KEYS)
    return _summary(values)


def _restart_receipt(background_tasks: BackgroundTasks) -> dict:
    settings = get_settings()
    get_settings.cache_clear()
    if settings.supervised:
        background_tasks.add_task(_exit_after_response)
    return {
        "saved": True,
        "restarting": settings.supervised,
        "restart_required": not settings.supervised,
    }


async def _exit_after_response() -> None:
    await asyncio.sleep(0.25)
    os._exit(0)


@router.put("/model")
async def put_model_credentials(
    body: ModelCredentialUpdate,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    actor = await _admin(request)
    values = {MODEL_API_KEY: body.api_key}
    if body.base_url is not None:
        values[MODEL_BASE_URL] = body.base_url.strip()
    if body.model is not None:
        values[MODEL_NAME] = body.model.strip()
    await request.app.state.container.platform_credential_store().put_many(
        values, updated_by=actor.id
    )
    container = request.app.state.container
    execution_plane_ready = (
        await container.is_agentteams_available() and container.agent_team_messenger is not None
    )
    if not execution_plane_ready:
        await container.bootstrap_operation_store().ensure_requested(requested_by=actor.id)
    return _restart_receipt(background_tasks)


@router.put("/github-app")
async def put_github_app_credentials(
    body: GitHubAppCredentialUpdate,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    actor = await _admin(request)
    values = {
        GITHUB_APP_ID: str(body.app_id),
        GITHUB_PRIVATE_KEY: body.private_key_pem,
    }
    if body.webhook_secret is not None:
        values[GITHUB_WEBHOOK_SECRET] = body.webhook_secret.strip()
    await request.app.state.container.platform_credential_store().put_many(
        values, updated_by=actor.id
    )
    return _restart_receipt(background_tasks)


@router.delete("/{key}", status_code=204)
async def delete_credential(
    key: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    await _admin(request)
    if key not in ALLOWED_KEYS:
        raise HTTPException(status_code=404, detail="unknown credential key")
    deleted = await request.app.state.container.platform_credential_store().delete(key)
    if not deleted:
        raise HTTPException(status_code=404, detail="credential is not set")
    _restart_receipt(background_tasks)
    return Response(status_code=204)
