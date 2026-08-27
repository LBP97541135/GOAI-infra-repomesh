"""Repository scanner — generates an :class:`AutoCard` from a local checkout.

Usage (CLI)::

    python -m repomesh.modules.repository_intelligence.application.scan /path/to/repo

The scanner is intentionally dependency-free: it reads the working tree with
``pathlib``, shells out to ``git`` for recent commits, and uses regexes for
API-route detection.  No framework SDK is imported.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Final

from repomesh.modules.repository_intelligence.domain import AutoCard

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Directories that are never informative for discovery.
_IGNORED_DIRS: Final[frozenset[str]] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "dist",
        "build",
        ".next",
        ".turbo",
        "target",
        ".idea",
        ".vscode",
    }
)

#: Generic directory names that carry no business semantics.
_GENERIC_DIR_NAMES: Final[frozenset[str]] = frozenset(
    {
        "src",
        "lib",
        "libs",
        "test",
        "tests",
        "spec",
        "specs",
        "a",
        "b",
        "c",
        "handler",
        "handlers",
        "util",
        "utils",
        "common",
        "misc",
        "internal",
        "pkg",
        "bin",
        "scripts",
        "config",
        "configs",
    }
)

#: Business keywords that bump the ``low_signal`` score when found in a repo name.
_BUSINESS_NAME_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        "pay",
        "order",
        "user",
        "auth",
        "notify",
        "email",
        "front",
        "api",
        "service",
        "admin",
        "config",
        "cart",
        "checkout",
        "billing",
        "invoice",
        "account",
        "profile",
        "search",
        "catalog",
        "inventory",
        "shipping",
        "warehouse",
        "report",
        "analytics",
        "dashboard",
        "gateway",
        "proxy",
        "worker",
        "scheduler",
        "webhook",
    }
)

#: Pure-framework dependencies that are not business-specific.
_GENERIC_DEPS: Final[frozenset[str]] = frozenset(
    {
        "fastapi",
        "flask",
        "django",
        "starlette",
        "express",
        "koa",
        "nestjs",
        "react",
        "vue",
        "angular",
        "next",
        "nuxt",
        "redis",
        "celery",
        "sqlalchemy",
        "sequelize",
        "mongoose",
        "prisma",
        "pytest",
        "jest",
        "vitest",
        "mocha",
        "eslint",
        "ruff",
        "black",
        "mypy",
        "typescript",
        "pydantic",
        "lodash",
        "axios",
        "requests",
        "httpx",
        "aiohttp",
    }
)

#: Commit messages that are too vague to be informative.
_VAGUE_COMMIT_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"^\s*(fix|bug|update|refactor|wip|chore|tidy|cleanup)\b", re.IGNORECASE),
    re.compile(r"^\s*\w+\s*$", re.IGNORECASE),  # single word
)

#: Dependency files recognised by the scanner, keyed by ecosystem.
_PYTHON_DEP_FILES: Final[tuple[str, ...]] = ("requirements.txt", "pyproject.toml", "setup.py")
_NODE_DEP_FILES: Final[tuple[str, ...]] = ("package.json",)
_GO_DEP_FILES: Final[tuple[str, ...]] = ("go.mod",)

#: API-route regex patterns per framework.  Each pattern has one capturing group
#: for the route path.
_API_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    # FastAPI / Flask / Starlette  —  @app.get("/path"), @router.post("/path")
    (
        "fastapi",
        re.compile(
            r'@\w+\.(?:get|post|put|delete|patch|head|options)\(\s*"([^"]+)"',
            re.IGNORECASE,
        ),
    ),
    # Express  —  app.get("/path", ...), router.post("/path", ...)
    (
        "express",
        re.compile(
            r'\b(?:app|router|server)\.(?:get|post|put|delete|patch)'
            r'\(\s*["\x27`]([^"\x27`]+)["\x27`]',
            re.IGNORECASE,
        ),
    ),
    # Gin  —  r.GET("/path", ...), router.POST("/path", ...)
    (
        "gin",
        re.compile(
            r'\b(?:r|router|engine|group)\.'
            r'(?:GET|POST|PUT|DELETE|PATCH)\(\s*"([^"]+)"',
            re.IGNORECASE,
        ),
    ),
)

#: Minimum score below which the card is flagged ``low_signal``.
_LOW_SIGNAL_THRESHOLD: Final[float] = 0.3

#: Mapping from dependency-file presence to language label.
_LANGUAGE_FILES: Final[tuple[tuple[str, str], ...]] = (
    ("requirements.txt", "python"),
    ("pyproject.toml", "python"),
    ("setup.py", "python"),
    ("package.json", "javascript"),
    ("go.mod", "go"),
    ("Cargo.toml", "rust"),
    ("pom.xml", "java"),
    ("build.gradle", "java"),
    ("build.gradle.kts", "java"),
    ("composer.json", "php"),
    ("Gemfile", "ruby"),
)


# ---------------------------------------------------------------------------
# Public API — scan_repo + inference helpers
# ---------------------------------------------------------------------------


def infer_name(source: str) -> str:
    """Infer a repository name from a URL or local path.

    Examples::

        infer_name("https://github.com/org/order-service")   → "order-service"
        infer_name("git@github.com:org/payment.git")         → "payment"
        infer_name("D:\\\\repos\\\\user-service")              → "user-service"
        infer_name("./components/agentteams/hermes")         → "hermes"
    """

    source = source.strip().rstrip("/\\")
    # Strip trailing .git
    if source.endswith(".git"):
        source = source[: -len(".git")]
    # Take the last path segment regardless of separator.
    name = re.split(r"[/\\:]", source)[-1]
    return name or source


def infer_languages(repo_path: Path | str) -> tuple[str, ...]:
    """Detect programming languages from the presence of dependency files."""

    root = Path(repo_path)
    languages: list[str] = []
    for filename, language in _LANGUAGE_FILES:
        if (root / filename).exists() and language not in languages:
            languages.append(language)
    return tuple(languages)


def scan_repo(repo_path: Path | str, *, max_commits: int = 5) -> AutoCard:
    """Scan *repo_path* and return an :class:`AutoCard`.

    The function never raises on partial data — missing git history, missing
    dependency files, or unreadable files simply produce empty tuples.  This
    makes the scanner robust for ephemeral or partial checkouts.
    """

    root = Path(repo_path).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Not a directory: {root}")

    top_dirs = _scan_top_dirs(root)
    deps = _scan_dependencies(root)
    recent_commits = _scan_recent_commits(root, max_commits)
    exposed_apis = _scan_exposed_apis(root)
    low_signal = _compute_low_signal(root.name, top_dirs, deps, recent_commits)

    return AutoCard(
        top_dirs=top_dirs,
        deps=deps,
        recent_commits=recent_commits,
        exposed_apis=exposed_apis,
        low_signal=low_signal,
    )


# ---------------------------------------------------------------------------
# top_dirs
# ---------------------------------------------------------------------------


def _scan_top_dirs(root: Path, *, max_depth: int = 2) -> tuple[str, ...]:
    """Collect directory names up to *max_depth* levels deep.

    Returns relative paths like ``"src"``, ``"src/api"``, joined by ``/``.
    """

    collected: list[str] = []

    def _walk(current: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name)
        except (PermissionError, OSError):
            return
        for entry in entries:
            if not entry.is_dir():
                continue
            if entry.name in _IGNORED_DIRS or entry.name.startswith("."):
                continue
            relative = entry.relative_to(root).as_posix()
            collected.append(relative)
            _walk(entry, depth + 1)

    _walk(root, 1)
    # Cap to a reasonable number for prompt size.
    return tuple(collected[:80])


# ---------------------------------------------------------------------------
# deps
# ---------------------------------------------------------------------------


def _scan_dependencies(root: Path) -> tuple[str, ...]:
    """Read dependency manifests and return a flat list of package names."""

    deps: list[str] = []
    deps.extend(_read_python_deps(root))
    deps.extend(_read_node_deps(root))
    deps.extend(_read_go_deps(root))
    # Deduplicate while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for dep in deps:
        key = dep.lower()
        if key not in seen:
            seen.add(key)
            unique.append(dep)
    return tuple(unique)


def _read_python_deps(root: Path) -> list[str]:
    deps: list[str] = []

    req = root / "requirements.txt"
    if req.is_file():
        for line in req.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            # Strip version specifiers:  package>=1.0  →  package
            name = re.split(r"[>=<!\[;]", line, maxsplit=1)[0].strip()
            if name:
                deps.append(name)

    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        text = pyproject.read_text(encoding="utf-8", errors="replace")
        deps.extend(_extract_toml_array(text, "dependencies"))
        deps.extend(_extract_toml_array(text, "optional-dependencies"))

    return deps


def _extract_toml_array(text: str, section: str) -> list[str]:
    """Best-effort extraction of package names from a ``[project]`` table.

    Handles both ``dependencies = ["foo", "bar>=1.0"]`` and the optional deps
    table.  Not a full TOML parser — intentionally simple.
    """

    deps: list[str] = []
    pattern = rf'{section}\s*=\s*\[(.*?)\]'
    for match in re.finditer(pattern, text, re.DOTALL):
        inner = match.group(1)
        for quoted in re.findall(r'"([^"]+)"', inner):
            name = re.split(r"[>=<!\[;]", quoted, maxsplit=1)[0].strip()
            if name:
                deps.append(name)
    return deps


def _read_node_deps(root: Path) -> list[str]:
    pkg = root / "package.json"
    if not pkg.is_file():
        return []
    try:
        data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []
    deps: list[str] = []
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        section = data.get(key)
        if isinstance(section, dict):
            deps.extend(section.keys())
    return deps


def _read_go_deps(root: Path) -> list[str]:
    gomod = root / "go.mod"
    if not gomod.is_file():
        return []
    deps: list[str] = []
    for line in gomod.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith("require") or line.startswith(")"):
            continue
        # require (
        #    github.com/foo/bar v1.2.3
        # )
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("github.com/"):
            deps.append(parts[0])
    return deps


# ---------------------------------------------------------------------------
# recent_commits
# ---------------------------------------------------------------------------


def _scan_recent_commits(root: Path, max_commits: int) -> tuple[str, ...]:
    """Return the last *max_commits* one-line commit messages (no merges)."""

    try:
        result = subprocess.run(  # noqa: S603 — trusted local path
            [
                "git",
                "log",
                f"--oneline=-{max_commits}",
                "--no-merges",
                "--pretty=%s",
            ],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ()
    if result.returncode != 0:
        return ()
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return tuple(lines)


# ---------------------------------------------------------------------------
# exposed_apis
# ---------------------------------------------------------------------------


def _scan_exposed_apis(root: Path) -> tuple[str, ...]:
    """Scan source files for API route definitions.

    Supports FastAPI/Flask (``@app.get(...)``), Express (``app.get(...)``),
    and Gin (``r.GET(...)``).  Returns a deduplicated list of route paths
    prefixed with the framework name for prompt clarity.
    """

    apis: list[str] = []
    source_suffixes = {".py", ".ts", ".tsx", ".js", ".jsx", ".go"}
    for path in _iter_source_files(root, source_suffixes, max_files=500):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        apis.extend(_match_api_routes(text))
    return _dedupe_api_routes(apis)


def _match_api_routes(text: str) -> list[str]:
    """Match ``framework:route`` entries in one source text.

    Shared by the local scanner (:func:`_scan_exposed_apis`) and the remote
    scanner (``scan_remote.py``) so both produce identical output shapes
    from the same framework regexes.  Not deduplicated — callers aggregate
    across files and run :func:`_dedupe_api_routes` once.
    """

    routes: list[str] = []
    for framework, pattern in _API_PATTERNS:
        for match in pattern.finditer(text):
            route = match.group(1).strip()
            if route:
                routes.append(f"{framework}:{route}")
    return routes


def _dedupe_api_routes(routes: list[str]) -> tuple[str, ...]:
    """Deduplicate ``framework:route`` entries and cap at 50."""

    seen: set[str] = set()
    unique: list[str] = []
    for api in routes:
        if api not in seen:
            seen.add(api)
            unique.append(api)
    return tuple(unique[:50])


def _iter_source_files(root: Path, suffixes: set[str], *, max_files: int = 500):
    count = 0
    for path in root.rglob("*"):
        if count >= max_files:
            break
        if not path.is_file():
            continue
        if path.suffix not in suffixes:
            continue
        # Skip ignored dirs.
        if any(part in _IGNORED_DIRS for part in path.parts):
            continue
        yield path
        count += 1


# ---------------------------------------------------------------------------
# low_signal scoring
# ---------------------------------------------------------------------------


def _compute_low_signal(
    repo_name: str,
    top_dirs: tuple[str, ...],
    deps: tuple[str, ...],
    recent_commits: tuple[str, ...],
) -> bool:
    """Return ``True`` when the card has too little signal for confident LLM
    matching.  Scoring rules (from the MVP plan)::

        score = 0
        +0.3  if repo name contains a business keyword
        +0.3  if any directory name is non-generic (has business semantics)
        +0.2  if any dependency is non-generic (business-specific)
        +0.2  if any commit message is specific (not a vague one-liner)

        score < 0.3 → low_signal = True
    """

    score = 0.0

    # +0.3 — repo name carries a business keyword
    name_lower = repo_name.lower()
    name_tokens = set(re.findall(r"[\w-]+", name_lower))
    if name_tokens & _BUSINESS_NAME_KEYWORDS:
        score += 0.3

    # +0.3 — at least one non-generic directory
    for dir_path in top_dirs:
        # Check the last component of each path.
        leaf = dir_path.rsplit("/", maxsplit=1)[-1].lower()
        if leaf and leaf not in _GENERIC_DIR_NAMES:
            score += 0.3
            break

    # +0.2 — at least one non-generic dependency
    for dep in deps:
        if dep.lower() not in _GENERIC_DEPS:
            score += 0.2
            break

    # +0.2 — at least one specific commit message
    for commit in recent_commits:
        if _is_specific_commit(commit):
            score += 0.2
            break

    return score < _LOW_SIGNAL_THRESHOLD


def _is_specific_commit(message: str) -> bool:
    """A commit is "specific" if it doesn't match any vague pattern."""

    return not any(pattern.search(message) for pattern in _VAGUE_COMMIT_PATTERNS)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(f"Usage: python -m {__name__} <repo_path>", file=sys.stderr)
        return 1

    card = scan_repo(args[0])
    payload = {
        "top_dirs": list(card.top_dirs),
        "deps": list(card.deps),
        "recent_commits": list(card.recent_commits),
        "exposed_apis": list(card.exposed_apis),
        "low_signal": card.low_signal,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
