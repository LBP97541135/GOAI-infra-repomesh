import re
import tomllib
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest

from repomesh.integrations.agentteams import versions
from repomesh.integrations.agentteams.versions import (
    UNKNOWN_REPOMESH_VERSION,
    UPSTREAM_MANIFEST,
    ComponentVersionsError,
    load_component_versions,
    release_identity,
)

REPO_ROOT = Path(__file__).parents[2]
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def manifest() -> dict[str, object]:
    return tomllib.loads(UPSTREAM_MANIFEST.read_text(encoding="utf-8"))


def test_manifest_declares_the_full_four_tuple() -> None:
    raw = manifest()

    assert set(raw) >= {
        "name",
        "version",
        "commit",
        "license",
        "controller_api",
        "matrix_api",
        "product_fork",
        "compatibility",
    }
    assert set(raw["product_fork"]) == {"repo", "branch", "commit"}
    assert set(raw["compatibility"]) == {"runtime_contract"}

    components = load_component_versions()
    assert components.upstream_repo == "agentscope-ai/AgentTeams"
    assert components.upstream_version == "v1.2.0"
    assert isinstance(components.fork_repo, str)
    assert isinstance(components.fork_commit, str)


def test_upstream_commit_is_a_full_lowercase_sha() -> None:
    components = load_component_versions()

    assert FULL_SHA.match(components.upstream_commit)


def test_runtime_contract_points_at_an_existing_contract_directory() -> None:
    components = load_component_versions()

    assert components.runtime_contract == "runtime.v1"
    assert (REPO_ROOT / "contracts" / "runtime" / "v1").is_dir()


def test_fork_branch_is_pinned_to_the_product_branch() -> None:
    assert load_component_versions().fork_branch == "repomesh/main"


def test_release_identity_reports_the_four_adr_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPOMESH_BUILD_COMMIT", "b" * 40)
    components = load_component_versions()

    identity = release_identity()

    assert identity.repomesh_version
    assert identity.repomesh_commit == "b" * 40
    assert identity.fork_commit == components.fork_commit
    assert identity.upstream_commit == components.upstream_commit
    assert identity.runtime_contract == "runtime.v1"


def test_release_identity_uses_documented_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_distribution(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr(versions, "distribution_version", missing_distribution)
    monkeypatch.delenv("REPOMESH_BUILD_COMMIT", raising=False)

    identity = release_identity()

    assert identity.repomesh_version == UNKNOWN_REPOMESH_VERSION
    assert identity.repomesh_commit == ""


def test_loader_reports_a_missing_manifest(tmp_path: Path) -> None:
    with pytest.raises(ComponentVersionsError, match="not found"):
        load_component_versions(tmp_path / "upstream.toml")
