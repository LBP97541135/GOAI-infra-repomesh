"""GitLab API adapter — implements :class:`PlatformFetcher` for GitLab.

Supports both self-hosted (``https://gitlab.example.com``) and public
(``https://gitlab.com``) instances.  Authentication is via a personal
access token passed in the ``PRIVATE-TOKEN`` header.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from .fetcher import FileEntry, PlatformFetcher, RepoInfo, UrlType

_logger = logging.getLogger(__name__)

#: Maximum number of repos to list per page.
_PER_PAGE = 100

#: Files that indicate project dependencies, keyed by ecosystem.
_DEP_FILE_NAMES = {
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
    "package.json",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
}


@dataclass
class GitLabProjectRef:
    """Parsed GitLab project reference extracted from a URL."""

    base_url: str       # e.g. https://gitlab.metaglobal.cn
    project_path: str   # e.g. orders/order-service (URL-encoded for API)
    raw_path: str       # e.g. orders/order-service (human-readable)
    project_id: int | None = None  # numeric ID from API, filled lazily


class GitLabFetcher(PlatformFetcher):
    """Talks to a GitLab instance via its REST API (v4)."""

    def __init__(self, *, token: str = "", base_url: str = "") -> None:
        self._token = token
        # Base URL is inferred per-request from the repo URL, so ``base_url``
        # is only used as a fallback for token-only initialisation.
        self._default_base_url = base_url.rstrip("/")

    # ------------------------------------------------------------------ helpers

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._token:
            headers["PRIVATE-TOKEN"] = self._token
        return headers

    @staticmethod
    def _parse_url(url: str) -> GitLabProjectRef:
        """Extract base_url and project path from a GitLab URL.

        ``https://gitlab.metaglobal.cn/orders/order-service``
        → base_url=``https://gitlab.metaglobal.cn``
          project_path=``orders%2Forder-service``
          raw_path=``orders/order-service``
        """

        url = url.strip().rstrip("/")
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        # Path after the leading /: "orders/order-service"
        raw_path = parsed.path.lstrip("/")
        # Remove trailing .git
        if raw_path.endswith(".git"):
            raw_path = raw_path[: -len(".git")]
        project_path = quote(raw_path, safe="")
        return GitLabProjectRef(
            base_url=base_url,
            project_path=project_path,
            raw_path=raw_path,
        )

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        response = await client.get(url, params=params, headers=self._headers())
        response.raise_for_status()
        return response.json()

    async def _get_project_info(
        self,
        client: httpx.AsyncClient,
        ref: GitLabProjectRef,
    ) -> dict[str, Any] | None:
        """GET /projects/:id — returns project info or None (404)."""

        url = f"{ref.base_url}/api/v4/projects/{ref.project_path}"
        try:
            return await self._get_json(client, url)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    async def _get_group_info(
        self,
        client: httpx.AsyncClient,
        ref: GitLabProjectRef,
    ) -> dict[str, Any] | None:
        """GET /groups/:id — returns group info or None (404)."""

        url = f"{ref.base_url}/api/v4/groups/{ref.project_path}"
        try:
            return await self._get_json(client, url)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    # ------------------------------------------------------------------ identify

    async def identify(self, url: str) -> UrlType:
        ref = self._parse_url(url)
        async with httpx.AsyncClient(timeout=15) as client:
            # Try project first.
            project = await self._get_project_info(client, ref)
            if project is not None:
                return UrlType.SINGLE_REPO

            # Try group.
            group = await self._get_group_info(client, ref)
            if group is not None:
                return UrlType.GROUP

        return UrlType.UNKNOWN

    # ------------------------------------------------------------------ parent group

    async def fetch_parent_group_url(self, repo_url: str) -> str | None:
        ref = self._parse_url(repo_url)
        async with httpx.AsyncClient(timeout=15) as client:
            project = await self._get_project_info(client, ref)
            if project is None:
                return None

            namespace = project.get("namespace")
            if not namespace or namespace.get("kind") != "group":
                return None

            # The namespace full_path gives us the group URL.
            group_path = namespace.get("full_path", "")
            if not group_path:
                return None
            return f"{ref.base_url}/{group_path}"

    # ------------------------------------------------------------------ list repos

    async def list_repos(self, group_url: str) -> list[RepoInfo]:
        ref = self._parse_url(group_url)
        repos: list[RepoInfo] = []
        async with httpx.AsyncClient(timeout=30) as client:
            page = 1
            while True:
                url = (
                    f"{ref.base_url}/api/v4/groups/{ref.project_path}/projects"
                )
                params = {
                    "per_page": _PER_PAGE,
                    "page": page,
                    "include_subgroups": "true",
                    "archived": "false",
                }
                data = await self._get_json(client, url, params=params)
                if not data:
                    break
                for item in data:
                    repos.append(self._to_repo_info(item, ref.base_url))
                if len(data) < _PER_PAGE:
                    break
                page += 1
        return repos

    @staticmethod
    def _to_repo_info(item: dict[str, Any], base_url: str) -> RepoInfo:
        return RepoInfo(
            name=item.get("name", item.get("path", "")),
            url=item.get("web_url", ""),
            description=item.get("description") or "",
            default_branch=item.get("default_branch") or "main",
            archived=item.get("archived", False),
            empty=item.get("empty_repo", False),
            fork=bool(item.get("forked_from_project")),
            web_url=item.get("web_url", ""),
        )

    # ------------------------------------------------------------------ file tree

    async def fetch_file_tree(self, repo_url: str) -> list[FileEntry]:
        ref = self._parse_url(repo_url)
        entries: list[FileEntry] = []
        async with httpx.AsyncClient(timeout=30) as client:
            page = 1
            while True:
                url = (
                    f"{ref.base_url}/api/v4/projects/"
                    f"{ref.project_path}/repository/tree"
                )
                params = {
                    "recursive": "true",
                    "per_page": _PER_PAGE,
                    "page": page,
                }
                data = await self._get_json(client, url, params=params)
                if not data:
                    break
                for item in data:
                    entries.append(
                        FileEntry(
                            path=item.get("path", ""),
                            is_dir=item.get("type") == "tree",
                        )
                    )
                if len(data) < _PER_PAGE:
                    break
                page += 1
        return entries

    # ------------------------------------------------------------------ commits

    async def fetch_commits(self, repo_url: str, limit: int = 5) -> list[str]:
        ref = self._parse_url(repo_url)
        async with httpx.AsyncClient(timeout=15) as client:
            url = (
                f"{ref.base_url}/api/v4/projects/"
                f"{ref.project_path}/repository/commits"
            )
            params = {"per_page": limit}
            data = await self._get_json(client, url, params=params)
        return [item.get("title", "") for item in data if isinstance(item, dict)]

    # ------------------------------------------------------------------ file content

    async def fetch_file_content(
        self, repo_url: str, file_path: str
    ) -> str | None:
        ref = self._parse_url(repo_url)
        encoded_path = quote(file_path, safe="")
        async with httpx.AsyncClient(timeout=15) as client:
            # Try raw endpoint first (returns plain text).
            url = (
                f"{ref.base_url}/api/v4/projects/"
                f"{ref.project_path}/repository/files/{encoded_path}/raw"
            )
            params = {"ref": "main"}
            try:
                response = await client.get(
                    url, params=params, headers=self._headers()
                )
                if response.status_code == 404:
                    # Try 'master' branch as fallback.
                    params["ref"] = "master"
                    response = await client.get(
                        url, params=params, headers=self._headers()
                    )
                response.raise_for_status()
                return response.text
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    return None
                raise
