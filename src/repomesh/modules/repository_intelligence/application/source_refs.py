"""Cross-repository source references — mechanism ⑤ (SOURCE) evidence.

A repository can *embed or pull in* another repository's code without a
build-time artifact: a git submodule pins a foreign URL, a ``go.work``
``use``/``replace`` points at a directory outside the repo boundary, a
package.json ``workspaces`` glob escapes the root, a Cargo ``path``
dependency resolves outside. Those are source-level references — the
compiler/package manager actually executes them, so they are
``confidence="confirmed"`` (docs/chenwenhui/仓库扫描链路问题清单-2026-08-25.md
§⑤).

The rule is deliberately strict: **only references that cross the
repository boundary count**. ``use ./cmd``, ``packages/*`` and
``path = "crates/util"`` name code *inside* this repository — not another
repository — so they produce nothing. A ``../`` path or a submodule URL
names something outside; its terminal directory name (or the URL's repo
name) becomes the evidence name, which scan_remote resolves through the
service registry exactly like mechanisms ①② ④.

Defensive contract, identical to the other mechanisms: malformed content
yields an empty result, never an exception; a ``${...}`` placeholder is
not a concrete reference and is skipped. These files declare no identity
for this repository, so :class:`SourceParseResult` carries references
only.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass

_MECHANISM = "SOURCE"
_CONFIDENCE = "confirmed"


@dataclass(frozen=True, slots=True)
class SourceRef:
    """One cross-repository source reference.

    ``name`` is the referenced repository's name as written — the
    terminal directory of a ``../`` path, the repo name of a submodule
    URL. scan_remote maps it 1:1 into a ``DepEvidence`` with
    ``mechanism="SOURCE"`` and ``confidence="confirmed"``; the graph
    resolves the name to a catalog repository through the service
    registry.
    """

    name: str
    mechanism: str = _MECHANISM
    confidence: str = _CONFIDENCE


@dataclass(frozen=True, slots=True)
class SourceParseResult:
    """What one source-reference file contributes to the card."""

    refs: tuple[SourceRef, ...] = ()


def parse_source_ref_file(filename: str, content: str) -> SourceParseResult:
    """Parse *content* of a source-reference file named *filename*.

    Dispatches by basename: ``.gitmodules`` (submodule URLs), ``go.work``
    (``use``/``replace``), ``package.json`` (``workspaces``), ``Cargo.toml``
    (workspace members and ``path`` dependencies). Anything else
    contributes nothing. Never raises.
    """

    base = filename.rsplit("/", 1)[-1]
    if base == ".gitmodules":
        return parse_gitmodules(content)
    if base == "go.work":
        return parse_go_work(content)
    if base == "package.json":
        return parse_package_workspaces(content)
    if base == "Cargo.toml":
        return parse_cargo_workspace(content)
    return SourceParseResult()


# ---------------------------------------------------------------------------
# .gitmodules — submodule URLs
# ---------------------------------------------------------------------------

_SUBMODULE_HEADER = re.compile(r'^\[submodule\s+"([^"]+)"\]\s*$')

#: URL shapes we understand: https/ssh/git protocol URLs and scp-style
#: ``git@host:owner/repo.git``. The repo name is the terminal segment.
_GIT_URL_TAIL = re.compile(r"[^/:@]+(?:\.git)?$")


def parse_gitmodules(content: str) -> SourceParseResult:
    """Extract submodule repo names from a ``.gitmodules`` file.

    Each ``[submodule "name"]`` section may declare ``url = …``; the
    terminal segment of that URL (sans ``.git``) is the referenced
    repository's name. Sections without a URL (or with a ``${…}``
    placeholder URL) contribute nothing.
    """

    refs: list[SourceRef] = []
    seen: set[str] = set()
    url: str | None = None
    in_section = False
    for raw_line in content.splitlines():
        line = raw_line.strip()
        header = _SUBMODULE_HEADER.match(line)
        if header is not None:
            if url:
                _add_source_ref(refs, seen, _repo_name_from_url(url))
            url = None
            in_section = True
            continue
        # A url line only counts inside a [submodule "…"] section; a bare
        # url line after malformed content must not leak into the result.
        if in_section and line.lower().startswith("url "):
            candidate = line.split(maxsplit=1)[1].strip()
            url = candidate.strip('"') if candidate else None
    if url:
        _add_source_ref(refs, seen, _repo_name_from_url(url))
    return SourceParseResult(refs=tuple(refs))


def _repo_name_from_url(url: str) -> str:
    """The repo name a git URL points at.

    ``https://host/owner/repo.git``, ``git@host:owner/repo.git`` and
    ``ssh://git@host/owner/repo`` all yield ``repo``. A ``${…}``
    placeholder yields ``""`` (skipped upstream).
    """

    url = url.strip().strip('"')
    if not url or "$" in url:
        return ""
    match = _GIT_URL_TAIL.search(url)
    if match is None:
        return ""
    return match.group(0).removesuffix(".git")


# ---------------------------------------------------------------------------
# go.work — use / replace
# ---------------------------------------------------------------------------

_GO_WORK_DIR_RE = re.compile(r"[./\w-]+")


def parse_go_work(content: str) -> SourceParseResult:
    """Extract cross-boundary ``use``/``replace`` paths from a go.work.

    ``use`` names the local modules in the workspace; ``replace``
    redirects a module to a local directory. Only paths that start with
    ``..`` escape this repository's root — they become SOURCE refs named
    after the terminal directory. In-repo ``use ./cmd`` contributes
    nothing.
    """

    refs: list[SourceRef] = []
    seen: set[str] = set()
    in_use_block = False
    in_replace_block = False
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line.startswith("use ("):
            in_use_block = True
            continue
        if in_use_block:
            if line == ")":
                in_use_block = False
            else:
                _add_go_use(refs, seen, line)
            continue
        if line.startswith("replace ("):
            in_replace_block = True
            continue
        if in_replace_block:
            if line == ")":
                in_replace_block = False
            else:
                _add_go_replace(refs, seen, line)
            continue
        if line.startswith("use "):
            for path in line.split()[1:]:
                _add_go_use(refs, seen, path)
            continue
        if line.startswith("replace "):
            _add_go_replace(refs, seen, line)
    return SourceParseResult(refs=tuple(refs))


def _add_go_use(
    refs: list[SourceRef], seen: set[str], line: str
) -> None:
    """One ``use`` entry (block line or inline token) → maybe a ref."""
    for token in _GO_WORK_DIR_RE.findall(line):
        if token.startswith(".."):
            _add_outside_ref(refs, seen, token)


def _add_go_replace(
    refs: list[SourceRef], seen: set[str], line: str
) -> None:
    """A ``replace old => path`` entry: only the local target counts."""
    if "=>" not in line:
        return
    target = line.split("=>", maxsplit=1)[1].strip()
    if target.startswith(".."):
        _add_outside_ref(refs, seen, target)


# ---------------------------------------------------------------------------
# package.json workspaces
# ---------------------------------------------------------------------------


def parse_package_workspaces(content: str) -> SourceParseResult:
    """Extract cross-boundary workspace globs from a package.json.

    ``workspaces`` may be a list of globs or ``{"packages": […]}``. Only
    globs that escape the repository root (``../…``) are references to
    other repositories; ``packages/*`` is this repo's own layout.
    """

    try:
        data = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return SourceParseResult()
    if not isinstance(data, dict):
        return SourceParseResult()

    workspaces = data.get("workspaces")
    patterns: list[str] = []
    if isinstance(workspaces, list):
        patterns = [p for p in workspaces if isinstance(p, str)]
    elif isinstance(workspaces, dict):
        packages = workspaces.get("packages")
        if isinstance(packages, list):
            patterns = [p for p in packages if isinstance(p, str)]

    refs: list[SourceRef] = []
    seen: set[str] = set()
    for pattern in patterns:
        stripped = pattern.strip()
        if stripped.startswith(".."):
            _add_outside_ref(refs, seen, stripped)
    return SourceParseResult(refs=tuple(refs))


# ---------------------------------------------------------------------------
# Cargo.toml — workspace members and path dependencies
# ---------------------------------------------------------------------------


def parse_cargo_workspace(content: str) -> SourceParseResult:
    """Extract cross-boundary references from a Cargo.toml.

    Two shapes count: ``[workspace].members`` entries that escape the
    root (``../shared-crate``) and ``path = "../…"`` values in
    ``[dependencies]`` / ``[dev-dependencies]`` / ``[workspace.dependencies]``.
    In-workspace members (``crates/*``) and registry deps are not source
    references.
    """

    try:
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        return SourceParseResult()
    if not isinstance(data, dict):
        return SourceParseResult()

    refs: list[SourceRef] = []
    seen: set[str] = set()

    workspace = data.get("workspace")
    if isinstance(workspace, dict):
        members = workspace.get("members")
        if isinstance(members, list):
            for member in members:
                if isinstance(member, str) and member.strip().startswith(".."):
                    _add_outside_ref(refs, seen, member.strip())

    for table_name in ("dependencies", "dev-dependencies"):
        table = data.get(table_name)
        if isinstance(table, dict):
            _add_cargo_path_deps(refs, seen, table)

    if isinstance(workspace, dict):
        wdeps = workspace.get("dependencies")
        if isinstance(wdeps, dict):
            _add_cargo_path_deps(refs, seen, wdeps)

    return SourceParseResult(refs=tuple(refs))


def _add_cargo_path_deps(
    refs: list[SourceRef], seen: set[str], table: dict
) -> None:
    """A dependency table whose entries may carry ``path = "../…"``."""
    for spec in table.values():
        if not isinstance(spec, dict):
            continue
        path = spec.get("path")
        if isinstance(path, str) and path.strip().startswith(".."):
            _add_outside_ref(refs, seen, path.strip())


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _add_outside_ref(
    refs: list[SourceRef], seen: set[str], path: str
) -> None:
    """Append a ref for a ``../`` path, terminal segment wins.

    ``../ts-common`` → ``ts-common``; ``../shared/*`` → ``shared``;
    ``../../org/libs/ts-util`` → ``ts-util``. Placeholders and wildcard
    segments are skipped; first-seen wins, case-insensitively.
    """

    segments = [seg for seg in path.replace("\\", "/").split("/") if seg]
    for segment in reversed(segments):
        if segment in (".", "..") or "*" in segment:
            continue
        if segment.strip() and "$" not in segment:
            _add_source_ref(refs, seen, segment)
        return


def _add_source_ref(refs: list[SourceRef], seen: set[str], name: str) -> None:
    stripped = name.strip()
    if not stripped:
        return
    key = stripped.lower()
    if key in seen:
        return
    seen.add(key)
    refs.append(SourceRef(name=stripped))
