"""The three environment classes of the worker runtime contract."""

from pathlib import Path

import pytest

from repomesh_runner.runtime_env import (
    REJECTED_VARIABLES,
    RuntimeEnvError,
    load_runtime_env,
)


def base_environ(workspace: Path) -> dict[str, str]:
    return {
        "REPOMESH_RUNNER_TASK_SOURCE_URL": "https://control.example/tasks/next",
        "REPOMESH_RUNNER_EVENT_SINK_URL": "https://control.example/events",
        "REPOMESH_RUNNER_WORKSPACE_ROOT": str(workspace),
    }


def test_consumed_variables_are_resolved(tmp_path: Path) -> None:
    env = load_runtime_env(base_environ(tmp_path))

    assert env.task_source_url == "https://control.example/tasks/next"
    assert env.event_sink_url == "https://control.example/events"
    assert env.workspace_root == tmp_path


def test_state_dir_defaults_below_the_workspace_root(tmp_path: Path) -> None:
    env = load_runtime_env(base_environ(tmp_path))

    assert env.state_dir == tmp_path / ".runner-state"


def test_state_dir_can_be_overridden(tmp_path: Path) -> None:
    environ = base_environ(tmp_path) | {"REPOMESH_RUNNER_STATE_DIR": str(tmp_path / "elsewhere")}

    assert load_runtime_env(environ).state_dir == tmp_path / "elsewhere"


def test_poll_timeout_defaults_to_thirty_seconds(tmp_path: Path) -> None:
    assert load_runtime_env(base_environ(tmp_path)).poll_timeout_seconds == 30.0


def test_poll_timeout_is_read_as_a_float(tmp_path: Path) -> None:
    environ = base_environ(tmp_path) | {"REPOMESH_RUNNER_POLL_TIMEOUT_SECONDS": "2.5"}

    assert load_runtime_env(environ).poll_timeout_seconds == 2.5


def test_control_token_is_consumed(tmp_path: Path) -> None:
    environ = base_environ(tmp_path) | {"REPOMESH_RUNNER_CONTROL_TOKEN": "runner-secret"}

    assert load_runtime_env(environ).control_token == "runner-secret"


@pytest.mark.parametrize("value", ["soon", "0", "-1"])
def test_unusable_poll_timeout_is_refused(tmp_path: Path, value: str) -> None:
    environ = base_environ(tmp_path) | {"REPOMESH_RUNNER_POLL_TIMEOUT_SECONDS": value}

    with pytest.raises(RuntimeEnvError, match="REPOMESH_RUNNER_POLL_TIMEOUT_SECONDS"):
        load_runtime_env(environ)


@pytest.mark.parametrize(
    "name",
    [
        "REPOMESH_RUNNER_TASK_SOURCE_URL",
        "REPOMESH_RUNNER_EVENT_SINK_URL",
        "REPOMESH_RUNNER_WORKSPACE_ROOT",
    ],
)
def test_missing_required_variable_is_refused(tmp_path: Path, name: str) -> None:
    environ = base_environ(tmp_path)
    del environ[name]

    with pytest.raises(RuntimeEnvError, match=f"{name} is required"):
        load_runtime_env(environ)


def test_workspace_root_must_exist(tmp_path: Path) -> None:
    environ = base_environ(tmp_path) | {"REPOMESH_RUNNER_WORKSPACE_ROOT": str(tmp_path / "missing")}

    with pytest.raises(RuntimeEnvError, match="existing directory"):
        load_runtime_env(environ)


def test_labels_are_passed_through(tmp_path: Path) -> None:
    environ = base_environ(tmp_path) | {
        "REPOMESH_LABEL_TEAM_ID": "team-7",
        "REPOMESH_LABEL_ROLE": "coder",
    }

    labels = load_runtime_env(environ).labels

    assert dict(labels) == {"team-id": "team-7", "role": "coder"}


def test_label_prefix_alone_is_not_a_label(tmp_path: Path) -> None:
    environ = base_environ(tmp_path) | {"REPOMESH_LABEL_": "nameless"}

    assert dict(load_runtime_env(environ).labels) == {}


def test_ignored_variables_do_not_leak_into_the_runtime_env(tmp_path: Path) -> None:
    environ = base_environ(tmp_path) | {
        "MATRIX_HOMESERVER": "https://matrix.example",
        "MATRIX_ACCESS_TOKEN": "secret",
        "OPENCLAW_CONFIG_DIR": "/etc/openclaw",
        "PATH": "/usr/bin",
    }

    env = load_runtime_env(environ)

    assert dict(env.labels) == {}
    assert env.task_source_url == "https://control.example/tasks/next"


def test_permission_bearing_variable_stops_startup(tmp_path: Path) -> None:
    environ = base_environ(tmp_path) | {"AGENTTEAMS_YOLO": "1"}

    with pytest.raises(RuntimeEnvError, match="AGENTTEAMS_YOLO"):
        load_runtime_env(environ)


@pytest.mark.parametrize("name", sorted(REJECTED_VARIABLES))
def test_every_rejected_variable_stops_startup(tmp_path: Path, name: str) -> None:
    environ = base_environ(tmp_path) | {name: "whatever"}

    with pytest.raises(RuntimeEnvError, match="permission-bearing"):
        load_runtime_env(environ)


def test_rejection_wins_over_a_missing_required_variable() -> None:
    with pytest.raises(RuntimeEnvError, match="AGENTTEAMS_YOLO"):
        load_runtime_env({"AGENTTEAMS_YOLO": "1"})


def test_runtime_env_is_frozen(tmp_path: Path) -> None:
    env = load_runtime_env(base_environ(tmp_path))

    with pytest.raises(AttributeError):
        env.task_source_url = "https://elsewhere.example"  # type: ignore[misc]


def test_load_runtime_env_reads_only_its_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The function is pure in its mapping argument: os.environ is the caller's business."""

    monkeypatch.setenv("REPOMESH_LABEL_SMUGGLED", "yes")
    monkeypatch.setenv("AGENTTEAMS_YOLO", "1")

    env = load_runtime_env(base_environ(tmp_path))

    assert dict(env.labels) == {}
