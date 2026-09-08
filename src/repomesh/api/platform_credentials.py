import asyncio
import html
import os
import secrets
import time
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
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


def _schedule_restart(background_tasks: BackgroundTasks) -> bool:
    """Clear the settings cache and, under a supervisor, exit so the new
    credentials are picked up by a fresh process. Returns whether a restart
    will actually happen."""
    supervised = get_settings().supervised
    get_settings.cache_clear()
    if supervised:
        background_tasks.add_task(_exit_after_response)
    return supervised


def _restart_receipt(background_tasks: BackgroundTasks) -> dict:
    restarting = _schedule_restart(background_tasks)
    return {
        "saved": True,
        "restarting": restarting,
        "restart_required": not restarting,
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


# ---------------------------------------------------------------------------
# GitHub App manifest flow: the wizard hands the user a pre-filled manifest
# form; GitHub redirects back here with a one-time code that converts to the
# App ID, PEM private key, and webhook secret — nothing is copy-pasted.
# ---------------------------------------------------------------------------

MANIFEST_STATE_TTL_SECONDS = 600
_pending_manifest_states: dict[str, tuple[float, str]] = {}


class ManifestRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=64)
    #: The browser's own origin. Behind a port-mapping proxy (nginx on :80 in
    #: the container, published as :8100 on the host) the Host header loses the
    #: external port, so the wizard supplies what the user's browser actually
    #: talks to — the callback URL must round-trip through the same origin.
    origin: str | None = Field(default=None, max_length=256)


def _issue_manifest_state(origin: str) -> str:
    now = time.monotonic()
    expired = [
        key
        for key, (created, _) in _pending_manifest_states.items()
        if now - created > MANIFEST_STATE_TTL_SECONDS
    ]
    for key in expired:
        _pending_manifest_states.pop(key, None)
    state = secrets.token_urlsafe(16)
    # The origin rides with the state: the callback page needs the same
    # browser-facing origin for its "return to console" redirect, and the
    # Host header behind the nginx port mapping cannot supply it.
    _pending_manifest_states[state] = (now, origin)
    return state


def _consume_manifest_state(state: str) -> str | None:
    entry = _pending_manifest_states.pop(state, None)
    if entry is None:
        return None
    created, origin = entry
    if time.monotonic() - created > MANIFEST_STATE_TTL_SECONDS:
        return None
    return origin


def _request_origin(request: Request, supplied: str | None = None) -> str:
    """The origin the user's browser talks to. The wizard passes its own
    window.location.origin (authoritative behind any proxy); the header
    fallback keeps direct API usage working. Only scheme://authority survives —
    a path or query in the supplied value would hijack the callback location."""
    if supplied:
        parts = urlparse(supplied)
        if parts.scheme in ("http", "https") and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}"
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host", request.url.netloc)
    return f"{proto}://{host}"


def _callback_page(title: str, detail: str, *, success: bool, wizard_url: str) -> HTMLResponse:
    detail_html = html.escape(detail)
    refresh = (
        f'<meta http-equiv="refresh" content="6;url={html.escape(wizard_url)}">'
        if success
        else ""
    )
    color = "#3f6f43" if success else "#a94438"
    return HTMLResponse(
        f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">{refresh}
<title>{html.escape(title)}</title>
</head>
<body style="font-family:system-ui,sans-serif;background:#16130d;color:#e9dec2;
display:grid;place-items:center;min-height:100vh;margin:0">
<div style="max-width:480px;padding:32px;border:1px solid #3a332a;background:#1d1812">
<h1 style="font-size:18px;margin:0 0 12px;color:{color}">{html.escape(title)}</h1>
<p style="font-size:13px;line-height:1.6;margin:0 0 16px">{detail_html}</p>
<p style="font-size:12px;margin:0">
<a style="color:#c99e52" href="{html.escape(wizard_url)}">返回控制台</a>
{'' if success else '<span style="color:#8a8172">（浏览器不会自动跳转，请点击上方链接）</span>'}
</p>
</div>
</body>
</html>"""
    )


async def _exchange_manifest_code(code: str) -> dict:
    """Trade the manifest-flow code for the app credentials. No authentication
    on this call by design — the one-time code itself is the secret."""
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"https://api.github.com/app-manifests/{code}/conversions",
            headers={"Accept": "application/vnd.github+json"},
        )
        response.raise_for_status()
        return response.json()


@router.post("/github-app/manifest")
async def create_github_app_manifest(
    request: Request, body: ManifestRequest | None = None
) -> dict:
    await _admin(request)
    origin = _request_origin(request, body.origin if body else None)
    callback_url = f"{origin}/api/v1/setup/credentials/github-app/manifest-callback"
    app_name = (body.display_name if body else None) or "RepoMesh Delivery"
    manifest = {
        "name": app_name,
        "url": origin,
        # Webhooks need a publicly reachable URL; local deployments get live
        # events through the observation poller instead, so the hook ships
        # inactive and the URL is a placeholder for public deployments to fix.
        "hook_attributes": {
            "url": f"{origin}/api/v1/delivery/github-webhook",
            "active": False,
        },
        "redirect_url": callback_url,
        "callback_urls": [callback_url],
        "setup_url": f"{origin}/",
        "description": "RepoMesh delivery automation for this deployment.",
        "public": False,
        "request_oauth_on_install": False,
        "default_permissions": {
            "contents": "write",
            "pull_requests": "write",
            "checks": "read",
            "metadata": "read",
        },
        "default_events": ["pull_request", "pull_request_review", "check_run"],
    }
    return {
        "state": _issue_manifest_state(origin),
        "github_url": "https://github.com/settings/apps/new",
        "manifest": manifest,
    }


@router.get("/github-app/manifest-callback")
async def github_app_manifest_callback(
    request: Request,
    background_tasks: BackgroundTasks,
    state: str = "",
    code: str = "",
) -> HTMLResponse:
    bound_origin = _consume_manifest_state(state)
    if bound_origin is None:
        return _callback_page(
            "链接已失效",
            "授权链接无效或已过期（10 分钟）。请回到安装向导重新点击「用 GitHub 账号一键创建」。",
            success=False,
            wizard_url=_request_origin(request),
        )
    wizard_url = f"{bound_origin}/"
    if not code:
        return _callback_page(
            "GitHub 未返回授权码",
            "回调缺少 code 参数。请回到安装向导重新发起一次。",
            success=False,
            wizard_url=wizard_url,
        )
    try:
        payload = await _exchange_manifest_code(code)
    except httpx.HTTPError as error:
        return _callback_page(
            "GitHub 凭证交换失败",
            f"GitHub API 返回错误：{str(error)[:400]}",
            success=False,
            wizard_url=wizard_url,
        )
    app_id = payload.get("id")
    pem = payload.get("pem")
    webhook_secret = payload.get("webhook_secret")
    slug = payload.get("slug") or app_id
    if not app_id or not pem:
        return _callback_page(
            "GitHub 返回的凭证不完整",
            f"缺少 App ID 或私钥（slug: {html.escape(str(slug))}）。请回到安装向导重试。",
            success=False,
            wizard_url=wizard_url,
        )
    values = {
        GITHUB_APP_ID: str(app_id),
        GITHUB_PRIVATE_KEY: pem.strip() + "\n",
    }
    if webhook_secret:
        values[GITHUB_WEBHOOK_SECRET] = webhook_secret
    await request.app.state.container.platform_credential_store().put_many(
        values, updated_by=None
    )
    restarting = _schedule_restart(background_tasks)
    applied = (
        "正在应用配置（约几秒），随后自动返回控制台…"
        if restarting
        else "回到控制台后即可看到 GitHub App 已配置。"
    )
    return _callback_page(
        "GitHub App 创建成功",
        f"App「{html.escape(str(slug))}」（ID {html.escape(str(app_id))}）"
        f"的凭证已加密保存。{applied}",
        success=True,
        wizard_url=wizard_url,
    )
