import asyncio
import base64
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .contracts import RepositoryRef, SCMAuthenticationError, SCMConflict

GITHUB_API_BASE = "https://api.github.com"


@dataclass(frozen=True, slots=True)
class CachedInstallationToken:
    value: str
    expires_at: datetime


class GitHubAppTokenProvider:
    """Issues short-lived installation tokens without exposing credentials to agents."""

    def __init__(
        self,
        app_id: int,
        private_key_loader: Callable[[], bytes],
        *,
        client: httpx.AsyncClient | None = None,
        now: Callable[[], datetime] | None = None,
        refresh_skew: timedelta = timedelta(minutes=5),
        permissions: dict[str, str] | None = None,
    ) -> None:
        if app_id <= 0:
            raise ValueError("GitHub App ID must be positive")
        self._app_id = app_id
        self._private_key_loader = private_key_loader
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=20)
        self._now = now or (lambda: datetime.now(UTC))
        self._refresh_skew = refresh_skew
        # Sent verbatim as the `permissions` body of the access-token mint, and
        # GitHub refuses any permission the App was never granted. So this set
        # must stay a subset of the manifest's `default_permissions` in
        # `api/platform_credentials.py` — an extra entry here does not widen the
        # token, it makes every mint fail with a 422.
        self._permissions = permissions or {
            "checks": "read",
            "contents": "write",
            "pull_requests": "write",
        }
        self._tokens: dict[int, CachedInstallationToken] = {}
        self._repository_installations: dict[tuple[str, str, str], int] = {}
        self._locks: dict[tuple[str, str, str], asyncio.Lock] = {}

    async def __call__(self, repository: RepositoryRef) -> str:
        key = (
            repository.api_base.rstrip("/"),
            repository.owner.lower(),
            repository.name.lower(),
        )
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            installation_id = self._repository_installations.get(key)
            if installation_id is not None:
                cached = self._tokens.get(installation_id)
                if cached and cached.expires_at - self._refresh_skew > self._now():
                    return cached.value
            app_jwt = self._issue_app_jwt()
            if installation_id is None:
                installation_id = await self._resolve_installation(repository, app_jwt)
                self._repository_installations[key] = installation_id
            token = await self._create_installation_token(repository, installation_id, app_jwt)
            self._tokens[installation_id] = token
            return token.value

    async def close(self) -> None:
        self._tokens.clear()
        if self._owns_client:
            await self._client.aclose()

    def _issue_app_jwt(self) -> str:
        return issue_app_jwt(self._app_id, self._private_key_loader(), now=self._now())

    async def _resolve_installation(self, repository: RepositoryRef, app_jwt: str) -> int:
        payload = await self._request(
            "GET",
            repository,
            f"/repos/{repository.owner}/{repository.name}/installation",
            app_jwt,
        )
        try:
            return int(payload["id"])
        except (KeyError, TypeError, ValueError) as error:
            raise SCMAuthenticationError("GitHub App installation response is invalid") from error

    async def _create_installation_token(
        self, repository: RepositoryRef, installation_id: int, app_jwt: str
    ) -> CachedInstallationToken:
        payload = await self._request(
            "POST",
            repository,
            f"/app/installations/{installation_id}/access_tokens",
            app_jwt,
            body={"permissions": self._permissions},
        )
        try:
            value = str(payload["token"]).strip()
            expires_at = datetime.fromisoformat(str(payload["expires_at"]).replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError) as error:
            raise SCMAuthenticationError("GitHub installation token response is invalid") from error
        if not value or expires_at.tzinfo is None:
            raise SCMAuthenticationError("GitHub installation token is invalid")
        return CachedInstallationToken(value, expires_at.astimezone(UTC))

    async def _request(
        self,
        method: str,
        repository: RepositoryRef,
        path: str,
        app_jwt: str,
        body: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(
                method,
                f"{repository.api_base.rstrip('/')}{path}",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {app_jwt}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json=body,
            )
        except httpx.HTTPError as error:
            raise SCMConflict(
                f"GitHub App credential request failed: {type(error).__name__}"
            ) from error
        if response.status_code in {401, 403, 404}:
            raise SCMAuthenticationError(
                "GitHub App is unauthorized or not installed for the repository"
            )
        if response.status_code >= 400:
            raise SCMConflict(f"GitHub App credential request failed: {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise SCMAuthenticationError("GitHub App credential response is invalid")
        return payload


def _urlsafe(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _encode_segment(value: dict[str, object]) -> str:
    return _urlsafe(json.dumps(value, separators=(",", ":")).encode())


def issue_app_jwt(app_id: int, private_key: bytes, *, now: datetime | None = None) -> str:
    """Sign an App-level JWT — the credential for every ``/app/...`` endpoint.

    Module-level rather than a provider method because the wizard's install
    callback needs exactly this and nothing else the provider carries: building
    one would create an ``httpx.AsyncClient`` the caller must remember to close,
    plus per-repository token caches with no repository in sight.
    """
    moment = now or datetime.now(UTC)
    header = _encode_segment({"alg": "RS256", "typ": "JWT"})
    payload = _encode_segment(
        {
            "iat": int((moment - timedelta(seconds=60)).timestamp()),
            "exp": int((moment + timedelta(minutes=9)).timestamp()),
            "iss": str(app_id),
        }
    )
    signing_input = f"{header}.{payload}".encode()
    try:
        loaded = serialization.load_pem_private_key(private_key, password=None)
    except (TypeError, ValueError) as error:
        raise SCMAuthenticationError("GitHub App private key is invalid") from error
    if not isinstance(loaded, rsa.RSAPrivateKey):
        raise SCMAuthenticationError("GitHub App private key must be RSA")
    signature = loaded.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{payload}.{_urlsafe(signature)}"


async def _app_request(
    method: str,
    path: str,
    app_jwt: str,
    *,
    api_base: str,
    client: httpx.AsyncClient | None,
) -> httpx.Response:
    """App-level counterpart to ``GitHubAppTokenProvider._request``.

    Separate from it on purpose: that one derives its base URL from a
    ``RepositoryRef`` and rejects anything but a JSON object, so it can neither
    be called without a repository nor return the array ``/app/installations``
    produces.
    """
    owned = client is None
    http = client or httpx.AsyncClient(timeout=20)
    try:
        return await http.request(
            method,
            f"{api_base.rstrip('/')}{path}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {app_jwt}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
    except httpx.HTTPError as error:
        raise SCMConflict(f"GitHub App request failed: {type(error).__name__}") from error
    finally:
        if owned:
            await http.aclose()


async def fetch_app_installation(
    app_id: int,
    private_key: bytes,
    installation_id: int,
    *,
    api_base: str = GITHUB_API_BASE,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any] | None:
    """Look up one installation of *this* App, or ``None`` if it has none.

    The App JWT can only see its own installations, so a 404 here is the answer
    to "is that ``installation_id`` really mine?" — which is what makes this a
    usable gate on GitHub's post-install redirect, whose ``installation_id``
    query parameter the docs warn can be forged.
    """
    app_jwt = issue_app_jwt(app_id, private_key)
    response = await _app_request(
        "GET",
        f"/app/installations/{installation_id}",
        app_jwt,
        api_base=api_base,
        client=client,
    )
    if response.status_code == 404:
        return None
    if response.status_code in {401, 403}:
        raise SCMAuthenticationError("GitHub App credentials are not accepted")
    if response.status_code >= 400:
        raise SCMConflict(f"GitHub App request failed: {response.status_code}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise SCMAuthenticationError("GitHub App installation response is invalid")
    return payload


async def list_app_installations(
    app_id: int,
    private_key: bytes,
    *,
    api_base: str = GITHUB_API_BASE,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Every account this App is installed on.

    The manual fallback: GitHub only redirects to ``setup_url`` at install
    time, so anyone who installed from GitHub's own UI — or whose deployment
    moved since the App was created, freezing a stale ``setup_url`` — never
    triggers the callback and needs this to be discovered.
    """
    app_jwt = issue_app_jwt(app_id, private_key)
    response = await _app_request(
        "GET",
        "/app/installations?per_page=100",
        app_jwt,
        api_base=api_base,
        client=client,
    )
    if response.status_code in {401, 403}:
        raise SCMAuthenticationError("GitHub App credentials are not accepted")
    if response.status_code >= 400:
        raise SCMConflict(f"GitHub App request failed: {response.status_code}")
    payload = response.json()
    if not isinstance(payload, list):
        raise SCMAuthenticationError("GitHub App installations response is invalid")
    return [item for item in payload if isinstance(item, dict)]


def private_key_file_loader(path: Path) -> Callable[[], bytes]:
    resolved = path.expanduser().resolve()

    def load() -> bytes:
        try:
            return resolved.read_bytes()
        except OSError as error:
            raise SCMAuthenticationError("GitHub App private key file is unavailable") from error

    return load

class StaticTokenProvider:
    """A fixed token for every repository — the local-dev seam.

    The App provider above mints short-lived installation tokens per
    repository; a developer driving the fixture repos with a personal token
    (``gh auth token``) has exactly one credential for all of them. Same
    call shape as the App provider so ``GitHubAdapter`` cannot tell them
    apart; ``close`` exists for the composition root's resource sweep.
    Selected only in ``bootstrap.app`` when ``REPOMESH_DELIVERY_GITHUB_TOKEN``
    is set and App credentials are not — the App path wins when both exist.
    """

    def __init__(self, token: str) -> None:
        self._token = token

    async def __call__(self, repository: RepositoryRef) -> str:
        return self._token

    async def close(self) -> None:  # nothing owned, kept for uniformity
        return None
