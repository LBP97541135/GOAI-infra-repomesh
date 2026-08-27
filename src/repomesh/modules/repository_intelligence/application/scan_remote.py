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

from repomesh.modules.repository_intelligence.application.call_declarations import (
    parse_call_declarations,
)
from repomesh.modules.repository_intelligence.application.dep_parsers import (
    BuildFileResult,
    parse_build_file,
)
from repomesh.modules.repository_intelligence.application.deploy_parsers import (
    is_compose_filename,
    parse_deploy_file,
)
from repomesh.modules.repository_intelligence.application.resource_config import (
    parse_resource_config,
)
from repomesh.modules.repository_intelligence.application.scan import (
    _BUSINESS_NAME_KEYWORDS,
    _GENERIC_DEPS,
    _GENERIC_DIR_NAMES,
    _IGNORED_DIRS,
    _LOW_SIGNAL_THRESHOLD,
    _VAGUE_COMMIT_PATTERNS,
    _dedupe_api_routes,
    _match_api_routes,
    infer_name,
)
from repomesh.modules.repository_intelligence.application.source_refs import (
    parse_source_ref_file,
)
from repomesh.modules.repository_intelligence.domain import (
    AutoCard,
    DepEvidence,
    RepositoryProfile,
)
from repomesh.modules.repository_intelligence.infrastructure.platform import (
    FileEntry,
    Platform,
    PlatformFetcher,
    RepoInfo,
    UrlType,
    detect_platform,
)

_logger = logging.getLogger(__name__)

#: Dependency files we know how to parse, keyed by ecosystem. ``setup.py``
#: is deliberately absent — parsing it would require executing/AST-parsing
#: Python code for a marginal legacy ecosystem, so it is not claimed as
#: supported and is never fetched.
_DEP_FILE_MAP: dict[str, tuple[str, ...]] = {
    "python": ("requirements.txt", "pyproject.toml"),
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

    .. deprecated::
        Prefer passing URL and requirement separately via :func:`load_requirement`.
        This function is retained for backward compatibility.

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

    entry_repo_name = _infer_entry_repo_name(url)
    return ParsedInput(url=url, requirement=requirement, entry_repo_name=entry_repo_name)


def load_requirement(
    requirement: str | None = None,
    requirement_file: str | None = None,
) -> str:
    """Load requirement text from a direct string or a file.

    Parameters
    ----------
    requirement:
        Inline requirement text (``--requirement`` flag).
    requirement_file:
        Path to a Markdown or plain-text file (``--requirement-file`` flag).

    Exactly one of the two must be provided.
    """

    if requirement_file:
        from pathlib import Path  # noqa: PLC0415

        return Path(requirement_file).read_text(encoding="utf-8")
    if requirement:
        return requirement
    raise ValueError(
        "必须提供需求：使用 --requirement 传入文本，"
        "或使用 --requirement-file 传入文件路径"
    )


def extract_entry_repo_name(url: str) -> str | None:
    """Infer the entry repo name from a single-repo URL.

    Returns ``None`` for group/org URLs.
    """

    return _infer_entry_repo_name(url)


def _infer_entry_repo_name(url: str) -> str | None:
    """Pre-extract a candidate entry repo name from the URL path."""

    from urllib.parse import urlparse  # noqa: PLC0415

    parsed_url = urlparse(url)
    path_segments = [s for s in parsed_url.path.split("/") if s]
    if len(path_segments) >= 2:
        return infer_name(path_segments[-1])
    return None


def identify_url_type(url: str) -> UrlType:
    """Classify a pasted URL as a single repo, a group/org, or neither.

    Parsing only — nothing leaves this process, so the console can debounce
    against it on every keystroke. It is the *offline* sibling of
    :meth:`PlatformFetcher.identify`, which asks the platform and costs a
    round trip.

    The verdict is deliberately derived from :func:`extract_entry_repo_name`
    rather than from a second rule of its own: that function is what a
    single-repo scan actually uses to name the repository, so agreement
    between the badge the user sees and the scan that follows is structural
    instead of maintained by hand. Its ``>= 2 path segments`` rule cannot tell
    a GitLab subgroup (``/group/subgroup``) from a repo inside a group, and
    this function inherits that: such a URL reads as ``SINGLE_REPO``, and the
    scan will fail on it rather than silently scanning the wrong thing.
    """

    if detect_platform(url) is Platform.LOCAL:
        # A local path is not something the remote scanners can reach; the
        # console has no local-scan entry point, so "unknown" is the honest
        # answer rather than inventing a fourth verdict.
        return UrlType.UNKNOWN
    if extract_entry_repo_name(url) is not None:
        return UrlType.SINGLE_REPO

    from urllib.parse import urlparse  # noqa: PLC0415

    segments = [s for s in urlparse(url).path.split("/") if s]
    return UrlType.GROUP if segments else UrlType.UNKNOWN


# ---------------------------------------------------------------------------
# Remote AutoCard building
# ---------------------------------------------------------------------------


async def scan_remote(
    repo_info: RepoInfo,
    fetcher: PlatformFetcher,
) -> AutoCard:
    """Build an :class:`AutoCard` from a remote repository.

    Makes 2-3 API calls per repo plus one per unique interesting file:
    1. File tree → top_dirs + detect dependency files
    2. Commits → recent_commits
    3. (Optional) Dependency file content → deps
    4. (Optional) Source file content → exposed_apis

    ``exposed_apis`` collects HTTP routes from up to 30 source files with
    the same extraction as the local scanner (capped far lower because
    every file costs an API round trip).

    File content is cached by path for the duration of the scan: several
    channels select overlapping files (``package.json``/``Cargo.toml`` feed
    both the BUILD and the SOURCE channel), and a file that fails to fetch
    once is not fetched again by a later channel.
    """

    # 1. File tree
    file_entries = await fetcher.fetch_file_tree(repo_info.url)
    top_dirs = _extract_top_dirs(file_entries)
    dep_files_present = _find_dep_files(file_entries)

    # Per-path content cache, local to this repo scan. ``None`` (file
    # missing or fetch failed) is cached too, so a path selected by several
    # channels costs exactly one API round trip.
    content_cache: dict[str, str | None] = {}

    async def _fetch(path: str) -> str | None:
        """Fetch one file once; every later channel reuses the result."""
        if path not in content_cache:
            try:
                content_cache[path] = await fetcher.fetch_file_content(
                    repo_info.url, path
                )
            except Exception:  # noqa: BLE001 — a scan survives one bad file
                _logger.debug("Failed to fetch %s for %s", path, repo_info.name)
                content_cache[path] = None
        return content_cache[path]

    # 2. Commits
    try:
        commits = await fetcher.fetch_commits(repo_info.url, limit=5)
    except Exception:  # noqa: BLE001
        _logger.warning("Failed to fetch commits for %s", repo_info.name)
        commits = []

    # 3. Dependency file content (if any dep files exist).
    # Structured output: legacy names keep feeding the free-text ``deps``
    # field, BUILD evidence feeds the graph, identities register as aliases.
    deps: list[str] = []
    evidence: list[DepEvidence] = []
    identities: list[str] = []
    for dep_file in dep_files_present:
        try:
            content = await _fetch(dep_file)
            if content is not None:
                parsed = _parse_dep_file(dep_file, content)
                deps.extend(parsed.legacy_names)
                evidence.extend(parsed.evidence)
                identities.extend(parsed.identities)
        except Exception:  # noqa: BLE001
            _logger.debug("Failed to parse %s for %s", dep_file, repo_info.name)

    # 3b. Scan source files for runtime call declarations (Feign/Dubbo/gRPC).
    # pom.xml only reveals Maven (compile-time) deps; call chains live in
    # framework declarations. Only declared names are evidence — the
    # first-generation string guessing (_JAVA_SERVICE_PATTERNS) is gone.
    source_files = _find_java_service_files(file_entries)
    for source_path in source_files:
        try:
            content = await _fetch(source_path)
            if content is not None:
                for target in parse_call_declarations(content):
                    deps.append(target.name)
                    evidence.append(
                        DepEvidence(
                            name=target.name,
                            mechanism=target.mechanism,
                            confidence=target.confidence,
                        )
                    )
        except Exception:  # noqa: BLE001
            _logger.debug("Failed to parse %s for %s", source_path, repo_info.name)

    # 3c. Application config (application.yml/.properties/.env) — mechanism ③.
    # Resource identifiers are *shared* semantics, not call semantics, so they
    # are kept out of the legacy ``deps`` list: the graph's name-matching
    # fallback must never mint a call edge out of a database name.
    config_files = _find_resource_files(file_entries)
    for config_path in config_files:
        try:
            content = await _fetch(config_path)
            if content is not None:
                for target in parse_resource_config(config_path, content):
                    evidence.append(
                        DepEvidence(
                            name=target.name,
                            mechanism=target.mechanism,
                            confidence=target.confidence,
                        )
                    )
        except Exception:  # noqa: BLE001
            _logger.debug("Failed to parse %s for %s", config_path, repo_info.name)

    # 3d. Deployment manifests (docker-compose / k8s) — mechanism ④.
    # ``depends_on`` and Service-selector references name *services*, so
    # they resolve through the service registry exactly like mechanisms ①②
    # — never resource-to-resource like ③. The service names this repo
    # deploys (compose services, k8s app labels) are deploy identities, so
    # other repos' deployment references can resolve back here.
    deploy_files = _find_deploy_files(file_entries)
    deploy_identities: list[str] = []
    for deploy_path in deploy_files:
        try:
            content = await _fetch(deploy_path)
            if content is not None:
                parsed = parse_deploy_file(deploy_path, content)
                for target in parsed.targets:
                    evidence.append(
                        DepEvidence(
                            name=target.name,
                            mechanism=target.mechanism,
                            confidence=target.confidence,
                        )
                    )
                deploy_identities.extend(parsed.identities)
        except Exception:  # noqa: BLE001
            _logger.debug("Failed to parse %s for %s", deploy_path, repo_info.name)

    # 3e. Cross-repository source references — mechanism ⑤ (SOURCE).
    # A submodule URL or a ``../`` workspace path names *code from another
    # repository*, so it is confirmed evidence (the compiler/package
    # manager executes it) — resolved through the service registry like
    # mechanisms ①②④. In-repo entries (``use ./cmd``, ``packages/*``)
    # are deliberately ignored by the parsers: they are not cross-repo
    # references. source refs land in ``deps`` like other confirmed
    # mechanisms so keyword discovery and scoring see them.
    source_ref_files = _find_source_ref_files(file_entries)
    for source_path in source_ref_files:
        try:
            content = await _fetch(source_path)
            if content is not None:
                parsed = parse_source_ref_file(source_path, content)
                for ref in parsed.refs:
                    deps.append(ref.name)
                    evidence.append(
                        DepEvidence(
                            name=ref.name,
                            mechanism=ref.mechanism,
                            confidence=ref.confidence,
                        )
                    )
        except Exception:  # noqa: BLE001
            _logger.debug("Failed to parse %s for %s", source_path, repo_info.name)

    # 3f. Exposed API routes (HTTP method + path) — lightweight collection.
    # Same framework regexes and ``framework:route`` output shape as the
    # local scanner (scan.py::_scan_exposed_apis), capped lower because
    # every source file costs an API round trip.  Routes are card metadata
    # for prompt clarity, not dependency evidence — nothing enters ``deps``
    # or ``dep_evidence`` here.
    api_files = _find_api_source_files(file_entries)
    api_routes: list[str] = []
    for api_path in api_files:
        try:
            content = await _fetch(api_path)
            if content is not None:
                api_routes.extend(_match_api_routes(content))
        except Exception:  # noqa: BLE001
            _logger.debug("Failed to parse %s for %s", api_path, repo_info.name)
    exposed_apis = _dedupe_api_routes(api_routes)

    # Deduplicate deps, evidence, identities, and deploy identities
    # (case-insensitive keys).
    (
        unique_deps,
        unique_evidence,
        unique_identities,
        unique_deploy_identities,
    ) = _dedupe_scan_output(deps, evidence, identities, deploy_identities)

    # low_signal scoring (without exposed_apis, max score = 0.9).
    low_signal = _compute_remote_low_signal(
        repo_info.name, top_dirs, unique_deps, tuple(commits)
    )

    return AutoCard(
        top_dirs=tuple(top_dirs),
        deps=unique_deps,
        dep_evidence=unique_evidence,
        identities=unique_identities,
        deploy_identities=unique_deploy_identities,
        recent_commits=tuple(commits),
        exposed_apis=exposed_apis,
        low_signal=low_signal,
    )


async def scan_org(
    group_url: str,
    fetcher: PlatformFetcher,
    *,
    on_progress: Callable[[int, int, str], None] | None = None,
    max_workers: int = 5,
    include_forks: bool = False,
) -> list[RepositoryProfile]:
    """Scan all repos under a group/org URL.

    1. List all repos.
    2. Filter out archived / empty / fork (unless *include_forks*).
    3. Concurrently build AutoCard for each.
    4. Return profiles.

    A repository that fails to scan is returned as a profile with
    ``scan_status="failed"`` and ``auto_card=None`` — never as a plausible
    empty card. The caller decides what to do with it; :func:`register`
    filters it out of the catalog.
    """

    all_repos = await fetcher.list_repos(group_url)
    repos = [
        r
        for r in all_repos
        if not r.should_skip and (include_forks or not r.fork)
    ]

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
                _logger.warning("Failed to scan %s, marking scan failed", repo.name)
                completed += 1
                if on_progress is not None:
                    on_progress(completed, total, repo.name)
                return RepositoryProfile(
                    name=repo.name,
                    url=repo.url,
                    description=repo.description,
                    auto_card=None,
                    scan_status="failed",
                )
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


async def scan_single_repo(
    repo_url: str,
    fetcher: PlatformFetcher,
) -> RepositoryProfile:
    """Scan one repository given only its URL.

    The single-repo peer of :func:`scan_org`. The name comes from the
    platform's own metadata (:meth:`PlatformFetcher.resolve_repo_name`) — the
    same source ``scan_org`` uses for every repo it lists — and falls back to
    the URL path (:func:`extract_entry_repo_name`) only when the platform
    cannot confirm the URL names an existing repository. That keeps the
    registered name authoritative (a GitLab project can be renamed in the UI
    while its URL path stays the same, and ``order-service.git`` in a URL is
    not the project's name) at the cost of one metadata call.

    Unlike :func:`scan_org`, a failure here is *not* swallowed into an empty
    AutoCard: an org scan of 40 repos should not die because one repo is
    unreadable, but a scan the user asked for by URL has nothing left to
    report if it fails, and a registered card full of nothing would be worse
    than an error.
    """

    name = await fetcher.resolve_repo_name(repo_url)
    if name is None:
        name = extract_entry_repo_name(repo_url)
    if name is None:
        raise ValueError(f"not a single-repository URL: {repo_url}")

    card = await scan_remote(RepoInfo(name=name, url=repo_url), fetcher)
    return RepositoryProfile(name=name, url=repo_url, auto_card=card)


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


#: Maximum dependency files fetched per repo, and per file kind (rate-limit
#: friendly: a monorepo can hold dozens of pom.xml/package.json).
_MAX_DEP_FILES_PER_TYPE = 10
_MAX_DEP_FILES_PER_REPO = 30


def _find_dep_files(entries: list[FileEntry]) -> list[str]:
    """Find dependency files anywhere in the file tree.

    Root-level scanning missed nested build files — a Go service in a
    subdirectory, a monorepo with one pom.xml per module. Search the whole
    tree, then apply the per-kind and per-repo caps so the fetch list stays
    rate-limit friendly.
    """

    found: list[str] = []
    for entry in entries:
        if entry.is_dir:
            continue
        filename = entry.path.rsplit("/", 1)[-1]
        if filename in _ALL_DEP_FILES:
            found.append(entry.path)

    # Per-kind cap first (at most N of any one file type), then overall cap.
    capped: list[str] = []
    per_type: dict[str, int] = {}
    for path in sorted(found):
        filename = path.rsplit("/", 1)[-1]
        if per_type.get(filename, 0) >= _MAX_DEP_FILES_PER_TYPE:
            continue
        per_type[filename] = per_type.get(filename, 0) + 1
        capped.append(path)
    return capped[:_MAX_DEP_FILES_PER_REPO]


#: Application config files that may declare shared resources (mechanism ③).
#: Only *application* configuration is read here — k8s/Helm manifests are
#: deployment topology (mechanism ④, Phase 5) and are deliberately not
#: matched by these patterns.
_MAX_CONFIG_FILES_PER_REPO = 10


def _is_resource_config_filename(filename: str) -> bool:
    """True for application config files that declare shared resources.

    ``application*.yml|yaml|properties`` (profiles included) and
    ``bootstrap.yml|yaml``. ``.env`` files are deliberately excluded: they
    are local environment overrides (often carrying secrets), not a
    repository's declaration that it *shares* a resource — a
    ``DATABASE_URL`` in .env is deployment config, not shared-state
    evidence. A ``deployment.yaml`` or ``values.yaml`` is deployment
    topology, not shared-resource config.
    """

    fname = filename.lower()
    if fname.startswith("application") and fname.endswith(
        (".yml", ".yaml", ".properties")
    ):
        return True
    return fname in ("bootstrap.yml", "bootstrap.yaml")


def _find_resource_files(entries: list[FileEntry]) -> list[str]:
    """Find application config files anywhere in the file tree.

    Whole-tree search like :func:`_find_dep_files` — Spring services keep
    ``src/main/resources/application.yml``, not a root-level file — capped
    so the fetch list stays rate-limit friendly.
    """

    found = [
        entry.path
        for entry in entries
        if not entry.is_dir and _is_resource_config_filename(entry.path.rsplit("/", 1)[-1])
    ]
    return sorted(found)[:_MAX_CONFIG_FILES_PER_REPO]


#: Deployment manifests that may declare deployment topology (mechanism ④).
#: Matched by filename shape (compose, k8s manifest keywords) or by living
#: under a deployment directory (k8s/, deploy/, helm/, charts/, manifests/).
_MAX_DEPLOY_FILES_PER_REPO = 10

#: Filename keywords that mark a YAML as a k8s manifest.
_K8S_MANIFEST_KEYWORDS = (
    "deployment",
    "statefulset",
    "daemonset",
    "service",
    "ingress",
)

#: Directories that conventionally hold deployment manifests.
_DEPLOY_DIR_NAMES = ("k8s", "deploy", "deployments", "helm", "charts", "manifests")


def _is_deploy_filename(filename: str, path: str) -> bool:
    """True for compose files and k8s/Helm manifests.

    Compose matches by name (``docker-compose*.yml``, ``compose*.yaml``).
    Other manifests must be YAML and either carry a k8s keyword in the
    filename (``deployment.yaml``, ``service.yaml``) or live under a
    deployment directory (``k8s/``, ``helm/templates/``). Helm
    ``values*.yaml`` files are deliberately excluded: they carry
    chart-specific data, not dependency signals.
    """

    fname = filename.lower()
    if is_compose_filename(fname):
        return True
    if fname.startswith("values"):
        return False
    if not fname.endswith((".yaml", ".yml")):
        return False
    if any(segment in path.lower().split("/") for segment in _DEPLOY_DIR_NAMES):
        return True
    return any(keyword in fname for keyword in _K8S_MANIFEST_KEYWORDS)


def _find_deploy_files(entries: list[FileEntry]) -> list[str]:
    """Find deployment manifests anywhere in the file tree.

    Same whole-tree strategy as :func:`_find_resource_files`, capped so the
    fetch list stays rate-limit friendly.
    """

    found = [
        entry.path
        for entry in entries
        if not entry.is_dir
        and _is_deploy_filename(entry.path.rsplit("/", 1)[-1], entry.path)
    ]
    return sorted(found)[:_MAX_DEPLOY_FILES_PER_REPO]


#: Source-reference files that may pull in *another* repository's code
#: (mechanism ⑤). `.gitmodules` pins submodule URLs; `go.work` points
#: workspace modules outside the repo; `package.json`/`Cargo.toml` may
#: declare cross-boundary workspaces/path deps. The BUILD channel also
#: reads package.json/Cargo.toml — each mechanism keeps its own parse of
#: the content (a small file, and the channels stay independent).
_SOURCE_REF_FILE_NAMES = (".gitmodules", "go.work", "package.json", "Cargo.toml")
_MAX_SOURCE_REF_FILES_PER_REPO = 10


def _find_source_ref_files(entries: list[FileEntry]) -> list[str]:
    """Find cross-repository source-reference files anywhere in the tree."""

    found = [
        entry.path
        for entry in entries
        if not entry.is_dir and entry.path.rsplit("/", 1)[-1] in _SOURCE_REF_FILE_NAMES
    ]
    return sorted(found)[:_MAX_SOURCE_REF_FILES_PER_REPO]


#: Source suffixes scanned for exposed API routes — the same set as the
#: local scanner (``scan.py::_scan_exposed_apis``), so local/remote
#: extraction is structurally identical (see :func:`_match_api_routes`).
_API_SOURCE_SUFFIXES = frozenset({".py", ".ts", ".tsx", ".js", ".jsx", ".go"})

#: Maximum source files fetched per repo for API-route scanning.  The local
#: scanner reads up to 500 files from disk; the remote scanner pays one API
#: round trip per file, so the cap is far lower but the extraction logic is
#: identical.
_MAX_API_SOURCE_FILES_PER_REPO = 30


def _find_api_source_files(entries: list[FileEntry]) -> list[str]:
    """Find source files likely to declare API routes.

    Same suffix set and ignored-directory filter as the local scanner's
    :func:`scan._iter_source_files`, capped much lower because every file
    costs an API round trip.  Sorted for determinism.
    """

    candidates: list[str] = []
    for entry in entries:
        if entry.is_dir:
            continue
        if not entry.path.endswith(tuple(_API_SOURCE_SUFFIXES)):
            continue
        if any(part in _IGNORED_DIRS for part in entry.path.split("/")):
            continue
        candidates.append(entry.path)
    return sorted(candidates)[:_MAX_API_SOURCE_FILES_PER_REPO]


# ---------------------------------------------------------------------------
# Dependency file parsing — mechanism ① (BUILD) evidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _DepParseResult:
    """What one build manifest contributes to the card and the graph.

    ``legacy_names`` keep feeding the free-text ``deps`` field (keyword
    discovery, low-signal scoring) exactly as before; ``evidence`` is the
    structured Phase 2 output the graph consumes; ``identities`` are this
    repository's own declared identifiers, which the service registry
    registers as aliases so evidence naming them resolves back here.
    """

    legacy_names: tuple[str, ...] = ()
    evidence: tuple[DepEvidence, ...] = ()
    identities: tuple[str, ...] = ()


def _dedupe_scan_output(
    deps: list[str],
    evidence: list[DepEvidence],
    identities: list[str],
    deploy_identities: list[str],
) -> tuple[
    tuple[str, ...],
    tuple[DepEvidence, ...],
    tuple[str, ...],
    tuple[str, ...],
]:
    """Deduplicate legacy deps, evidence, build identities, deploy identities.

    Evidence names (``group:artifact`` coordinates) already carry the Maven
    composite, so a bare ``artifact`` legacy dep is a different key than
    ``group:artifact`` evidence — both are kept, and the graph's registry
    resolution decides what they point to.
    """

    unique_deps: list[str] = []
    seen_deps: set[str] = set()
    for dep in deps:
        key = dep.lower()
        if key not in seen_deps:
            seen_deps.add(key)
            unique_deps.append(dep)

    unique_evidence: list[DepEvidence] = []
    seen_evidence: set[str] = set()
    for ev in evidence:
        key = f"{ev.name.lower()}|{ev.mechanism}"
        if key not in seen_evidence:
            seen_evidence.add(key)
            unique_evidence.append(ev)

    unique_identities: list[str] = []
    seen_identities: set[str] = set()
    for identity in identities:
        key = identity.lower()
        if key not in seen_identities:
            seen_identities.add(key)
            unique_identities.append(identity)

    unique_deploy_identities: list[str] = []
    seen_deploy_identities: set[str] = set()
    for identity in deploy_identities:
        key = identity.lower()
        if key not in seen_deploy_identities:
            seen_deploy_identities.add(key)
            unique_deploy_identities.append(identity)

    return (
        tuple(unique_deps),
        tuple(unique_evidence),
        tuple(unique_identities),
        tuple(unique_deploy_identities),
    )


def _parse_dep_file(filename: str, content: str) -> _DepParseResult:
    """Parse a dependency file into legacy names, BUILD evidence, identities.

    Delegates to :mod:`dep_parsers` (matched on the file's basename so
    nested build files resolve too); file kinds that module does not handle
    (``setup.py``) contribute nothing rather than a guess. Managed Maven
    entries (``<dependencyManagement>``) are a version policy, so they
    never become evidence.
    """
    base = filename.rsplit("/", 1)[-1]
    result = parse_build_file(base, content)
    if result is None:
        return _DepParseResult()

    direct = [dep for dep in result.deps if not dep.managed]
    return _DepParseResult(
        legacy_names=tuple(dep.coordinates for dep in direct),
        evidence=tuple(
            DepEvidence(
                name=dep.coordinates,
                mechanism="BUILD",
                confidence="confirmed",
            )
            for dep in direct
        ),
        identities=_identity_aliases(result),
    )


def _identity_aliases(result: BuildFileResult) -> tuple[str, ...]:
    """The aliases this repository answers to, most precise first.

    A Maven identity ``com.example:auth-service`` yields both the composite
    and the bare artifactId, so evidence that names either resolves.
    """
    if result.identity is None:
        return ()
    group, _, artifact = result.identity.partition(":")
    if group and artifact:
        return (result.identity, artifact)
    return (result.identity,)


#: Source files worth scanning for runtime call declarations (Feign/Dubbo
#: live in Service/Controller/Client/Config classes; gRPC stubs live in
#: generated ``*Grpc.java`` files).
_JAVA_SCAN_PATTERNS = (
    "ServiceImpl.java",
    "Service.java",
    "Controller.java",
    "Client.java",
    "Config.java",
    "Grpc.java",
)

#: Maximum number of source files to fetch per repo (rate-limit friendly).
_MAX_JAVA_FILES_PER_REPO = 15


def _find_java_service_files(entries: list[FileEntry]) -> list[str]:
    """Find source files likely to declare runtime calls.

    Feign clients, Dubbo consumers and gRPC stubs cluster in the same
    filename shapes as the old inter-service-call scan used, so the file
    selection is unchanged — only the parsing (now
    :func:`call_declarations.parse_call_declarations`) is framework-anchored.

    Deterministic by construction: every candidate is collected first, then
    sorted by path and capped, so the same repository always yields the
    same file set no matter the order the platform API returned the tree in.
    """

    candidates = [
        entry.path
        for entry in entries
        if not entry.is_dir
        and entry.path.endswith(".java")
        and any(pat in entry.path.rsplit("/", 1)[-1] for pat in _JAVA_SCAN_PATTERNS)
    ]
    return sorted(candidates)[:_MAX_JAVA_FILES_PER_REPO]


def _compute_remote_low_signal(
    repo_name: str,
    top_dirs: tuple[str, ...],
    deps: tuple[str, ...],
    recent_commits: tuple[str, ...],
) -> bool:
    """Same scoring as scan.py (exposed_apis does not contribute to either).

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
