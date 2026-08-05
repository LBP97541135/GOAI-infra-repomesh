"""Version metadata for the AgentTeams product fork.

ADR 0002 requires every RepoMesh release to report four compatibility values:
the RepoMesh version, the AgentTeams product-fork commit, the AgentTeams
upstream base commit and the runtime contract version. The pinned component
values live in ``upstream.toml`` next to this module; the RepoMesh version and
commit are injected by the build and are never hard-coded here.
"""

import os
import tomllib
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
from pathlib import Path

UPSTREAM_MANIFEST = Path(__file__).with_name("upstream.toml")
BUILD_COMMIT_ENV = "REPOMESH_BUILD_COMMIT"
UNKNOWN_REPOMESH_VERSION = "0+unknown"


class ComponentVersionsError(RuntimeError):
    """Raised when the AgentTeams component manifest is missing or incomplete."""


@dataclass(frozen=True, slots=True)
class ComponentVersions:
    """Pinned coordinates of the vendored AgentTeams component."""

    upstream_repo: str
    upstream_version: str
    upstream_commit: str
    fork_repo: str
    fork_branch: str
    fork_commit: str
    runtime_contract: str


@dataclass(frozen=True, slots=True)
class ReleaseIdentity:
    """The four ADR 0002 compatibility values reported by a release."""

    repomesh_version: str
    repomesh_commit: str
    fork_commit: str
    upstream_commit: str
    runtime_contract: str


def _read_manifest(path: Path) -> dict[str, object]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError as error:
        raise ComponentVersionsError(
            f"AgentTeams component manifest not found: {path}"
        ) from error
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise ComponentVersionsError(
            f"AgentTeams component manifest is not valid TOML: {path}"
        ) from error


def _string(manifest: dict[str, object], path: Path, *keys: str) -> str:
    node: object = manifest
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            raise ComponentVersionsError(
                f"AgentTeams component manifest {path} is missing key '{'.'.join(keys)}'"
            )
        node = node[key]
    if not isinstance(node, str):
        raise ComponentVersionsError(
            f"AgentTeams component manifest {path} key '{'.'.join(keys)}' must be a string"
        )
    return node


def load_component_versions(manifest_path: Path | None = None) -> ComponentVersions:
    """Parse ``upstream.toml`` into the four-tuple component coordinates."""
    path = manifest_path or UPSTREAM_MANIFEST
    manifest = _read_manifest(path)
    return ComponentVersions(
        upstream_repo=_string(manifest, path, "name"),
        upstream_version=_string(manifest, path, "version"),
        upstream_commit=_string(manifest, path, "commit"),
        fork_repo=_string(manifest, path, "product_fork", "repo"),
        fork_branch=_string(manifest, path, "product_fork", "branch"),
        fork_commit=_string(manifest, path, "product_fork", "commit"),
        runtime_contract=_string(manifest, path, "compatibility", "runtime_contract"),
    )


def repomesh_version() -> str:
    """Installed RepoMesh distribution version, or a sentinel when not installed."""
    try:
        return distribution_version("repomesh")
    except PackageNotFoundError:
        return UNKNOWN_REPOMESH_VERSION


def repomesh_commit() -> str:
    """Build-injected RepoMesh commit; empty when the build did not provide one."""
    return os.environ.get(BUILD_COMMIT_ENV, "")


def release_identity(manifest_path: Path | None = None) -> ReleaseIdentity:
    """Report the four ADR 0002 compatibility values for this release."""
    components = load_component_versions(manifest_path)
    return ReleaseIdentity(
        repomesh_version=repomesh_version(),
        repomesh_commit=repomesh_commit(),
        fork_commit=components.fork_commit,
        upstream_commit=components.upstream_commit,
        runtime_contract=components.runtime_contract,
    )
