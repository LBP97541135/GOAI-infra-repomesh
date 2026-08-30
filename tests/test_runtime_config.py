import os
import stat
from pathlib import Path

import pytest

from repomesh.modules.platform_config.runtime_config import (
    RuntimeConfigError,
    load_runtime_environment,
    read_runtime_config,
    write_runtime_config,
)
from repomesh.settings import Settings

RUNTIME_VALUES = {
    "REPOMESH_AGENTTEAMS_REQUIRED": "true",
    "REPOMESH_AGENTTEAMS_CONTROLLER_URL": "http://agentteams-controller:8090",
    "REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN": "controller-token",
    "REPOMESH_AGENTTEAMS_MATRIX_URL": "http://agentteams-controller:6167",
    "REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN": "matrix-token",
    "REPOMESH_AGENTTEAMS_STORAGE_ENDPOINT": "http://agentteams-controller:9000",
    "REPOMESH_AGENTTEAMS_STORAGE_ACCESS_KEY": "minio-user",
    "REPOMESH_AGENTTEAMS_STORAGE_SECRET_KEY": "minio-password",
    "REPOMESH_AGENTTEAMS_STORAGE_BUCKET": "agentteams-storage",
}


def test_runtime_config_round_trip_and_permissions(tmp_path: Path) -> None:
    target = tmp_path / "platform-runtime.env"
    assert write_runtime_config(RUNTIME_VALUES, target) == target
    assert read_runtime_config(target) == RUNTIME_VALUES
    if os.name == "posix":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_runtime_environment_does_not_override_explicit_value(tmp_path: Path) -> None:
    target = tmp_path / "platform-runtime.env"
    write_runtime_config(RUNTIME_VALUES, target)
    environment = {"REPOMESH_AGENTTEAMS_CONTROLLER_URL": "https://emergency.example.test"}
    loaded = load_runtime_environment(target, environ=environment)
    assert loaded == RUNTIME_VALUES
    assert environment["REPOMESH_AGENTTEAMS_CONTROLLER_URL"] == (
        "https://emergency.example.test"
    )
    assert environment["REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN"] == "matrix-token"


def test_runtime_environment_beats_dotenv_and_defaults(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "REPOMESH_AGENTTEAMS_REQUIRED=false\n"
        "REPOMESH_AGENTTEAMS_CONTROLLER_URL=http://dotenv-controller:8090\n",
        encoding="utf-8",
    )
    target = tmp_path / "platform-runtime.env"
    write_runtime_config(
        {
            "REPOMESH_AGENTTEAMS_REQUIRED": "true",
            "REPOMESH_AGENTTEAMS_CONTROLLER_URL": "http://runtime-controller:8090",
        },
        target,
    )
    monkeypatch.delenv("REPOMESH_AGENTTEAMS_REQUIRED", raising=False)
    monkeypatch.delenv("REPOMESH_AGENTTEAMS_CONTROLLER_URL", raising=False)
    load_runtime_environment(target)
    settings = Settings()
    assert settings.agentteams_required is True
    assert settings.agentteams_controller_url == "http://runtime-controller:8090"


@pytest.mark.parametrize(
    "content, message",
    [
        ("NOT_ALLOWED=value\n", "not allowed"),
        ("REPOMESH_AGENTTEAMS_REQUIRED=true\nREPOMESH_AGENTTEAMS_REQUIRED=false\n", "duplicated"),
        ("REPOMESH_AGENTTEAMS_REQUIRED\n", "malformed"),
        ("REPOMESH_AGENTTEAMS_REQUIRED=maybe\n", "must be true or false"),
        ("REPOMESH_AGENTTEAMS_CONTROLLER_URL=file:///tmp/socket\n", "URL is invalid"),
        ("REPOMESH_MODEL_API_KEY=must-not-be-here\n", "not allowed"),
    ],
)
def test_runtime_config_rejects_malformed_or_forbidden_input(
    tmp_path: Path,
    content: str,
    message: str,
) -> None:
    target = tmp_path / "runtime.env"
    target.write_text(content, encoding="utf-8")
    with pytest.raises(RuntimeConfigError, match=message):
        read_runtime_config(target)


def test_runtime_config_error_does_not_disclose_invalid_value(tmp_path: Path) -> None:
    sentinel = "secret-value-must-not-escape"
    target = tmp_path / "runtime.env"
    target.write_text(
        f"REPOMESH_AGENTTEAMS_CONTROLLER_URL={sentinel}\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeConfigError) as caught:
        read_runtime_config(target)
    assert sentinel not in str(caught.value)


def test_failed_atomic_replace_preserves_previous_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "runtime.env"
    write_runtime_config({"REPOMESH_AGENTTEAMS_REQUIRED": "false"}, target)

    def refuse_replace(source, destination) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", refuse_replace)
    with pytest.raises(OSError, match="simulated"):
        write_runtime_config({"REPOMESH_AGENTTEAMS_REQUIRED": "true"}, target)
    assert read_runtime_config(target)["REPOMESH_AGENTTEAMS_REQUIRED"] == "false"
    assert list(tmp_path.glob(".runtime.env.*.tmp")) == []
