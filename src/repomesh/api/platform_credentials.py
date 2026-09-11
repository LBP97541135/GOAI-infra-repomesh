import asyncio
import html
import ipaddress
import os
import re
import secrets
from urllib.parse import quote, urlparse

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field, field_validator

from repomesh.integrations.scm.contracts import SCMAuthenticationError, SCMConflict
from repomesh.integrations.scm.github_auth import (
    fetch_app_installation,
    list_app_installations,
)
from repomesh.modules.platform_config import (
    ALLOWED_KEYS,
    CREATE_STATE_TTL_SECONDS,
    GITHUB_APP_ID,
    GITHUB_PRIVATE_KEY,
    GITHUB_WEBHOOK_SECRET,
    INSTALL_STATE_TTL_SECONDS,
    MANIFEST_STATE_CREATE,
    MANIFEST_STATE_INSTALL,
    MODEL_API_KEY,
    MODEL_BASE_URL,
    MODEL_NAME,
    GitHubAppRegistration,
    ManifestState,
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


def _registration_summary(registration: GitHubAppRegistration | None) -> dict | None:
    if registration is None:
        return None
    return {
        "app_id": registration.app_id,
        "slug": registration.slug,
        "owner_login": registration.owner_login,
        "owner_type": registration.owner_type,
        "requested_owner_login": registration.requested_owner_login,
        "installed": registration.installed,
        "installation_id": registration.installation_id,
        "installation_account_login": registration.installation_account_login,
        "installed_at": (
            registration.installed_at.isoformat() if registration.installed_at else None
        ),
        "install_url": f"https://github.com/apps/{quote(registration.slug, safe='')}"
        "/installations/new",
    }


@router.get("")
async def credential_status(request: Request) -> dict:
    await _admin(request)
    container = request.app.state.container
    values = await container.platform_credential_store().get_many(ALLOWED_KEYS)
    summary = _summary(values)
    # A sibling key, not a field inside `github_app`: the registration lives in
    # its own plaintext table, and the wizard needs to tell "App created" apart
    # from "App installed".
    summary["github_app_registration"] = _registration_summary(
        await container.github_app_registration_store().current()
    )
    return summary


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
# GitHub App manifest flow
#
# The wizard POSTs a pre-filled manifest to GitHub; GitHub creates the App and
# redirects back here with a one-time code that converts into the App ID, PEM
# private key and webhook secret — nothing is copy-pasted by hand. The flow then
# walks straight on to the install page, because an App that exists but is
# installed nowhere delivers exactly nothing.
# ---------------------------------------------------------------------------

#: GitHub's own account-name rules. Unvalidated, this value is concatenated into
#: the URL the wizard POSTs the manifest to — i.e. a redirect primitive handed
#: straight to an administrator.
_OWNER_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")

#: GitHub rejects App names longer than this.
_MAX_APP_NAME = 34

_MANIFEST_CALLBACK_PATH = "/api/v1/setup/credentials/github-app/manifest-callback"

#: Frozen into every App at creation time: GitHub stores `setup_url` and cannot
#: be told a new one without editing the App on github.com. Renaming this route
#: orphans the install callback of every App already created.
_INSTALL_CALLBACK_PATH = "/api/v1/setup/credentials/github-app/installed"


class ManifestRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=_MAX_APP_NAME)
    #: Empty means "my personal account"; otherwise the organization the App
    #: should belong to. It decides which GitHub form the manifest is POSTed to.
    owner_login: str | None = Field(default=None, max_length=39)
    #: The browser's own origin. Behind a port-mapping proxy (nginx on :80 in the
    #: container, published as :8100 on the host) the Host header loses the
    #: external port, so the wizard supplies what the browser actually talks to —
    #: the callback has to round-trip through the same origin.
    origin: str | None = Field(default=None, max_length=256)
    #: Creating a second App abandons the first one on GitHub, so the caller has
    #: to say so explicitly.
    replace: bool = False

    @field_validator("display_name", "owner_login")
    @classmethod
    def blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("owner_login")
    @classmethod
    def valid_owner_login(cls, value: str | None) -> str | None:
        if value is not None and not _OWNER_LOGIN.match(value):
            raise ValueError("owner_login is not a valid GitHub account name")
        return value


async def _optional_actor(request: Request):
    """The signed-in administrator, if the browser happened to carry a session.

    The callbacks are reached by GitHub redirecting the browser — a top-level
    GET, which `samesite=lax` does send `repomesh_session` on — so this usually
    succeeds and gives the credential write a real `updated_by`. It stays
    optional on purpose: an expired session must not cost the user an App that
    GitHub has already created and whose private key it will never re-issue.
    """
    try:
        return await _admin(request)
    except HTTPException:
        return None


def _is_local_hostname(hostname: str) -> bool:
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return address.is_loopback or address.is_private


def _request_origin(request: Request, supplied: str | None = None) -> str:
    """The origin the user's browser actually talks to.

    The wizard passes its own `window.location.origin`, which is authoritative
    behind a proxy that rewrites Host. But this value ends up in the manifest's
    `redirect_url`, so an unchecked one is a stored open redirect carrying a
    one-time code that converts into an App private key. Hence: the supplied
    host must match the host this request arrived on (the *port* may differ —
    that is the whole reason the field exists) or be a loopback/private literal.
    """
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    forwarded = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    host = forwarded.split(",")[0].strip() or request.url.netloc
    if not supplied:
        return f"{proto}://{host}"
    parts = urlparse(supplied)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise HTTPException(status_code=400, detail="origin must be an http(s) origin")
    if parts.path or parts.query or parts.fragment or parts.username:
        raise HTTPException(status_code=400, detail="origin must not carry a path or credentials")
    supplied_host = (parts.hostname or "").lower()
    request_host = (urlparse(f"//{host}").hostname or "").lower()
    if supplied_host != request_host and not _is_local_hostname(supplied_host):
        raise HTTPException(status_code=400, detail="origin does not belong to this deployment")
    return f"{parts.scheme}://{parts.netloc}"


def _callback_page(title: str, detail: str, *, success: bool, extra_html: str = "") -> HTMLResponse:
    """Render a terminal page for one of GitHub's redirects.

    Links are relative: by the time this renders the browser is already sitting
    on the correct external origin, whereas the absolute form used to lose the
    published port to nginx's `proxy_set_header Host $host`.
    """
    # Long enough to outlast `alembic upgrade head` plus container startup —
    # the restart scheduled alongside this response runs both.
    refresh = '<meta http-equiv="refresh" content="20;url=/">' if success else ""
    color = "#3f6f43" if success else "#a94438"
    hint = "" if success else '<span style="color:#8a8172">（本页不会自动跳转）</span>'
    return HTMLResponse(
        f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">{refresh}
<title>{html.escape(title)}</title>
</head>
<body style="font-family:system-ui,sans-serif;background:#16130d;color:#e9dec2;
display:grid;place-items:center;min-height:100vh;margin:0">
<div style="max-width:560px;padding:32px;border:1px solid #3a332a;background:#1d1812">
<h1 style="font-size:18px;margin:0 0 12px;color:{color}">{html.escape(title)}</h1>
<p style="font-size:13px;line-height:1.7;margin:0 0 16px">{html.escape(detail)}</p>
{extra_html}
<p style="font-size:12px;margin:16px 0 0">
<a style="color:#c99e52" href="/">返回控制台</a> {hint}
</p>
</div>
</body>
</html>"""
    )


def _rescue_html(state: ManifestState) -> str:
    """What to show when the App exists on GitHub but its key never reached us.

    The private key is emitted exactly once, by the conversion call. If that call
    failed we hold nothing — not the App ID, not the slug — so the only thread
    back to the orphan is the name we generated, which is precisely why the name
    is generated server-side and stored alongside the state.
    """
    owner = state.requested_owner
    settings_url = (
        f"https://github.com/organizations/{quote(owner, safe='')}/settings/apps"
        if owner
        else "https://github.com/settings/apps"
    )
    return (
        '<div style="border:1px solid #3a332a;padding:14px;font-size:12px;line-height:1.8">'
        "<strong>这个 App 很可能已经建好了，只是凭证没传回来。可以这样自救：</strong>"
        '<ol style="margin:8px 0 0;padding-left:18px">'
        f'<li>打开 <a style="color:#c99e52" href="{html.escape(settings_url)}" '
        'target="_blank" rel="noreferrer">GitHub App 设置页</a>，找名为 '
        f"<code>{html.escape(state.requested_name)}</code> 的 App。</li>"
        "<li>如果在：进去点 Generate a private key 重新生成一把私钥，"
        "记下页面上的 App ID，回到安装向导用下方的手动表单填进去。</li>"
        "<li>如果不在：直接回向导重新点一次一键创建即可。</li>"
        "<li>Webhook secret 确实找不回来了，但本部署出厂就把 webhook 设为停用"
        "（实时事件走观察轮询），所以不影响使用。</li>"
        "</ol></div>"
    )


async def _exchange_manifest_code(code: str) -> dict:
    """Trade the manifest-flow code for the App credentials.

    No authentication on this call by design — the one-time code *is* the secret.
    """
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"https://api.github.com/app-manifests/{code}/conversions",
            headers={"Accept": "application/vnd.github+json"},
        )
        response.raise_for_status()
        return response.json()


async def _stored_app_credentials(container) -> tuple[int, bytes] | None:
    """App ID and PEM read straight from the store.

    Deliberately not `container.scm_token_provider`: the install callback runs in
    the process that created the App but has not restarted yet, so the provider —
    built at boot from whatever credentials existed then — is guaranteed to be
    absent exactly when this is needed.
    """
    values = await container.platform_credential_store().get_many(
        {GITHUB_APP_ID, GITHUB_PRIVATE_KEY}
    )
    stored_id = values.get(GITHUB_APP_ID)
    stored_key = values.get(GITHUB_PRIVATE_KEY)
    if stored_id is None or stored_key is None:
        return None
    try:
        app_id = int(stored_id.value)
    except ValueError:
        return None
    return app_id, stored_key.value.encode("utf-8")


async def _sync_installation(
    container, app_id: int, private_key: bytes, owner_login: str
) -> tuple[int, str | None] | None:
    """Ask GitHub which accounts this App is installed on and record the match.

    Shared by the "already installed at conversion time" branch and the manual
    verify endpoint, which are the same question asked from two places.
    """
    installations = await list_app_installations(app_id, private_key)
    if not installations:
        return None
    preferred = next(
        (
            item
            for item in installations
            if str((item.get("account") or {}).get("login") or "").casefold()
            == owner_login.casefold()
        ),
        installations[0],
    )
    installation_id = int(preferred.get("id") or 0)
    account_login = str((preferred.get("account") or {}).get("login") or "") or None
    await container.github_app_registration_store().mark_installed(
        app_id=app_id,
        installation_id=installation_id,
        installation_account_login=account_login,
    )
    return installation_id, account_login


@router.post("/github-app/manifest")
async def create_github_app_manifest(request: Request, body: ManifestRequest | None = None) -> dict:
    actor = await _admin(request)
    payload = body or ManifestRequest()
    container = request.app.state.container
    existing = await container.github_app_registration_store().current()
    if existing is not None and not payload.replace:
        raise HTTPException(
            status_code=409,
            detail=(
                f"本部署已经绑定了 GitHub App「{existing.slug}」"
                f"（ID {existing.app_id}，归属 {existing.owner_login}）。"
                "再建一个会让它变成没人使用的孤儿 App —— GitHub 上需要你自己去删。"
                "确认要替换，请带 replace=true 重发。"
            ),
        )
    origin = _request_origin(request, payload.origin)
    callback_url = f"{origin}{_MANIFEST_CALLBACK_PATH}"
    # Generated here, not in the browser: App names are globally unique on
    # GitHub, so a fixed one collides on first use — and the server has to know
    # the exact name it sent in order to point at the orphan if the exchange
    # later fails.
    app_name = payload.display_name or f"RepoMesh Delivery {secrets.token_hex(3)}"
    manifest = {
        "name": app_name,
        "url": origin,
        # Webhooks need a publicly reachable URL; local deployments get their
        # events from the observation poller instead, so the hook ships inactive
        # and the URL is a placeholder for public deployments to enable.
        "hook_attributes": {
            "url": f"{origin}/api/v1/delivery/github-webhook",
            "active": False,
        },
        "redirect_url": callback_url,
        "callback_urls": [callback_url],
        "setup_url": f"{origin}{_INSTALL_CALLBACK_PATH}",
        "description": "RepoMesh delivery automation for this deployment.",
        "public": False,
        "request_oauth_on_install": False,
        # Must stay a superset of GitHubAppTokenProvider's default permissions:
        # the token mint sends those verbatim and GitHub 422s anything the App
        # was never granted here.
        "default_permissions": {
            "contents": "write",
            "pull_requests": "write",
            "checks": "read",
            "metadata": "read",
        },
        "default_events": ["pull_request", "pull_request_review", "check_run"],
    }
    state = await container.github_app_manifest_state_store().issue(
        kind=MANIFEST_STATE_CREATE,
        requested_name=app_name,
        requested_origin=origin,
        requested_owner=payload.owner_login,
        requested_by=actor.id,
        ttl_seconds=CREATE_STATE_TTL_SECONDS,
    )
    github_url = (
        f"https://github.com/organizations/{quote(payload.owner_login, safe='')}/settings/apps/new"
        if payload.owner_login
        else "https://github.com/settings/apps/new"
    )
    return {
        "state": state,
        "github_url": github_url,
        "app_name": app_name,
        "owner_login": payload.owner_login,
        "manifest": manifest,
    }


@router.get("/github-app/manifest-callback")
async def github_app_manifest_callback(
    request: Request,
    background_tasks: BackgroundTasks,
    state: str = "",
    code: str = "",
) -> Response:
    container = request.app.state.container
    states = container.github_app_manifest_state_store()
    claimed = await states.claim(state, kind=MANIFEST_STATE_CREATE)
    if claimed is None:
        return _callback_page(
            "链接已失效",
            "这个授权链接无效、已经被用过，或者已经超过一小时。"
            "请回到安装向导，重新点击「用 GitHub 账号一键创建」。",
            success=False,
        )
    if not code:
        await states.release(claimed.id)
        return _callback_page(
            "GitHub 没有返回授权码",
            "回调里缺少 code 参数。请回到安装向导重新发起一次。",
            success=False,
        )
    try:
        payload = await _exchange_manifest_code(code)
    except httpx.HTTPStatusError as error:
        # GitHub answered, so the one-time code is spent. Releasing the state
        # would only invite a retry that cannot possibly succeed.
        return _callback_page(
            "GitHub 凭证交换失败",
            f"GitHub API 返回错误：{str(error)[:300]}",
            success=False,
            extra_html=_rescue_html(claimed),
        )
    except httpx.HTTPError as error:
        # Transport-level: GitHub most likely never saw the code, so hand the
        # state back and let the user simply reload this page.
        await states.release(claimed.id)
        return _callback_page(
            "暂时联系不上 GitHub",
            f"网络错误：{type(error).__name__}。请刷新本页重试，授权码仍然有效。",
            success=False,
        )

    app_id = payload.get("id")
    pem = payload.get("pem")
    slug = payload.get("slug")
    owner = payload.get("owner") or {}
    if not app_id or not pem or not slug:
        return _callback_page(
            "GitHub 返回的凭证不完整",
            "转换响应里缺少 App ID、私钥或 slug，无法保存。",
            success=False,
            extra_html=_rescue_html(claimed),
        )

    values = {
        GITHUB_APP_ID: str(app_id),
        GITHUB_PRIVATE_KEY: str(pem).strip() + "\n",
    }
    webhook_secret = payload.get("webhook_secret")
    if webhook_secret:
        values[GITHUB_WEBHOOK_SECRET] = str(webhook_secret)
    actor = await _optional_actor(request)
    await container.platform_credential_store().put_many(
        values, updated_by=actor.id if actor is not None else None
    )
    await states.finalize(claimed.id)
    owner_login = str(owner.get("login") or "")
    await container.github_app_registration_store().upsert(
        app_id=int(app_id),
        slug=str(slug),
        owner_login=owner_login,
        owner_type=str(owner.get("type") or "Unknown"),
        requested_owner_login=claimed.requested_owner,
    )

    wanted = claimed.requested_owner
    if wanted and owner_login.casefold() != wanted.casefold():
        # The credentials are valid, so they stay saved. But a private App can
        # only be installed on its owner's account, so landing under the wrong
        # one is a dead end the user has to hear about right now.
        _schedule_restart(background_tasks)
        return _callback_page(
            "App 建到了另一个账号下",
            f"你选择的是「{wanted}」，但 GitHub 把 App「{slug}」建在了「{owner_login}」名下。"
            f"私有 App 只能装在它的归属账号上，所以它够不到「{wanted}」的仓库。"
            f"请到 GitHub 删掉这个 App，确认自己在「{wanted}」里有创建 App 的权限之后，"
            "回向导勾选「替换现有 App」重跑一次。",
            success=False,
        )

    if int(payload.get("installations_count") or 0) > 0:
        account = owner_login
        try:
            synced = await _sync_installation(
                container, int(app_id), str(pem).encode("utf-8"), owner_login
            )
        except (SCMAuthenticationError, SCMConflict):
            synced = None
        if synced is not None:
            account = synced[1] or owner_login
        restarting = _schedule_restart(background_tasks)
        detail = f"App「{slug}」（ID {app_id}）的凭证已加密保存，它已经安装在「{account}」。"
        if synced is None:
            detail += "安装记录暂时没查到，回到向导点一次「我已在 GitHub 上安装完成」即可补上。"
        if restarting:
            detail += "正在应用配置（约十几秒），随后自动返回控制台…"
        return _callback_page("GitHub App 已就绪", detail, success=True)

    # Straight on to the install page — and deliberately *no* restart here. The
    # user returns to `setup_url` within seconds, while a restart has to run
    # `alembic upgrade head` and bring the container back up; nginx stays alive
    # and would proxy the return hop into a dead upstream, so the install would
    # never be recorded. The restart happens on the last hop instead.
    install_state = await states.issue(
        kind=MANIFEST_STATE_INSTALL,
        requested_name=claimed.requested_name,
        requested_origin=claimed.requested_origin,
        requested_owner=owner_login or claimed.requested_owner,
        requested_by=claimed.requested_by,
        ttl_seconds=INSTALL_STATE_TTL_SECONDS,
    )
    return RedirectResponse(
        f"https://github.com/apps/{quote(str(slug), safe='')}/installations/new"
        f"?state={quote(install_state, safe='')}",
        status_code=302,
    )


@router.get("/github-app/installed")
async def github_app_installed(
    request: Request,
    background_tasks: BackgroundTasks,
    installation_id: int | None = None,
    state: str = "",
    setup_action: str = "",
) -> Response:
    container = request.app.state.container
    registration = await container.github_app_registration_store().current()
    if registration is None:
        return _callback_page(
            "找不到对应的 GitHub App",
            "本部署还没有记录任何 GitHub App。请回到安装向导重新走一次创建流程。",
            success=False,
        )
    if state:
        # Soft on purpose: GitHub documents `state` round-tripping on the OAuth
        # callback, not on `setup_url`. Hard-gating on it would break the real
        # flow in a way no test here could catch. The App JWT below is the gate.
        await container.github_app_manifest_state_store().claim(state, kind=MANIFEST_STATE_INSTALL)
    if installation_id is None:
        return _callback_page(
            "没有收到安装信息",
            "GitHub 没有回传 installation_id。如果你确实已经装好了，"
            "回到向导点击「我已在 GitHub 上安装完成」即可让本机重新核对。",
            success=False,
        )
    credentials = await _stored_app_credentials(container)
    if credentials is None:
        return _callback_page(
            "本机还没有 App 凭证",
            "凭证缺失或损坏，无法向 GitHub 核对这次安装。请回到向导重新创建 App。",
            success=False,
        )
    app_id, private_key = credentials
    try:
        installation = await fetch_app_installation(app_id, private_key, installation_id)
    except (SCMAuthenticationError, SCMConflict) as error:
        return _callback_page(
            "无法向 GitHub 核对这次安装",
            f"{type(error).__name__}: {str(error)[:300]}。"
            "稍后回到向导点击「我已在 GitHub 上安装完成」可以重试。",
            success=False,
        )
    # An App JWT can only see its own installations, which is what makes this a
    # real check: GitHub's own docs warn that the `installation_id` appended to
    # `setup_url` can be forged.
    if installation is None or int(installation.get("app_id") or 0) != app_id:
        return _callback_page(
            "这次安装无法确认",
            "GitHub 不认为这个安装编号属于本部署的 App，因此什么都没有记录。"
            "如果你确实完成了安装，请回到向导点击「我已在 GitHub 上安装完成」。",
            success=False,
        )
    account = installation.get("account") or {}
    account_login = str(account.get("login") or "") or None
    await container.github_app_registration_store().mark_installed(
        app_id=app_id,
        installation_id=installation_id,
        installation_account_login=account_login,
    )
    restarting = _schedule_restart(background_tasks)
    detail = f"App「{registration.slug}」已安装在「{account_login or registration.owner_login}」。"
    detail += (
        "正在应用配置（约十几秒），随后自动返回控制台…"
        if restarting
        else "回到控制台重启服务后即可生效。"
    )
    return _callback_page("GitHub App 已安装完成", detail, success=True)


@router.post("/github-app/verify-installation")
async def verify_github_app_installation(
    request: Request, background_tasks: BackgroundTasks
) -> dict:
    """Ask GitHub directly which accounts this App is installed on.

    The only reliable path through the install half of the flow: GitHub
    redirects to `setup_url` at install time only, so anyone who installed from
    GitHub's own UI never triggers the callback — and `setup_url` is a fossil of
    the origin the App was created on, so it dies for good once a deployment
    moves to a different address.
    """
    await _admin(request)
    container = request.app.state.container
    registration = await container.github_app_registration_store().current()
    if registration is None:
        raise HTTPException(status_code=404, detail="no GitHub App is registered")
    credentials = await _stored_app_credentials(container)
    if credentials is None:
        raise HTTPException(status_code=409, detail="GitHub App credentials are missing")
    app_id, private_key = credentials
    try:
        synced = await _sync_installation(container, app_id, private_key, registration.owner_login)
    except (SCMAuthenticationError, SCMConflict) as error:
        raise HTTPException(status_code=502, detail=f"GitHub 查询失败：{error}") from error
    if synced is None:
        return {"installed": False, "installation_id": None, "account": None, "restarting": False}
    installation_id, account_login = synced
    return {
        "installed": True,
        "installation_id": installation_id,
        "account": account_login,
        "restarting": _schedule_restart(background_tasks),
    }
