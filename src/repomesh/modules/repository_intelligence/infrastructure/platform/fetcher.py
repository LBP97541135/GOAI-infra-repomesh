"""Platform fetcher protocol — abstracts GitLab / GitHub / future platforms.

The domain and application layers depend only on :class:`PlatformFetcher`.
Concrete implementations (GitLab, GitHub) live in sibling modules and are
wired in the composition root or CLI.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class UrlType(Enum):
    """Result of URL identification."""

    SINGLE_REPO = "single_repo"
    GROUP = "group"
    UNKNOWN = "unknown"


class Platform(Enum):
    """Supported hosting platforms."""

    GITLAB = "gitlab"
    GITHUB = "github"
    LOCAL = "local"


@dataclass(frozen=True, slots=True)
class RepoInfo:
    """Minimal repository metadata returned by :meth:`PlatformFetcher.list_repos`."""

    name: str
    url: str
    description: str = ""
    default_branch: str = "main"
    archived: bool = False
    empty: bool = False
    fork: bool = False
    web_url: str = ""

    @property
    def should_skip(self) -> bool:
        """Whether this repo should be excluded from scanning."""

        return self.archived or self.empty


@dataclass(frozen=True, slots=True)
class FileEntry:
    """A single entry in a repository file tree."""

    path: str
    is_dir: bool


class PlatformFetcher(Protocol):
    """Abstract interface for talking to a code-hosting platform.

    Every method is async and may perform HTTP calls.  Callers should
    handle network errors and rate limits.
    """

    async def identify(self, url: str) -> UrlType:
        """Determine whether *url* points to a single repo or a group/org.

        Returns :attr:`UrlType.UNKNOWN` if the URL cannot be classified.
        """
        ...

    async def fetch_parent_group_url(self, repo_url: str) -> str | None:
        """For a single-repo URL, return the parent group/org URL.

        Returns ``None`` if the repo is not inside a group.
        """
        ...

    async def list_repos(self, group_url: str) -> list[RepoInfo]:
        """List all repositories under a group or organization.

        The caller is responsible for filtering (archived, empty, fork).
        """
        ...

    async def fetch_file_tree(self, repo_url: str) -> list[FileEntry]:
        """Return the full file tree of a repository.

        Each entry indicates whether it is a directory.  Implementation
        may paginate internally.
        """
        ...

    async def fetch_commits(self, repo_url: str, limit: int = 5) -> list[str]:
        """Return the last *limit* commit messages (one-line subjects)."""
        ...

    async def fetch_file_content(self, repo_url: str, file_path: str) -> str | None:
        """Return the text content of a file, or ``None`` if it does not exist."""
        ...


# ---------------------------------------------------------------------------
# Platform detection helpers
# ---------------------------------------------------------------------------


def detect_platform(url: str) -> Platform:
    """Heuristically determine the platform from a URL string.

    >>> detect_platform("https://gitlab.metaglobal.cn/orders/order-service")
    Platform.GITLAB
    >>> detect_platform("https://github.com/org/repo")
    Platform.GITHUB
    >>> detect_platform("D:\\\\repos\\\\order-service")
    Platform.LOCAL
    """

    lower = url.lower().strip()
    if lower.startswith(("http://", "https://", "git@", "ssh://")):
        if "github.com" in lower:
            return Platform.GITHUB
        # Everything else with a git URL pattern is treated as GitLab
        # (self-hosted instances like gitlab.metaglobal.cn).
        return Platform.GITLAB
    # Local path.
    return Platform.LOCAL


def make_fetcher(
    platform: Platform,
    *,
    gitlab_token: str = "",
    gitlab_base_url: str = "",
    github_token: str = "",
) -> PlatformFetcher:
    """Factory that wires concrete fetcher implementations.

    Lazy-imports the concrete class to keep this module dependency-free.
    """

    if platform is Platform.GITLAB:
        from .gitlab_fetcher import GitLabFetcher  # noqa: PLC0415

        return GitLabFetcher(token=gitlab_token, base_url=gitlab_base_url)

    if platform is Platform.GITHUB:
        from .github_fetcher import GitHubFetcher  # noqa: PLC0415

        return GitHubFetcher(token=github_token)

    raise ValueError(f"No fetcher for platform: {platform}")
