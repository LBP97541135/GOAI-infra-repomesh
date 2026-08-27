"""Rate-limit retry tests for the platform fetchers (Phase 7.2).

Covers the shared :func:`get_with_retry` helper plus the two fetchers'
retry behaviour: ``_get_json`` on GitHub/GitLab and the direct calls in
GitLab's ``fetch_file_content`` (which has a 404→master fallback that must
also survive a rate limit).
"""

from __future__ import annotations

import time

import httpx
import pytest

from repomesh.modules.repository_intelligence.infrastructure.platform._http import (
    _DEFAULT_MAX_ATTEMPTS,
    _rate_limit_wait,
    get_with_retry,
)
from repomesh.modules.repository_intelligence.infrastructure.platform.github_fetcher import (
    GitHubFetcher,
)
from repomesh.modules.repository_intelligence.infrastructure.platform.gitlab_fetcher import (
    GitLabFetcher,
)


def _resp(
    status: int,
    *,
    headers: dict[str, str] | None = None,
    json_data: object | None = None,
    text: str | None = None,
) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        headers=headers or {},
        json=json_data,
        text=text,
        request=httpx.Request("GET", "https://example.test/"),
    )


class _SequenceClient:
    """Duck-typed httpx client that serves canned responses in order.

    Also an async context manager so it can stand in for the
    ``httpx.AsyncClient`` a fetcher creates internally.
    """

    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict | None, dict | None]] = []

    async def get(
        self,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> httpx.Response:
        # Snapshot params: callers mutate their dict in place between calls
        # (e.g. the main→master fallback), and we want to assert what each
        # request actually sent.
        self.calls.append(
            (url, dict(params) if params is not None else None, headers)
        )
        if not self._responses:
            raise AssertionError("more requests than canned responses")
        return self._responses.pop(0)

    async def __aenter__(self) -> _SequenceClient:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


# ---------------------------------------------------------------------------
# _rate_limit_wait — pure wait-time decisions
# ---------------------------------------------------------------------------


class TestRateLimitWait:
    def test_429_with_retry_after(self) -> None:
        response = _resp(429, headers={"retry-after": "2"})
        assert _rate_limit_wait(response, attempt=1) == 2.0

    def test_429_with_retry_after_zero(self) -> None:
        response = _resp(429, headers={"retry-after": "0"})
        assert _rate_limit_wait(response, attempt=1) == 0.0

    def test_429_without_headers_uses_exponential_backoff(self) -> None:
        response = _resp(429)
        assert _rate_limit_wait(response, attempt=1) == 1.0
        assert _rate_limit_wait(response, attempt=2) == 2.0
        assert _rate_limit_wait(response, attempt=3) == 4.0

    def test_403_with_rate_limit_headers_is_retried(self) -> None:
        # GitHub marks an exhausted quota with X-RateLimit-Remaining: 0.
        response = _resp(403, headers={"x-ratelimit-remaining": "0"})
        assert _rate_limit_wait(response, attempt=1) == 1.0

    def test_403_with_reset_in_the_future_waits_until_reset(self) -> None:
        reset = int(time.time()) + 5
        response = _resp(403, headers={"x-ratelimit-reset": str(reset)})
        wait = _rate_limit_wait(response, attempt=1)
        assert wait is not None
        assert 4.0 <= wait <= 5.0

    def test_403_without_signal_is_not_retried(self) -> None:
        # A plain 403 is a permission problem; retrying burns quota.
        response = _resp(403)
        assert _rate_limit_wait(response, attempt=1) is None

    def test_non_rate_limit_statuses_are_not_retried(self) -> None:
        assert _rate_limit_wait(_resp(200), attempt=1) is None
        assert _rate_limit_wait(_resp(404), attempt=1) is None
        assert _rate_limit_wait(_resp(500), attempt=1) is None

    def test_retry_after_is_capped(self) -> None:
        response = _resp(429, headers={"retry-after": "9999"})
        assert _rate_limit_wait(response, attempt=1) == 30.0


# ---------------------------------------------------------------------------
# get_with_retry — end-to-end retry loop
# ---------------------------------------------------------------------------


class TestGetWithRetry:
    @pytest.mark.asyncio
    async def test_successful_request_is_not_retried(self) -> None:
        client = _SequenceClient([_resp(200, json_data={"ok": True})])
        response = await get_with_retry(
            client, "https://api.example.test/x", headers={}
        )

        assert response.status_code == 200
        assert len(client.calls) == 1

    @pytest.mark.asyncio
    async def test_429_backs_off_then_succeeds(self) -> None:
        client = _SequenceClient([
            _resp(429, headers={"retry-after": "0"}),
            _resp(429, headers={"retry-after": "0"}),
            _resp(200, json_data={"ok": True}),
        ])
        response = await get_with_retry(
            client, "https://api.example.test/x", headers={}
        )

        assert response.status_code == 200
        assert len(client.calls) == 3

    @pytest.mark.asyncio
    async def test_403_with_signal_backs_off_then_succeeds(self) -> None:
        client = _SequenceClient([
            _resp(403, headers={"x-ratelimit-remaining": "0"}),
            _resp(200, json_data={"ok": True}),
        ])
        response = await get_with_retry(
            client, "https://api.example.test/x", headers={}
        )

        assert response.status_code == 200
        assert len(client.calls) == 2

    @pytest.mark.asyncio
    async def test_plain_403_is_returned_without_retrying(self) -> None:
        client = _SequenceClient([_resp(403)])
        response = await get_with_retry(
            client, "https://api.example.test/x", headers={}
        )

        assert response.status_code == 403
        assert len(client.calls) == 1

    @pytest.mark.asyncio
    async def test_persistent_429_returns_final_response(self) -> None:
        client = _SequenceClient([
            _resp(429, headers={"retry-after": "0"}),
            _resp(429, headers={"retry-after": "0"}),
            _resp(429, headers={"retry-after": "0"}),
        ])
        response = await get_with_retry(
            client, "https://api.example.test/x", headers={}
        )

        assert response.status_code == 429
        assert len(client.calls) == _DEFAULT_MAX_ATTEMPTS


# ---------------------------------------------------------------------------
# GitHubFetcher — _get_json / _try_get
# ---------------------------------------------------------------------------


class TestGitHubFetcherRetry:
    def _fetcher(self) -> GitHubFetcher:
        return GitHubFetcher()

    @pytest.mark.asyncio
    async def test_get_json_retries_429(self) -> None:
        client = _SequenceClient([
            _resp(429, headers={"retry-after": "0"}),
            _resp(200, json_data={"name": "order-service"}),
        ])
        data = await self._fetcher()._get_json(
            client, "https://api.github.com/repos/acme/order-service"
        )

        assert data == {"name": "order-service"}
        assert len(client.calls) == 2

    @pytest.mark.asyncio
    async def test_try_get_retries_then_404_returns_none(self) -> None:
        client = _SequenceClient([
            _resp(429, headers={"retry-after": "0"}),
            _resp(404),
        ])
        data = await self._fetcher()._try_get(
            client, "https://api.github.com/repos/acme/nope"
        )

        assert data is None
        assert len(client.calls) == 2

    @pytest.mark.asyncio
    async def test_try_get_plain_403_raises_without_retry(self) -> None:
        client = _SequenceClient([_resp(403)])
        with pytest.raises(httpx.HTTPStatusError):
            await self._fetcher()._try_get(
                client, "https://api.github.com/repos/acme/secret"
            )
        assert len(client.calls) == 1


# ---------------------------------------------------------------------------
# GitLabFetcher — _get_json and the direct calls in fetch_file_content
# ---------------------------------------------------------------------------


class TestGitLabFetcherRetry:
    def _fetcher(self) -> GitLabFetcher:
        return GitLabFetcher()

    @pytest.mark.asyncio
    async def test_get_json_retries_429(self) -> None:
        client = _SequenceClient([
            _resp(429, headers={"retry-after": "0"}),
            _resp(200, json_data={"name": "order-service"}),
        ])
        data = await self._fetcher()._get_json(
            client, "https://gitlab.example.com/api/v4/projects/team%2Frepo"
        )

        assert data == {"name": "order-service"}
        assert len(client.calls) == 2

    @pytest.mark.asyncio
    async def test_fetch_file_content_retries_rate_limited_direct_calls(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """main→404 fallback to master, then a 429 that must be retried."""
        client = _SequenceClient([
            _resp(404),
            _resp(429, headers={"retry-after": "0"}),
            _resp(200, text="# hello"),
        ])
        monkeypatch.setattr(
            "repomesh.modules.repository_intelligence.infrastructure."
            "platform.gitlab_fetcher.httpx.AsyncClient",
            lambda *args, **kwargs: client,
        )
        content = await self._fetcher().fetch_file_content(
            "https://gitlab.example.com/team/repo", "README.md"
        )

        assert content == "# hello"
        assert len(client.calls) == 3
        # First call asked for main; the fallback asked for master.
        assert client.calls[0][1] == {"ref": "main"}
        assert client.calls[1][1] == {"ref": "master"}
        assert client.calls[2][1] == {"ref": "master"}

    @pytest.mark.asyncio
    async def test_fetch_file_content_404_on_both_branches_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _SequenceClient([_resp(404), _resp(404)])
        monkeypatch.setattr(
            "repomesh.modules.repository_intelligence.infrastructure."
            "platform.gitlab_fetcher.httpx.AsyncClient",
            lambda *args, **kwargs: client,
        )
        content = await self._fetcher().fetch_file_content(
            "https://gitlab.example.com/team/repo", "missing.txt"
        )

        assert content is None
        assert len(client.calls) == 2
