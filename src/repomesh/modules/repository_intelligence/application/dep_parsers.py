"""Structured build-manifest parsers — mechanism ① (BUILD) evidence.

Replaces the string-regex guessing of the first-generation scan with a
real parser per ecosystem (docs/chenwenhui/仓库扫描链路问题清单-2026-08-25.md
§Phase 2). Every parser returns the same shape::

    BuildFileResult(identity, deps, managed)

- ``identity`` — the identifier this repository declares for itself
  (Maven ``groupId:artifactId``, npm package name, go module path, PEP 508
  distribution name). It feeds the service registry, so another
  repository's BUILD evidence that names this identifier resolves back to
  this repository.
- ``deps`` — direct build dependencies. scan_remote turns them into
  ``DepEvidence(mechanism="BUILD", confidence="confirmed")``.
- ``managed`` — Maven ``<dependencyManagement>`` entries only. These are a
  version policy, not a dependency: they never become edges.

Parsers are defensive by contract: malformed content yields an empty
result, never an exception — a scan must survive any one unparseable file.
"""

from __future__ import annotations

import json
import re
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BuildDep:
    """One build-time dependency with its Maven-style coordinates."""

    name: str
    version: str = ""
    group_id: str = ""
    managed: bool = False

    @property
    def coordinates(self) -> str:
        """The identifier other repositories use to reference this dep."""
        if self.group_id:
            return f"{self.group_id}:{self.name}"
        return self.name


@dataclass(frozen=True, slots=True)
class BuildFileResult:
    """What one build manifest declares about itself and its dependencies."""

    identity: str | None
    deps: tuple[BuildDep, ...] = ()
    managed: tuple[BuildDep, ...] = ()


_EMPTY = BuildFileResult(identity=None)


def parse_build_file(filename: str, content: str) -> BuildFileResult | None:
    """Parse *content* of a build manifest named *filename*.

    Returns ``None`` for file kinds this module does not handle (their
    legacy paths stay untouched); an empty result for unparseable content.
    """
    if filename == "pom.xml":
        return parse_pom(content)
    if filename == "package.json":
        return parse_package_json(content)
    if filename == "go.mod":
        return parse_go_mod(content)
    if filename == "pyproject.toml":
        return parse_pyproject(content)
    if filename == "requirements.txt":
        return parse_requirements(content)
    if filename in ("build.gradle", "build.gradle.kts"):
        return parse_gradle(content)
    if filename == "Cargo.toml":
        return parse_cargo(content)
    return None


# ---------------------------------------------------------------------------
# pom.xml (ElementTree, namespace-agnostic)
# ---------------------------------------------------------------------------


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(element: ET.Element, name: str) -> ET.Element | None:
    for child in element:
        if _localname(child.tag) == name:
            return child
    return None


def _children(element: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in element if _localname(c.tag) == name]


def _text(element: ET.Element | None) -> str:
    if element is None or element.text is None:
        return ""
    return element.text.strip()


def parse_pom(content: str) -> BuildFileResult:
    """Parse a Maven pom.xml.

    Direct ``<dependencies>`` become :class:`BuildDep`; the
    ``<dependencyManagement>`` block is classified separately (a version
    policy, not a dependency, and never an edge). The project's own
    ``groupId:artifactId`` is the identity; when the project inherits its
    group from a parent, the bare artifactId stands in.
    """
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return _EMPTY

    group_id = _text(_child(root, "groupId"))
    artifact_id = _text(_child(root, "artifactId"))
    identity: str | None = None
    if artifact_id:
        identity = f"{group_id}:{artifact_id}" if group_id else artifact_id

    deps: list[BuildDep] = []
    managed: list[BuildDep] = []
    dependencies = _child(root, "dependencies")
    if dependencies is not None:
        for dep in _children(dependencies, "dependency"):
            parsed = _parse_dependency(dep)
            if parsed is not None:
                deps.append(parsed)
    management = _child(root, "dependencyManagement")
    if management is not None:
        mgmt = _child(management, "dependencies")
        if mgmt is not None:
            for dep in _children(mgmt, "dependency"):
                parsed = _parse_dependency(dep, managed=True)
                if parsed is not None:
                    managed.append(parsed)

    return BuildFileResult(identity=identity, deps=tuple(deps), managed=tuple(managed))


def _parse_dependency(element: ET.Element, *, managed: bool = False) -> BuildDep | None:
    artifact = _text(_child(element, "artifactId"))
    if not artifact:
        return None
    return BuildDep(
        name=artifact,
        version=_text(_child(element, "version")),
        group_id=_text(_child(element, "groupId")),
        managed=managed,
    )


# ---------------------------------------------------------------------------
# package.json
# ---------------------------------------------------------------------------


def parse_package_json(content: str) -> BuildFileResult:
    """Parse package.json: name identity, dependency maps as BUILD deps."""
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _EMPTY
    if not isinstance(data, dict):
        return _EMPTY

    identity = data.get("name")
    if not isinstance(identity, str) or not identity.strip():
        identity = None

    deps: list[BuildDep] = []
    seen: set[str] = set()
    for section in ("dependencies", "devDependencies", "peerDependencies"):
        block = data.get(section)
        if not isinstance(block, dict):
            continue
        for name, version in block.items():
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            version_str = version if isinstance(version, str) else ""
            deps.append(BuildDep(name=name, version=version_str))

    return BuildFileResult(identity=identity, deps=tuple(deps))


# ---------------------------------------------------------------------------
# go.mod
# ---------------------------------------------------------------------------


def parse_go_mod(content: str) -> BuildFileResult:
    """Parse go.mod: ``module`` identity, ``require`` entries as BUILD deps.

    Only ``require`` statements are read — ``replace``/``exclude`` blocks
    describe how to fetch or prune modules, not what this module depends
    on. Direct and indirect requires are both recorded (indirect entries
    are transitive build deps and still evidence, if weaker).
    """
    identity: str | None = None
    deps: list[BuildDep] = []
    seen: set[str] = set()
    in_require_block = False

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line.startswith("module ") and identity is None:
            module_path = line.split(maxsplit=1)[1].strip()
            identity = module_path or None
            continue
        if line.startswith("require ("):
            in_require_block = True
            continue
        if in_require_block:
            if line == ")":
                in_require_block = False
            elif line:
                parts = line.split()
                _add_go_dep(deps, seen, parts[0], parts[1] if len(parts) > 1 else "")
            continue
        if line.startswith("require ") and "(" not in line:
            parts = line.split()
            if len(parts) >= 2:
                _add_go_dep(deps, seen, parts[1], parts[2] if len(parts) > 2 else "")

    return BuildFileResult(identity=identity, deps=tuple(deps))


def _add_go_dep(deps: list[BuildDep], seen: set[str], path: str, version: str) -> None:
    path = path.strip()
    if not path or path.startswith("//"):
        return
    key = path.lower()
    if key in seen:
        return
    seen.add(key)
    deps.append(BuildDep(name=path, version=version))


# ---------------------------------------------------------------------------
# pyproject.toml (tomllib) and requirements.txt
# ---------------------------------------------------------------------------


def parse_pyproject(content: str) -> BuildFileResult:
    """Parse pyproject.toml: PEP 621 ``[project]`` and Poetry tables.

    Identity comes from ``[project].name`` (falling back to
    ``[tool.poetry].name``). Dependencies come from ``[project].dependencies``
    and ``[tool.poetry.dependencies]``; the poetry ``python`` constraint and
    dev/optional groups are not dependencies of the package itself.
    """
    try:
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        return _EMPTY
    if not isinstance(data, dict):
        return _EMPTY

    project = data.get("project")
    identity: str | None = None
    if isinstance(project, dict):
        name = project.get("name")
        if isinstance(name, str) and name.strip():
            identity = name
    if identity is None:
        tool = data.get("tool")
        if isinstance(tool, dict):
            poetry = tool.get("poetry")
            if isinstance(poetry, dict):
                name = poetry.get("name")
                if isinstance(name, str) and name.strip():
                    identity = name

    deps: list[BuildDep] = []
    seen: set[str] = set()
    if isinstance(project, dict):
        for name, version in _pep508_list(project.get("dependencies")):
            _add_py_dep(deps, seen, name, version)

    tool = data.get("tool")
    if isinstance(tool, dict):
        poetry = tool.get("poetry")
        if isinstance(poetry, dict):
            table = poetry.get("dependencies")
            if isinstance(table, dict):
                for name, spec in table.items():
                    if name == "python":
                        continue
                    _add_py_dep(deps, seen, name, _poetry_version(spec))

    return BuildFileResult(identity=identity, deps=tuple(deps))


def parse_requirements(content: str) -> BuildFileResult:
    """Parse requirements.txt lines (PEP 508-ish, no identity)."""
    deps: list[BuildDep] = []
    seen: set[str] = set()
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            continue
        name, version = _split_pep508(stripped)
        _add_py_dep(deps, seen, name, version)
    return BuildFileResult(identity=None, deps=tuple(deps))


_PEP508_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")


def _split_pep508(item: str) -> tuple[str, str]:
    """Split a PEP 508 requirement into (name, specifier).

    Handles plain ``name``, ``name>=1.0``, extras (``name[all]>=1.0`` →
    bare ``name``) and environment markers (``; python_version >= "3.9"``).
    """
    body = item.split(";", maxsplit=1)[0].strip()
    match = _PEP508_NAME.match(body)
    if match is None:
        return body, ""
    return match.group(1), body[match.end():].strip()


def _pep508_list(value: object) -> list[tuple[str, str]]:
    """Normalise a PEP 621 dependencies value into (name, specifier) pairs."""
    result: list[tuple[str, str]] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                result.append(_split_pep508(item))
    elif isinstance(value, dict):
        for name, spec in value.items():
            if isinstance(name, str) and name.strip():
                result.append((name, _poetry_version(spec)))
    return result


def _poetry_version(spec: object) -> str:
    if isinstance(spec, str):
        return spec
    if isinstance(spec, dict):
        version = spec.get("version")
        return version if isinstance(version, str) else ""
    return ""


def _add_py_dep(
    deps: list[BuildDep], seen: set[str], name: str, version: str
) -> None:
    name = name.strip()
    if not name:
        return
    key = name.lower()
    if key in seen:
        return
    seen.add(key)
    deps.append(BuildDep(name=name, version=version))


# ---------------------------------------------------------------------------
# build.gradle / build.gradle.kts (Groovy & Kotlin DSL coordinate subset)
# ---------------------------------------------------------------------------

#: Gradle configurations treated as *product* BUILD evidence. Test-only
#: configurations are excluded — a test dependency is not a runtime edge.
_GRADLE_IGNORED_CONFIG_PREFIXES = ("test", "androidTest", "testFixtures")

#: ``implementation 'group:artifact:version'`` — optional parens allow the
#: multi-line ``implementation('a:b:c')`` form. ``$`` never appears in a
#: concrete coordinate (``${libs.foo}`` / ``$version`` are placeholders and
#: deliberately excluded).
_GRADLE_COORD = re.compile(
    r"""(\w+)\s*\(?\s*['"]([^'"$]+:[^'"$]+:[^'"$]+)['"]"""
)
#: ``implementation group: 'g', name: 'a'[, version: 'v']`` named form.
_GRADLE_NAMED = re.compile(
    r"""(\w+)\s*(?:\(\s*)?group\s*:\s*['"]([^'"]+)['"]\s*,\s*"""
    r"""name\s*:\s*['"]([^'"]+)['"]"""
)


def parse_gradle(content: str) -> BuildFileResult:
    """Parse build.gradle / build.gradle.kts dependency coordinates.

    Only ``dependencies { ... }`` blocks are read; both coordinate styles
    (``implementation 'g:a:v'`` and the named ``group:/name:`` form) are
    handled across single-line and parenthesised multi-line declarations.
    Test configurations (``testImplementation`` …), unresolved ``${...}``
    placeholders, and non-coordinate declarations (``project(...)``,
    ``files(...)``, ``fileTree(...)``) never match. The Gradle project name
    lives in settings.gradle, not here, so no identity is claimed.
    """
    deps: list[BuildDep] = []
    seen: set[str] = set()
    for block in re.findall(r"dependencies\s*\{([^}]*)\}", content, re.DOTALL):
        for match in _GRADLE_COORD.finditer(block):
            config, coords = match.group(1), match.group(2)
            if _is_test_config(config):
                continue
            _add_gradle_dep(deps, seen, coords)
        for match in _GRADLE_NAMED.finditer(block):
            config, group, name = match.group(1), match.group(2), match.group(3)
            if _is_test_config(config):
                continue
            _add_gradle_dep(deps, seen, f"{group}:{name}")
    return BuildFileResult(identity=None, deps=tuple(deps))


def _is_test_config(config: str) -> bool:
    lowered = config.lower()
    return any(lowered.startswith(prefix) for prefix in _GRADLE_IGNORED_CONFIG_PREFIXES)


def _add_gradle_dep(deps: list[BuildDep], seen: set[str], coordinates: str) -> None:
    parts = coordinates.split(":")
    group_id = parts[0] if len(parts) >= 1 else ""
    name = parts[1] if len(parts) >= 2 else coordinates
    version = parts[2] if len(parts) >= 3 else ""
    key = f"{group_id}:{name}".lower()
    if key in seen:
        return
    seen.add(key)
    deps.append(BuildDep(name=name, version=version, group_id=group_id))


# ---------------------------------------------------------------------------
# Cargo.toml (tomllib)
# ---------------------------------------------------------------------------


def parse_cargo(content: str) -> BuildFileResult:
    """Parse Cargo.toml: ``[package].name`` identity, ``[dependencies]`` /
    ``[build-dependencies]`` tables as BUILD deps.

    ``[dev-dependencies]`` are excluded (a test-only crate is not a runtime
    edge) and ``[workspace.dependencies]`` is a shared version policy — the
    Cargo analogue of Maven ``<dependencyManagement>`` — never a direct
    dependency. Both the plain ``name = "version"`` form and the table form
    ``name = { version = "1.0", path = "../x" }`` are read; path-only deps
    keep their crate name (the SOURCE channel records the path). A
    ``name = { workspace = true }`` entry inherits its version from that
    policy and declares no concrete coordinate here, so it is skipped.
    """
    try:
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        return _EMPTY
    if not isinstance(data, dict):
        return _EMPTY

    package = data.get("package")
    identity: str | None = None
    if isinstance(package, dict):
        name = package.get("name")
        if isinstance(name, str) and name.strip():
            identity = name

    deps: list[BuildDep] = []
    seen: set[str] = set()
    for section in ("dependencies", "build-dependencies"):
        table = data.get(section)
        if not isinstance(table, dict):
            continue
        for name, spec in table.items():
            if not isinstance(name, str) or not name.strip():
                continue
            if _cargo_workspace_inherit_only(spec):
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            deps.append(BuildDep(name=name, version=_cargo_version(spec)))

    return BuildFileResult(identity=identity, deps=tuple(deps))


def _cargo_workspace_inherit_only(spec: object) -> bool:
    """True for a table form that declares no concrete coordinate itself.

    ``{ workspace = true }`` (and ``{ workspace = true, features = [...] }``)
    inherits everything concrete from ``[workspace.dependencies]`` — a shared
    version policy, not a dependency of this crate. A table with ``version``,
    ``path`` or ``git`` names a concrete coordinate and stays.
    """
    if not isinstance(spec, dict):
        return False
    return not any(key in spec for key in ("version", "path", "git"))


def _cargo_version(spec: object) -> str:
    if isinstance(spec, str):
        return spec
    if isinstance(spec, dict):
        version = spec.get("version")
        return version if isinstance(version, str) else ""
    return ""
