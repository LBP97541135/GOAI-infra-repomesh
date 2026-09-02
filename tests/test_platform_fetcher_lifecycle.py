"""Shared-HTTP-client lifecycle tests for the platform fetchers (t5).

The GitLab/GitHub fetchers used to create one ``httpx.AsyncClient`` per
method call — an org scan of hundreds of API calls paid a fresh TLS+TCP
handshake every time. Now each fetcher owns one lazily-created client and
an idempotent :meth:`aclose` that composition roots call when the scan
finishes. These tests pin that behaviour down:

* several method calls through one fetcher create exactly one client
  (connection reuse, not just a renamed variable);
* ``aclose`` releases that client exactly once and is safe to call again;
* ``aclose`` on a fetcher that never made a request is a no-op.
"""

from __future__ import annotations

import httpx
import pytest

from repomesh.modules.repository_intelligence.infrastructure.platform.github_fetcher import (
    GitHubFetcher,
)
from repomesh.modules.repository_intelligence.infrastructure.platform.gitlab_fetcher import (
    GitLabFetcher,
)

_GITLAB_CLIENT = (
    "repomesh.modules.repository_intelligence.infrastructure.platform."
    "gitlab_fetcher.httpx.AsyncClient"
)
_GITHUB_CLIENT = (
    "repomesh.modules.repository_intelligence.infrastructure.platform."
    "github_fetcher.httpx.AsyncClient"
)

#: (fetcher class, httpx.AsyncClient patch target, repo URL the stub answers)
_CASES = [
    (GitLabFetcher, _GITLAB_CLIENT, "https://gitlab.example.com/acme"),
    (GitHubFetcher, _GITHUB_CLIENT, "https://github.com/acme"),
]


class _RecorderClient:
    """Stands in for ``httpx.AsyncClient``; records creation and close.

    Answers every GET with an empty JSON list, which both fetchers read as
    "nothing more to list" and return an empty result for.
    """

    def __init__(self) -> None:
        self.closed = False

    async def get(
        self,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> httpx.Response:
        return httpx.Response(
            200, json=[], request=httpx.Request("GET", url)
        )

    async def aclose(self) -> None:
        self.closed = True


class TestSharedHttpClient:
    @pytest.mark.parametrize("fetcher_class,client_target,repo_url", _CASES)
    @pytest.mark.asyncio
    async def test_methods_reuse_one_client(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fetcher_class: type,
        client_target: str,
        repo_url: str,
    ) -> None:
        """Several API calls through one fetcher create exactly one client."""

        created: list[_RecorderClient] = []

        def _factory(*args: object, **kwargs: object) -> _RecorderClient:
            client = _RecorderClient()
            created.append(client)
            return client

        monkeypatch.setattr(client_target, _factory)
        fetcher = fetcher_class()

        await fetcher.list_repos(repo_url)
        await fetcher.fetch_commits(repo_url)
        await fetcher.fetch_file_content(repo_url, "README.md")

        assert len(created) == 1
        assert created[0].closed is False  # still serving until aclose

    @pytest.mark.parametrize("fetcher_class,client_target,repo_url", _CASES)
    @pytest.mark.asyncio
    async def test_aclose_releases_the_shared_client_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fetcher_class: type,
        client_target: str,
        repo_url: str,
    ) -> None:
        """aclose closes the client exactly once and is idempotent."""

        created: list[_RecorderClient] = []

        def _factory(*args: object, **kwargs: object) -> _RecorderClient:
            client = _RecorderClient()
            created.append(client)
            return client

        monkeypatch.setattr(client_target, _factory)
        fetcher = fetcher_class()

        await fetcher.list_repos(repo_url)
        assert len(created) == 1
        assert created[0].closed is False

        await fetcher.aclose()
        assert created[0].closed is True
        assert fetcher._client is None  # noqa: SLF001 — released for good

        # Second close must not fail and must not close anything twice.
        await fetcher.aclose()
        assert created[0].closed is True


class TestAcloseWithoutRequests:
    @pytest.mark.parametrize("fetcher_class,_client_target,_repo_url", _CASES)
    @pytest.mark.asyncio
    async def test_aclose_is_a_noop_when_nothing_was_fetched(
        self,
        fetcher_class: type,
        _client_target: str,
        _repo_url: str,
    ) -> None:
        """A fetcher that never made a request closes cleanly."""

        fetcher = fetcher_class()
        await fetcher.aclose()
