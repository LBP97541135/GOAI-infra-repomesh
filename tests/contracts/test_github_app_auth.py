import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from repomesh.integrations.scm.contracts import (
    RepositoryRef,
    SCMAuthenticationError,
)
from repomesh.integrations.scm.github_auth import GitHubAppTokenProvider


def private_key() -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def decode_segment(value: str) -> dict:
    padding = "=" * (-len(value) % 4)
    return json.loads(base64.urlsafe_b64decode(value + padding))


@pytest.mark.asyncio
async def test_resolves_installation_and_caches_short_lived_token() -> None:
    requests: list[httpx.Request] = []
    now = datetime(2026, 8, 7, 8, 0, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        authorization = request.headers["Authorization"]
        assert authorization.startswith("Bearer ")
        app_jwt = authorization.removeprefix("Bearer ")
        header, payload, _ = app_jwt.split(".")
        assert decode_segment(header)["alg"] == "RS256"
        claims = decode_segment(payload)
        assert claims["iss"] == "12345"
        assert claims["exp"] - claims["iat"] == 600
        if request.method == "GET":
            return httpx.Response(200, json={"id": 77})
        assert json.loads(request.content)["permissions"] == {
            "checks": "read",
            "contents": "write",
            "pull_requests": "write",
        }
        return httpx.Response(
            201,
            json={
                "token": "installation-token-1",
                "expires_at": (now + timedelta(hours=1)).isoformat(),
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GitHubAppTokenProvider(
        12345, private_key, client=client, now=lambda: now
    )
    repository = RepositoryRef.from_github("acme", "pricing")

    first, second = await asyncio.gather(provider(repository), provider(repository))

    assert first == second == "installation-token-1"
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/repos/acme/pricing/installation"),
        ("POST", "/app/installations/77/access_tokens"),
    ]
    await client.aclose()


@pytest.mark.asyncio
async def test_refreshes_token_before_expiry_without_resolving_installation_again() -> None:
    clock = [datetime(2026, 8, 7, 8, 0, tzinfo=UTC)]
    requests: list[httpx.Request] = []
    issued = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal issued
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"id": 77})
        issued += 1
        return httpx.Response(
            201,
            json={
                "token": f"token-{issued}",
                "expires_at": (clock[0] + timedelta(minutes=10)).isoformat(),
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GitHubAppTokenProvider(
        12345, private_key, client=client, now=lambda: clock[0]
    )
    repository = RepositoryRef.from_github("acme", "pricing")

    assert await provider(repository) == "token-1"
    clock[0] += timedelta(minutes=6)
    assert await provider(repository) == "token-2"
    assert [request.method for request in requests] == ["GET", "POST", "POST"]
    await client.aclose()


@pytest.mark.asyncio
async def test_invalid_private_key_fails_without_network_request() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GitHubAppTokenProvider(12345, lambda: b"not-a-key", client=client)

    with pytest.raises(SCMAuthenticationError, match="private key"):
        await provider(RepositoryRef.from_github("acme", "pricing"))
    assert requests == []
    await client.aclose()
