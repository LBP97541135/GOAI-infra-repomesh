import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from repomesh.integrations.bootstrap import AgentTeamsBootstrapExecutor
from repomesh.integrations.bootstrap.command_runner import BootstrapCommandResult
from repomesh.integrations.bootstrap.docker_target import DockerComposeTarget
from repomesh.modules.platform_config import (
    MODEL_API_KEY,
    MODEL_BASE_URL,
    MODEL_NAME,
    BootstrapKind,
    BootstrapOperation,
    BootstrapPhase,
    BootstrapState,
    BootstrapUserInputRequired,
    StoredCredential,
)

MODEL_KEY = "model-secret-sentinel"
MATRIX_PASSWORD = "matrix-password-sentinel"
CONTROLLER_TOKEN = "controller-token-sentinel"
MATRIX_TOKEN = "matrix-token-sentinel"
MINIO_SECRET = "minio-secret-sentinel"


def operation() -> BootstrapOperation:
    now = datetime.now(UTC)
    return BootstrapOperation(
        id=uuid4(),
        kind=BootstrapKind.CONFIGURE_EXECUTION_PLANE,
        state=BootstrapState.RUNNING,
        phase=BootstrapPhase.INSTALLING_AGENTTEAMS,
        attempt=1,
        requested_by=None,
        lease_owner="executor-test",
        lease_expires_at=None,
        error_code=None,
        error_detail=None,
        requested_at=now,
        started_at=now,
        updated_at=now,
        finished_at=None,
    )


class FakeCredentialStore:
    def __init__(self, *, with_key: bool = True) -> None:
        now = datetime.now(UTC)
        self.values = (
            {
                MODEL_API_KEY: StoredCredential(MODEL_API_KEY, MODEL_KEY, now, None),
                MODEL_BASE_URL: StoredCredential(
                    MODEL_BASE_URL, "https://model.example.test/v1", now, None
                ),
                MODEL_NAME: StoredCredential(MODEL_NAME, "test-model", now, None),
            }
            if with_key
            else {}
        )

    async def get_many(self, keys):
        return {key: value for key, value in self.values.items() if key in keys}


class FakeOperationStore:
    def __init__(self) -> None:
        self.phases: list[BootstrapPhase] = []

    async def transition(self, operation_id, *, target, phase, lease_owner, **kwargs):
        assert target is BootstrapState.RUNNING
        assert lease_owner == "executor-test"
        self.phases.append(phase)
        return None


@dataclass
class CommandCall:
    arguments: tuple[str, ...]
    stdin: bytes | None
    environment: dict[str, str]
    capture_stdout: bool


class FakeCommandRunner:
    def __init__(self, *, controller_initially_healthy: bool) -> None:
        self.controller_healthy = controller_initially_healthy
        self.calls: list[CommandCall] = []

    async def run(
        self,
        arguments,
        *,
        stdin=None,
        environment=None,
        timeout_seconds=30,
        capture_stdout=True,
    ):
        self.calls.append(
            CommandCall(arguments, stdin, dict(environment or {}), capture_stdout)
        )
        if arguments == ("docker", "info"):
            return BootstrapCommandResult(0, b"")
        if arguments[:3] == ("docker", "exec", "agentteams-controller") and arguments[-1].endswith(
            "/healthz"
        ):
            return BootstrapCommandResult(0 if self.controller_healthy else 1, b"")
        if arguments[0] == "bash":
            self.controller_healthy = True
            return BootstrapCommandResult(0, b"")
        if arguments[-1] == "/var/run/agentteams/cli-token":
            return BootstrapCommandResult(0, CONTROLLER_TOKEN.encode())
        if any("_matrix/client/v3/login" in argument for argument in arguments):
            return BootstrapCommandResult(
                0,
                f'{{"access_token":"{MATRIX_TOKEN}"}}'.encode(),
            )
        if arguments[:2] == ("docker", "restart"):
            return BootstrapCommandResult(0, b"api-container")
        raise AssertionError(f"unexpected command: {arguments}")


class FakeTargetSelector:
    async def select(self):
        return DockerComposeTarget("a" * 64, "repomesh-project")


class FakeReadinessVerifier:
    async def wait(self) -> bool:
        return True


def write_agentteams_env(path: Path) -> None:
    path.write_text(
        "AGENTTEAMS_ADMIN_USER=admin\n"
        f"AGENTTEAMS_ADMIN_PASSWORD={MATRIX_PASSWORD}\n"
        "AGENTTEAMS_MINIO_USER=minio-user\n"
        f"AGENTTEAMS_MINIO_PASSWORD={MINIO_SECRET}\n",
        encoding="utf-8",
    )


def make_executor(tmp_path: Path, commands: FakeCommandRunner, *, with_key: bool = True):
    operation_store = FakeOperationStore()
    runtime_values = {}
    agentteams_env = tmp_path / "agentteams.env"
    write_agentteams_env(agentteams_env)

    def runtime_writer(values, path):
        runtime_values.update(values)
        return path

    executor = AgentTeamsBootstrapExecutor(
        FakeCredentialStore(with_key=with_key),
        operation_store,
        commands,
        FakeTargetSelector(),
        runtime_path=tmp_path / "runtime.env",
        agentteams_env_path=agentteams_env,
        installer_path=tmp_path / "install.sh",
        readiness_verifier=FakeReadinessVerifier(),
        runtime_writer=runtime_writer,
    )
    return executor, operation_store, runtime_values


@pytest.mark.asyncio
@pytest.mark.parametrize("controller_healthy", [True, False])
async def test_executor_completes_skip_and_install_paths_without_secret_argv(
    tmp_path: Path,
    caplog,
    controller_healthy: bool,
) -> None:
    commands = FakeCommandRunner(controller_initially_healthy=controller_healthy)
    executor, store, runtime_values = make_executor(tmp_path, commands)
    with caplog.at_level(logging.INFO):
        await executor.execute(operation(), "executor-test")

    installer_calls = [call for call in commands.calls if call.arguments[0] == "bash"]
    assert len(installer_calls) == (0 if controller_healthy else 1)
    if installer_calls:
        assert installer_calls[0].environment["AGENTTEAMS_LLM_API_KEY"] == MODEL_KEY
        assert installer_calls[0].capture_stdout is False

    all_arguments = " ".join(argument for call in commands.calls for argument in call.arguments)
    assert MODEL_KEY not in all_arguments
    assert MATRIX_PASSWORD not in all_arguments
    matrix_call = next(call for call in commands.calls if call.stdin is not None)
    assert MATRIX_PASSWORD.encode() in matrix_call.stdin
    assert runtime_values["REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN"] == CONTROLLER_TOKEN
    assert runtime_values["REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN"] == MATRIX_TOKEN
    assert runtime_values["REPOMESH_AGENTTEAMS_STORAGE_SECRET_KEY"] == MINIO_SECRET
    assert MODEL_KEY not in runtime_values.values()
    assert store.phases == [
        BootstrapPhase.INSTALLING_AGENTTEAMS,
        BootstrapPhase.VERIFYING_CONTROLLER,
        BootstrapPhase.CONFIGURING_MATRIX,
        BootstrapPhase.CONFIGURING_STORAGE,
        BootstrapPhase.WRITING_RUNTIME_CONFIG,
        BootstrapPhase.RESTARTING_API,
        BootstrapPhase.VERIFYING_PLATFORM,
    ]
    for sentinel in (MODEL_KEY, MATRIX_PASSWORD, CONTROLLER_TOKEN, MATRIX_TOKEN, MINIO_SECRET):
        assert sentinel not in caplog.text


@pytest.mark.asyncio
async def test_executor_waits_for_user_when_model_key_is_missing(tmp_path: Path) -> None:
    executor, store, _ = make_executor(
        tmp_path,
        FakeCommandRunner(controller_initially_healthy=True),
        with_key=False,
    )
    with pytest.raises(BootstrapUserInputRequired):
        await executor.execute(operation(), "executor-test")
    assert store.phases == []
