"""Remote AutoCard builder + URL parsing + org scanning.

This module bridges the :class:`PlatformFetcher` (GitLab/GitHub) with the
domain :class:`AutoCard`.  It also handles parsing the user's free-text
input to extract the URL and requirement.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from repomesh.modules.repository_intelligence.application.scan import (
    _BUSINESS_NAME_KEYWORDS,
    _GENERIC_DEPS,
    _GENERIC_DIR_NAMES,
    _LOW_SIGNAL_THRESHOLD,
    _VAGUE_COMMIT_PATTERNS,
    infer_name,
)
from repomesh.modules.repository_intelligence.domain import (
    AutoCard,
    RepositoryProfile,
)
from repomesh.modules.repository_intelligence.infrastructure.platform import (
    FileEntry,
    PlatformFetcher,
    RepoInfo,
)

_logger = logging.getLogger(__name__)

#: Dependency files we know how to parse, keyed by ecosystem.
_DEP_FILE_MAP: dict[str, tuple[str, ...]] = {
    "python": ("requirements.txt", "pyproject.toml", "setup.py"),
    "node": ("package.json",),
    "go": ("go.mod",),
    "rust": ("Cargo.toml",),
    "java": ("pom.xml", "build.gradle", "build.gradle.kts"),
}

#: All known dependency file names (flattened for quick lookup).
_ALL_DEP_FILES: set[str] = {f for files in _DEP_FILE_MAP.values() for f in files}


# ---------------------------------------------------------------------------
# Input parsing — extract URL + requirement from free text
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParsedInput:
    """Result of parsing a user's free-text input."""

    url: str
    requirement: str
    entry_repo_name: str | None  # non-None if URL is a single repo


# URL regex: matches http(s)://... or git@... or local paths starting with
# a drive letter (D:\) or dot-slash (./).
_URL_PATTERN = re.compile(
    r'(https?://[^\s]+|git@[^\s]+|ssh://[^\s]+'
    r'|(?:[A-Za-z]:[\\/])[^\s]*|[.]/[^\s]*)',
)


def parse_user_input(text: str) -> ParsedInput:
    """Extract the URL/path and the requirement from free text.

    Examples::

        >>> parse_user_input("在 https://gitlab.example.com/orders/order-service 里加微信支付")
        ParsedInput(url="https://gitlab.example.com/orders/order-service",
                    requirement="加微信支付",
                    entry_repo_name="order-service")

        >>> parse_user_input("在 https://gitlab.example.com/ 下加微信支付")
        ParsedInput(url="https://gitlab.example.com/",
                    requirement="加微信支付",
                    entry_repo_name=None)
    """

    match = _URL_PATTERN.search(text)
    if match is None:
        raise ValueError(
            "Input must contain a repository URL or local path. "
            "Example: 在 https://gitlab.example.com/orders/repo 里加功能"
        )

    url = match.group(1).rstrip("/")

    # Remove the URL from the text to get the requirement.
    requirement = text[: match.start()] + text[match.start() + len(url):]
    # Clean up residual whitespace and connector words.
    requirement = requirement.strip()
    requirement = re.sub(r'^(在|from|at|on)\s*', '', requirement, flags=re.IGNORECASE)
    requirement = re.sub(
        r'\s*(里|中|下|inside|under|in)\s*$', '', requirement, flags=re.IGNORECASE,
    )
    requirement = requirement.strip()

    # Entry repo name: only if the URL looks like a single repo (has ≥2 path segments
    # after the host).  Final determination is done by the fetcher's identify().
    # We just pre-extract a candidate here.
    from urllib.parse import urlparse  # noqa: PLC0415

    parsed_url = urlparse(url)
    path_segments = [s for s in parsed_url.path.split("/") if s]
    entry_repo_name = infer_name(path_segments[-1]) if len(path_segments) >= 2 else None

    return ParsedInput(url=url, requirement=requirement, entry_repo_name=entry_repo_name)


# ---------------------------------------------------------------------------
# Remote AutoCard building
# ---------------------------------------------------------------------------


async def scan_remote(
    repo_info: RepoInfo,
    fetcher: PlatformFetcher,
) -> AutoCard:
    """Build an :class:`AutoCard` from a remote repository.

    Makes 2-3 API calls per repo:
    1. File tree → top_dirs + detect dependency files
    2. Commits → recent_commits
    3. (Optional) Dependency file content → deps

    ``exposed_apis`` is left empty (requires reading every source file).
    """

    # 1. File tree
    file_entries = await fetcher.fetch_file_tree(repo_info.url)
    top_dirs = _extract_top_dirs(file_entries)
    dep_files_present = _find_dep_files(file_entries)

    # 2. Commits
    try:
        commits = await fetcher.fetch_commits(repo_info.url, limit=5)
    except Exception:  # noqa: BLE001
        _logger.warning("Failed to fetch commits for %s", repo_info.name)
        commits = []

    # 3. Dependency file content (if any dep files exist)
    deps: list[str] = []
    for dep_file in dep_files_present:
        try:
            content = await fetcher.fetch_file_content(repo_info.url, dep_file)
            if content is not None:
                deps.extend(_parse_dep_file(dep_file, content))
        except Exception:  # noqa: BLE001
            _logger.debug("Failed to fetch %s for %s", dep_file, repo_info.name)

    # Deduplicate deps.
    seen: set[str] = set()
    unique_deps: list[str] = []
    for dep in deps:
        key = dep.lower()
        if key not in seen:
            seen.add(key)
            unique_deps.append(dep)

    # low_signal scoring (without exposed_apis, max score = 0.9).
    low_signal = _compute_remote_low_signal(
        repo_info.name, top_dirs, tuple(unique_deps), tuple(commits)
    )

    return AutoCard(
        top_dirs=tuple(top_dirs),
        deps=tuple(unique_deps),
        recent_commits=tuple(commits),
        exposed_apis=(),
        low_signal=low_signal,
    )


async def scan_org(
    group_url: str,
    fetcher: PlatformFetcher,
    *,
    on_progress: Callable[[int, int, str], None] | None = None,
    max_workers: int = 5,
) -> list[RepositoryProfile]:
    """Scan all repos under a group/org URL.

    1. List all repos.
    2. Filter out archived / empty / fork.
    3. Concurrently build AutoCard for each.
    4. Return profiles.
    """

    all_repos = await fetcher.list_repos(group_url)
    repos = [r for r in all_repos if not r.should_skip]

    total = len(repos)
    if total == 0:
        return []

    # Concurrent scanning with a semaphore to limit parallelism.
    semaphore = asyncio.Semaphore(max_workers)
    profiles: list[RepositoryProfile] = []
    completed = 0

    async def _scan_one(repo: RepoInfo) -> RepositoryProfile:
        nonlocal completed
        async with semaphore:
            try:
                card = await scan_remote(repo, fetcher)
            except Exception:  # noqa: BLE001
                _logger.warning("Failed to scan %s, using empty card", repo.name)
                card = AutoCard(low_signal=True)
        completed += 1
        if on_progress is not None:
            on_progress(completed, total, repo.name)
        return RepositoryProfile(
            name=repo.name,
            url=repo.url,
            description=repo.description,
            auto_card=card,
        )

    tasks = [asyncio.create_task(_scan_one(r)) for r in repos]
    profiles = await asyncio.gather(*tasks)
    return list(profiles)


# ---------------------------------------------------------------------------
# File tree helpers
# ---------------------------------------------------------------------------


def _extract_top_dirs(entries: list[FileEntry]) -> list[str]:
    """Extract first 2 levels of directory names from file tree."""

    dirs: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if not entry.is_dir:
            continue
        parts = entry.path.split("/")
        # Take first 1-2 levels.
        for depth in range(1, min(len(parts), 2) + 1):
            dir_path = "/".join(parts[:depth])
            if dir_path not in seen:
                seen.add(dir_path)
                dirs.append(dir_path)
    return dirs[:80]


def _find_dep_files(entries: list[FileEntry]) -> list[str]:
    """Find dependency files in the file tree (root level only)."""

    found: list[str] = []
    for entry in entries:
        if entry.is_dir:
            continue
        # Check root-level files only (no slash in path).
        if "/" not in entry.path and entry.path in _ALL_DEP_FILES:
            found.append(entry.path)
    return found


# ---------------------------------------------------------------------------
# Dependency file parsing (reuses logic from scan.py but adapted for text)
# ---------------------------------------------------------------------------


def _parse_dep_file(filename: str, content: str) -> list[str]:
    """Parse a dependency file and return package names."""

    if filename in ("requirements.txt",):
        return _parse_requirements(content)
    if filename in ("pyproject.toml",):
        return _parse_pyproject(content)
    if filename == "package.json":
        return _parse_package_json(content)
    if filename == "go.mod":
        return _parse_go_mod(content)
    # For unrecognised files, return empty (don't guess).
    return []


def _parse_requirements(content: str) -> list[str]:
    import re  # noqa: PLC0415

    deps: list[str] = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        name = re.split(r"[>=<!\[;]", line, maxsplit=1)[0].strip()
        if name:
            deps.append(name)
    return deps


def _parse_pyproject(content: str) -> list[str]:
    import re  # noqa: PLC0415

    deps: list[str] = []
    pattern = r'dependencies\s*=\s*\[(.*?)\]'
    for match in re.finditer(pattern, content, re.DOTALL):
        inner = match.group(1)
        for quoted in re.findall(r'"([^"]+)"', inner):
            name = re.split(r"[>=<!\[;]", quoted, maxsplit=1)[0].strip()
            if name:
                deps.append(name)
    return deps


def _parse_package_json(content: str) -> list[str]:
    import json  # noqa: PLC0415

    try:
        data = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []
    deps: list[str] = []
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        section = data.get(key)
        if isinstance(section, dict):
            deps.extend(section.keys())
    return deps


def _parse_go_mod(content: str) -> list[str]:
    deps: list[str] = []
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("require") or line.startswith(")"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("github.com/"):
            deps.append(parts[0])
    return deps


# ---------------------------------------------------------------------------
# Low-signal scoring (adapted for remote: no exposed_apis)
# ---------------------------------------------------------------------------


def _compute_remote_low_signal(
    repo_name: str,
    top_dirs: tuple[str, ...],
    deps: tuple[str, ...],
    recent_commits: tuple[str, ...],
) -> bool:
    """Same scoring as scan.py but without exposed_apis.

    Max possible score = 0.9 (missing the 0.2 from exposed_apis).
    Threshold stays at 0.3.
    """

    import re  # noqa: PLC0415

    score = 0.0

    # +0.3 — repo name carries a business keyword
    name_tokens = set(re.findall(r"[\w-]+", repo_name.lower()))
    if name_tokens & _BUSINESS_NAME_KEYWORDS:
        score += 0.3

    # +0.3 — at least one non-generic directory
    for dir_path in top_dirs:
        leaf = dir_path.rsplit("/", maxsplit=1)[-1].lower()
        if leaf and leaf not in _GENERIC_DIR_NAMES:
            score += 0.3
            break

    # +0.2 — at least one non-generic dependency
    for dep in deps:
        if dep.lower() not in _GENERIC_DEPS:
            score += 0.2
            break

    # +0.2 — at least one specific commit
    for commit in recent_commits:
        if not any(p.search(commit) for p in _VAGUE_COMMIT_PATTERNS):
            score += 0.2
            break

    return score < _LOW_SIGNAL_THRESHOLD
