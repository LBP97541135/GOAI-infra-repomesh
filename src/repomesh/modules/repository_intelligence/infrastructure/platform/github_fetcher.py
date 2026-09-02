"""GitHub API adapter — implements :class:`PlatformFetcher` for GitHub.

Supports both github.com and GitHub Enterprise.  Authentication is via
a personal access token in the ``Authorization: Bearer`` header.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from ._http import get_with_retry
from .fetcher import FileEntry, PlatformFetcher, RepoInfo, UrlType

_logger = logging.getLogger(__name__)

_API_BASE = "https://api.github.com"
_PER_PAGE = 100


@dataclass
class GitHubRepoRef:
    """Parsed GitHub repository reference."""

    api_base: str       # e.g. https://api.github.com
    owner: str          # e.g. org name or user
    repo: str           # e.g. order-service
    raw_url: str        # original URL


class GitHubFetcher(PlatformFetcher):
    """Talks to GitHub via its REST API (v3 / 2022-11-28)."""

    def __init__(self, *, token: str = "", api_base: str = _API_BASE) -> None:
        self._token = token
        self._api_base = api_base.rstrip("/")
        # One shared HTTP client per fetcher, created lazily on first use.
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------ helpers

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def _get_client(self) -> httpx.AsyncClient:
        """The one shared client for this fetcher, created on first use.

        An org scan drives hundreds of API calls through one fetcher, and
        each ``httpx.AsyncClient`` owns a connection pool — creating one per
        call would pay a fresh TLS+TCP handshake every time. The client is
        created lazily (never in ``__init__``) because fetchers are built
        synchronously in the CLI/FastAPI composition root, with no event
        loop running yet. Concurrent first use is race-free: the check and
        the assignment happen in one event-loop step with no ``await``
        between them.
        """
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def aclose(self) -> None:
        """Release the shared client's connection pool (idempotent)."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def _parse_url(url: str) -> GitHubRepoRef:
        """Extract owner and repo from a GitHub URL.

        ``https://github.com/org/repo`` → owner=``org``, repo=``repo``
        ``https://github.com/org/repo/tree/main`` → owner=``org``, repo=``repo``
        """

        url = url.strip().rstrip("/")
        parsed = urlparse(url)
        path_parts = [p for p in parsed.path.split("/") if p]
        # Remove .git from the last segment.
        if path_parts and path_parts[-1].endswith(".git"):
            path_parts[-1] = path_parts[-1][: -len(".git")]

        api_base = _API_BASE if parsed.netloc == "github.com" else f"{parsed.scheme}://{parsed.netloc}/api/v3"

        if len(path_parts) >= 2:
            owner = path_parts[0]
            repo = path_parts[1]
        elif len(path_parts) == 1:
            owner = path_parts[0]
            repo = ""
        else:
            owner = ""
            repo = ""

        return GitHubRepoRef(api_base=api_base, owner=owner, repo=repo, raw_url=url)

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        response = await get_with_retry(
            client, url, params=params, headers=self._headers()
        )
        response.raise_for_status()
        return response.json()

    async def _try_get(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """GET that returns None on 404."""
        try:
            return await self._get_json(client, url, params=params)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    # ------------------------------------------------------------------ identify

    async def identify(self, url: str) -> UrlType:
        ref = self._parse_url(url)
        client = await self._get_client()
        # Try as repo: GET /repos/{owner}/{repo}
        if ref.repo:
            repo_url = f"{ref.api_base}/repos/{ref.owner}/{ref.repo}"
            data = await self._try_get(client, repo_url)
            if data is not None:
                return UrlType.SINGLE_REPO

        # Try as org: GET /orgs/{owner}
        org_url = f"{ref.api_base}/orgs/{ref.owner}"
        data = await self._try_get(client, org_url)
        if data is not None:
            return UrlType.GROUP

        # Try as user: GET /users/{owner}
        user_url = f"{ref.api_base}/users/{ref.owner}"
        data = await self._try_get(client, user_url)
        if data is not None:
            return UrlType.GROUP

        return UrlType.UNKNOWN

    # ------------------------------------------------------------------ resolve name

    async def resolve_repo_name(self, url: str) -> str | None:
        ref = self._parse_url(url)
        if not ref.repo:
            return None
        client = await self._get_client()
        data = await self._try_get(
            client, f"{ref.api_base}/repos/{ref.owner}/{ref.repo}"
        )
        if data is None:
            return None
        name = data.get("name")
        return str(name) if name else None

    # ------------------------------------------------------------------ parent group

    async def fetch_parent_group_url(self, repo_url: str) -> str | None:
        ref = self._parse_url(repo_url)
        if not ref.repo:
            return None
        client = await self._get_client()
        url = f"{ref.api_base}/repos/{ref.owner}/{ref.repo}"
        data = await self._try_get(client, url)
        if data is None:
            return None
        owner = data.get("owner", {})
        owner_type = owner.get("type", "")
        if owner_type.lower() in ("organization", "user"):
            owner_login = owner.get("login", "")
            if owner_login:
                parsed = urlparse(ref.raw_url)
                return f"{parsed.scheme}://{parsed.netloc}/{owner_login}"
        return None

    # ------------------------------------------------------------------ list repos

    async def list_repos(self, group_url: str) -> list[RepoInfo]:
        ref = self._parse_url(group_url)
        repos: list[RepoInfo] = []
        client = await self._get_client()
        page = 1
        while True:
            # Try org repos first.
            url = f"{ref.api_base}/orgs/{ref.owner}/repos"
            params = {"per_page": _PER_PAGE, "page": page, "type": "all"}
            data = await self._try_get(client, url, params=params)
            if data is None:
                # Fall back to user repos.
                url = f"{ref.api_base}/users/{ref.owner}/repos"
                data = await self._try_get(client, url, params=params)
                if data is None:
                    break
            if not data:
                break
            for item in data:
                repos.append(self._to_repo_info(item))
            if len(data) < _PER_PAGE:
                break
            page += 1
        return repos

    @staticmethod
    def _to_repo_info(item: dict[str, Any]) -> RepoInfo:
        return RepoInfo(
            name=item.get("name", ""),
            url=item.get("html_url", item.get("clone_url", "")),
            description=item.get("description") or "",
            default_branch=item.get("default_branch") or "main",
            archived=item.get("archived", False),
            empty=item.get("size", 1) == 0,
            fork=item.get("fork", False),
            web_url=item.get("html_url", ""),
        )

    # ------------------------------------------------------------------ file tree

    async def fetch_file_tree(self, repo_url: str) -> list[FileEntry]:
        ref = self._parse_url(repo_url)
        if not ref.repo:
            return []
        client = await self._get_client()
        # Get default branch.
        repo_api = f"{ref.api_base}/repos/{ref.owner}/{ref.repo}"
        repo_data = await self._try_get(client, repo_api)
        branch = "main"
        if repo_data and repo_data.get("default_branch"):
            branch = repo_data["default_branch"]

        # Get tree recursively.
        tree_url = (
            f"{ref.api_base}/repos/{ref.owner}/{ref.repo}/git/trees/{branch}"
        )
        params = {"recursive": "1"}
        data = await self._try_get(client, tree_url, params=params)
        if data is None:
            return []

        entries: list[FileEntry] = []
        for item in data.get("tree", []):
            entries.append(
                FileEntry(
                    path=item.get("path", ""),
                    is_dir=item.get("type") == "tree",
                )
            )
        return entries

    # ------------------------------------------------------------------ commits

    async def fetch_commits(self, repo_url: str, limit: int = 5) -> list[str]:
        ref = self._parse_url(repo_url)
        if not ref.repo:
            return []
        client = await self._get_client()
        url = f"{ref.api_base}/repos/{ref.owner}/{ref.repo}/commits"
        params = {"per_page": limit}
        data = await self._get_json(client, url, params=params)
        return [
            item.get("commit", {}).get("message", "").split("\n")[0]
            for item in data
            if isinstance(item, dict)
        ]

    # ------------------------------------------------------------------ file content

    async def fetch_file_content(
        self, repo_url: str, file_path: str
    ) -> str | None:
        ref = self._parse_url(repo_url)
        if not ref.repo:
            return None
        client = await self._get_client()
        url = f"{ref.api_base}/repos/{ref.owner}/{ref.repo}/contents/{file_path}"
        data = await self._try_get(client, url)
        if data is None:
            return None
        # GitHub returns content as base64-encoded.
        encoding = data.get("encoding", "")
        content = data.get("content", "")
        if encoding == "base64" and content:
            import base64  # noqa: PLC0415

            return base64.b64decode(content).decode("utf-8", errors="replace")
        return content if isinstance(content, str) else None
