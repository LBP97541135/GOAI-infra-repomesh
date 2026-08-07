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
        now = self._now()
        header = self._encode({"alg": "RS256", "typ": "JWT"})
        payload = self._encode(
            {
                "iat": int((now - timedelta(seconds=60)).timestamp()),
                "exp": int((now + timedelta(minutes=9)).timestamp()),
                "iss": str(self._app_id),
            }
        )
        signing_input = f"{header}.{payload}".encode()
        try:
            private_key = serialization.load_pem_private_key(
                self._private_key_loader(), password=None
            )
        except (TypeError, ValueError) as error:
            raise SCMAuthenticationError("GitHub App private key is invalid") from error
        if not isinstance(private_key, rsa.RSAPrivateKey):
            raise SCMAuthenticationError("GitHub App private key must be RSA")
        signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return f"{header}.{payload}.{self._urlsafe(signature)}"

    async def _resolve_installation(
        self, repository: RepositoryRef, app_jwt: str
    ) -> int:
        payload = await self._request(
            "GET",
            repository,
            f"/repos/{repository.owner}/{repository.name}/installation",
            app_jwt,
        )
        try:
            return int(payload["id"])
        except (KeyError, TypeError, ValueError) as error:
            raise SCMAuthenticationError(
                "GitHub App installation response is invalid"
            ) from error

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
            raise SCMAuthenticationError(
                "GitHub installation token response is invalid"
            ) from error
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

    @classmethod
    def _encode(cls, value: dict[str, object]) -> str:
        return cls._urlsafe(json.dumps(value, separators=(",", ":")).encode())

    @staticmethod
    def _urlsafe(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def private_key_file_loader(path: Path) -> Callable[[], bytes]:
    resolved = path.expanduser().resolve()

    def load() -> bytes:
        try:
            return resolved.read_bytes()
        except OSError as error:
            raise SCMAuthenticationError(
                "GitHub App private key file is unavailable"
            ) from error

    return load
