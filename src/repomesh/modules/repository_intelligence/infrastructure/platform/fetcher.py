"""Platform fetcher protocol — abstracts GitLab / GitHub / future platforms.

The domain and application layers depend only on :class:`PlatformFetcher`.
Concrete implementations (GitLab, GitHub) live in sibling modules and are
wired in the composition root or CLI.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class UrlType(Enum):
    """Result of URL identification."""

    SINGLE_REPO = "single_repo"
    GROUP = "group"
    UNKNOWN = "unknown"


class Platform(Enum):
    """Hosting platforms, including the two we can talk to.

    ``UNSUPPORTED`` names a well-known hosting service we do not implement
    (so the caller can say so instead of trying and failing).  ``UNKNOWN``
    covers everything else: a host that looks like git but is neither known
    nor declared in configuration.
    """

    GITLAB = "gitlab"
    GITHUB = "github"
    LOCAL = "local"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


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

    async def resolve_repo_name(self, url: str) -> str | None:
        """Return the authoritative repository name from the platform.

        Uses the platform's own metadata (``GET /repos/...`` / ``GET
        /projects/...``), not the URL path.  ``None`` when the URL does not
        name an existing repository.
        """
        ...

    async def aclose(self) -> None:
        """Release any pooled HTTP connections (idempotent).

        A fetcher may hold one shared ``httpx.AsyncClient`` so an org scan
        reuses TCP/TLS connections instead of paying a handshake per call.
        Composition roots (CLI, API endpoints, background tasks) must call
        this exactly once when the fetcher is no longer needed; callers
        that never made a request are unaffected.
        """
        ...


# ---------------------------------------------------------------------------
# Platform detection helpers
# ---------------------------------------------------------------------------

#: Well-known hosting platforms we deliberately do not implement, mapped to a
#: friendly label for error messages.  Everything else is UNKNOWN.
_UNSUPPORTED_PLATFORM_HOSTS = {
    "gitee.com": "Gitee",
    "bitbucket.org": "Bitbucket",
    "dev.azure.com": "Azure DevOps",
    "codeberg.org": "Codeberg",
    "sourceforge.net": "SourceForge",
}

#: Platform names accepted in ``repository_scan_platforms`` configuration.
_PLATFORM_NAMES = {
    "gitlab": Platform.GITLAB,
    "github": Platform.GITHUB,
}


def _host_of(url: str) -> str:
    """Extract the lower-cased hostname from a git URL of any supported form."""

    lower = url.lower().strip()
    if lower.startswith("git@"):  # git@host:path
        return lower[4:].split(":")[0].rstrip(".").lower()
    from urllib.parse import urlsplit  # noqa: PLC0415

    return (urlsplit(lower).hostname or "").rstrip(".").lower()


def detect_platform(
    url: str,
    platform_map: Mapping[str, str] | None = None,
) -> Platform:
    """Heuristically determine the platform from a URL string.

    Known hosts are decided by name; anything else falls back to the
    ``platform_map`` supplied by the caller (e.g. from
    ``REPOMESH_REPOSITORY_PLATFORMS`` configuration) and then to
    ``UNKNOWN`` — never to a guess that a random host speaks GitLab.

    >>> detect_platform("https://gitlab.example.com/orders/order-service")
    Platform.GITLAB
    >>> detect_platform("https://github.com/org/repo")
    Platform.GITHUB
    >>> detect_platform("https://gitee.com/org/repo")
    Platform.UNSUPPORTED
    >>> detect_platform("D:\\\\repos\\\\order-service")
    Platform.LOCAL
    """

    lower = url.lower().strip()
    if not lower.startswith(("http://", "https://", "git@", "ssh://")):
        # Local path.
        return Platform.LOCAL

    host = _host_of(lower)
    if host in _UNSUPPORTED_PLATFORM_HOSTS:
        return Platform.UNSUPPORTED
    if host == "github.com" or host.endswith(".github.com"):
        return Platform.GITHUB
    if host == "gitlab.com" or "gitlab" in host:
        return Platform.GITLAB
    if platform_map:
        mapped = _PLATFORM_NAMES.get((platform_map.get(host) or "").lower())
        if mapped is not None:
            return mapped
    return Platform.UNKNOWN


def unsupported_platform_label(url: str) -> str:
    """Friendly label for an unsupported platform host (for error messages)."""

    return _UNSUPPORTED_PLATFORM_HOSTS.get(_host_of(url), _host_of(url))


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

    raise ValueError(
        f"No fetcher for platform: {platform}. "
        "Only GitHub and GitLab are supported."
    )
