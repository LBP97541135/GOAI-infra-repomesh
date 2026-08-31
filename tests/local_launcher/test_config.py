"""Loading the operator's config file.

The interesting case is the missing key. The launcher has no defaults to fall
back to for a roster path or an Origin allowlist -- guessing either would mean
either serving nothing or serving everyone -- so the load is written to raise
rather than to cope, and that is worth pinning.
"""

import json
from pathlib import Path

import pytest

from repomesh_local_launcher.config import DEFAULT_PORT, load_config

COMPLETE = {
    "membersFile": "D:/repo/scripts/bridge-e1/members.json",
    "enrollmentDir": "D:/repo/output/bridge-team/e1/enrollments",
    "envFile": "D:/repo/output/bridge-team/e1-members.env",
    "runtimeDir": "D:/repo/output/bridge-team/e1",
    "workspaceRoot": "D:/Project4work/.repomesh-e1/workspaces",
    "subset": "m7",
    "rosterVersion": "e1-2026-08-29",
    "allowedOrigins": ["http://127.0.0.1:5280"],
    "port": 8121,
}


def write_config(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_reads_every_key(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, COMPLETE))

    assert config.members_file == Path("D:/repo/scripts/bridge-e1/members.json")
    assert config.enrollment_dir == Path("D:/repo/output/bridge-team/e1/enrollments")
    assert config.env_file == Path("D:/repo/output/bridge-team/e1-members.env")
    assert config.runtime_dir == Path("D:/repo/output/bridge-team/e1")
    assert config.workspace_root == Path("D:/Project4work/.repomesh-e1/workspaces")
    assert config.subset == "m7"
    assert config.roster_version == "e1-2026-08-29"
    assert config.allowed_origins == ("http://127.0.0.1:5280",)
    assert config.port == 8121


def test_the_two_optional_keys_may_be_absent(tmp_path: Path) -> None:
    absent = ("workspaceRoot", "subset", "port")
    payload = {key: value for key, value in COMPLETE.items() if key not in absent}

    config = load_config(write_config(tmp_path, payload))

    assert config.workspace_root is None
    assert config.subset is None
    assert config.port == DEFAULT_PORT


@pytest.mark.parametrize(
    "missing",
    ["membersFile", "enrollmentDir", "envFile", "runtimeDir", "rosterVersion", "allowedOrigins"],
)
def test_a_missing_required_key_fails_the_load(tmp_path: Path, missing: str) -> None:
    payload = {key: value for key, value in COMPLETE.items() if key != missing}

    with pytest.raises(KeyError, match=missing):
        load_config(write_config(tmp_path, payload))
