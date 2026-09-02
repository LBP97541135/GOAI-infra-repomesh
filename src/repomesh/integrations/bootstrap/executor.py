import asyncio
import json
from collections.abc import Callable
from pathlib import Path

import httpx

from repomesh.modules.platform_config import (
    MODEL_API_KEY,
    MODEL_BASE_URL,
    MODEL_NAME,
    BootstrapErrorCode,
    BootstrapExecutionError,
    BootstrapOperation,
    BootstrapOperationStore,
    BootstrapPhase,
    BootstrapState,
    BootstrapUserInputRequired,
    PostgresPlatformCredentialStore,
    write_runtime_config,
)

from .command_runner import BootstrapCommandError, BootstrapCommandRunner
from .docker_target import (
    DockerCommandError,
    DockerComposeApiTargetSelector,
    DockerTargetSafetyError,
)
from .recovery import RetryPolicy


class ApiReadinessVerifier:
    def __init__(
        self,
        url: str = "http://api:8000/health/ready",
        *,
        attempts: int = 30,
        interval_seconds: float = 2,
    ) -> None:
        self._url = url
        self._attempts = attempts
        self._interval_seconds = interval_seconds

    async def wait(self) -> bool:
        async with httpx.AsyncClient(timeout=3) as client:
            for attempt in range(self._attempts):
                try:
                    response = await client.get(self._url)
                    if response.status_code == 200:
                        return True
                except httpx.HTTPError:
                    pass
                if attempt + 1 < self._attempts:
                    await asyncio.sleep(self._interval_seconds)
        return False


class AgentTeamsBootstrapExecutor:
    def __init__(
        self,
        credential_store: PostgresPlatformCredentialStore,
        operation_store: BootstrapOperationStore,
        command_runner: BootstrapCommandRunner,
        target_selector: DockerComposeApiTargetSelector,
        *,
        runtime_path: Path = Path("/app/.secrets/platform-runtime.env"),
        agentteams_env_path: Path = Path("/app/.secrets/agentteams-manager.env"),
        installer_path: Path = Path(
            "/app/components/agentteams/install/agentteams-install.sh"
        ),
        readiness_verifier: ApiReadinessVerifier | None = None,
        runtime_writer: Callable[[dict[str, str], Path], Path] = write_runtime_config,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._credentials = credential_store
        self._operations = operation_store
        self._commands = command_runner
        self._targets = target_selector
        self._runtime_path = runtime_path
        self._agentteams_env_path = agentteams_env_path
        self._installer_path = installer_path
        self._readiness = readiness_verifier or ApiReadinessVerifier()
        self._runtime_writer = runtime_writer
        self._retry = retry_policy or RetryPolicy()

    async def execute(self, operation: BootstrapOperation, lease_owner: str) -> None:
        model = await self._credentials.get_many({MODEL_API_KEY, MODEL_BASE_URL, MODEL_NAME})
        api_key = model.get(MODEL_API_KEY)
        if api_key is None or not api_key.value:
            raise BootstrapUserInputRequired(
                BootstrapErrorCode.MODEL_CREDENTIAL_MISSING,
                "model credentials are required before execution-plane setup",
            )
        base_url = (
            model[MODEL_BASE_URL].value
            if MODEL_BASE_URL in model and model[MODEL_BASE_URL].value
            else "https://api.deepseek.com/v1"
        )
        model_name = (
            model[MODEL_NAME].value
            if MODEL_NAME in model and model[MODEL_NAME].value
            else "deepseek-chat"
        )
        await self._require_docker()
        await self._advance(operation, lease_owner, BootstrapPhase.INSTALLING_AGENTTEAMS)
        if not await self._controller_healthy():
            await self._install_agentteams(api_key.value, base_url, model_name)

        await self._advance(operation, lease_owner, BootstrapPhase.VERIFYING_CONTROLLER)
        if not await self._controller_healthy(retry=True):
            raise BootstrapExecutionError(
                BootstrapErrorCode.CONTROLLER_UNHEALTHY,
                "AgentTeams Controller did not become healthy",
                retryable=True,
            )
        controller_token = await self._controller_token()

        installer_environment = self._read_agentteams_environment()
        await self._advance(operation, lease_owner, BootstrapPhase.CONFIGURING_MATRIX)
        matrix_token = await self._matrix_token(installer_environment)

        await self._advance(operation, lease_owner, BootstrapPhase.CONFIGURING_STORAGE)
        minio_user = installer_environment.get("AGENTTEAMS_MINIO_USER", "")
        minio_password = installer_environment.get("AGENTTEAMS_MINIO_PASSWORD", "")
        if not minio_user or not minio_password:
            raise BootstrapExecutionError(
                BootstrapErrorCode.STORAGE_CREDENTIALS_MISSING,
                "AgentTeams object-storage credentials are unavailable",
                retryable=True,
            )

        await self._advance(operation, lease_owner, BootstrapPhase.WRITING_RUNTIME_CONFIG)
        runtime_values = {
            "REPOMESH_AGENTTEAMS_REQUIRED": "true",
            "REPOMESH_AGENTTEAMS_CONTROLLER_URL": "http://agentteams-controller:8090",
            "REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN": controller_token,
            "REPOMESH_AGENTTEAMS_MATRIX_URL": "http://agentteams-controller:6167",
            "REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN": matrix_token,
            "REPOMESH_AGENTTEAMS_STORAGE_ENDPOINT": "http://agentteams-controller:9000",
            "REPOMESH_AGENTTEAMS_STORAGE_ACCESS_KEY": minio_user,
            "REPOMESH_AGENTTEAMS_STORAGE_SECRET_KEY": minio_password,
            "REPOMESH_AGENTTEAMS_STORAGE_BUCKET": "agentteams-storage",
        }
        try:
            self._runtime_writer(runtime_values, self._runtime_path)
        except Exception as error:
            raise BootstrapExecutionError(
                BootstrapErrorCode.RUNTIME_CONFIG_WRITE_FAILED,
                "RepoMesh runtime configuration could not be written",
                retryable=True,
            ) from error

        await self._advance(operation, lease_owner, BootstrapPhase.RESTARTING_API)
        try:
            target = await self._targets.select()
        except DockerTargetSafetyError as error:
            raise BootstrapExecutionError(
                BootstrapErrorCode.API_RESTART_FAILED,
                "RepoMesh API container selection violated a safety invariant",
                retryable=False,
            ) from error
        except DockerCommandError as error:
            raise BootstrapExecutionError(
                BootstrapErrorCode.API_RESTART_FAILED,
                "RepoMesh API container could not be selected safely",
                retryable=True,
            ) from error
        restarted = await self._retry.run(
            lambda: self._commands.run(("docker", "restart", target.container_id)),
            accept=lambda result: result.returncode == 0,
            retry_exceptions=(BootstrapCommandError,),
        )
        if restarted.returncode != 0:
            raise BootstrapExecutionError(
                BootstrapErrorCode.API_RESTART_FAILED,
                "RepoMesh API container could not be restarted",
                retryable=True,
            )

        await self._advance(operation, lease_owner, BootstrapPhase.VERIFYING_PLATFORM)
        if not await self._readiness.wait():
            raise BootstrapExecutionError(
                BootstrapErrorCode.PLATFORM_VERIFICATION_FAILED,
                "RepoMesh did not become ready after execution-plane configuration",
                retryable=True,
            )

    async def _advance(
        self,
        operation: BootstrapOperation,
        lease_owner: str,
        phase: BootstrapPhase,
    ) -> None:
        await self._operations.transition(
            operation.id,
            target=BootstrapState.RUNNING,
            phase=phase,
            lease_owner=lease_owner,
        )

    async def _require_docker(self) -> None:
        try:
            result = await self._retry.run(
                lambda: self._commands.run(("docker", "info"), capture_stdout=False),
                accept=lambda command: command.returncode == 0,
                retry_exceptions=(BootstrapCommandError,),
            )
        except BootstrapCommandError as error:
            raise BootstrapExecutionError(
                BootstrapErrorCode.DOCKER_UNAVAILABLE,
                "Docker is unavailable to the bootstrap service",
                retryable=True,
            ) from error
        if result.returncode != 0:
            raise BootstrapExecutionError(
                BootstrapErrorCode.DOCKER_UNAVAILABLE,
                "Docker is unavailable to the bootstrap service",
                retryable=True,
            )

    async def _controller_healthy(self, *, retry: bool = False) -> bool:
        async def probe():
            return await self._commands.run(
                (
                    "docker",
                    "exec",
                    "agentteams-controller",
                    "curl",
                    "-sf",
                    "http://127.0.0.1:8090/healthz",
                ),
                capture_stdout=False,
            )

        try:
            result = (
                await self._retry.run(
                    probe,
                    accept=lambda command: command.returncode == 0,
                    retry_exceptions=(BootstrapCommandError,),
                )
                if retry
                else await probe()
            )
            return result.returncode == 0
        except BootstrapCommandError:
            return False

    async def _install_agentteams(
        self,
        api_key: str,
        base_url: str,
        model_name: str,
    ) -> None:
        environment = {
            "AGENTTEAMS_NON_INTERACTIVE": "1",
            "AGENTTEAMS_LLM_PROVIDER": "openai-compat",
            "AGENTTEAMS_LLM_API_KEY": api_key,
            "AGENTTEAMS_OPENAI_BASE_URL": base_url,
            "AGENTTEAMS_DEFAULT_MODEL": model_name,
            "AGENTTEAMS_ENV_FILE": str(self._agentteams_env_path),
            "AGENTTEAMS_WORKSPACE_VOLUME": "agentteams-manager-workspace",
            "AGENTTEAMS_HOST_SHARE_VOLUME": "agentteams-host-share",
        }
        try:
            result = await self._commands.run(
                ("bash", str(self._installer_path)),
                environment=environment,
                timeout_seconds=1800,
                capture_stdout=False,
            )
        except BootstrapCommandError as error:
            raise BootstrapExecutionError(
                BootstrapErrorCode.AGENTTEAMS_INSTALL_FAILED,
                "AgentTeams installation did not complete",
                retryable=True,
            ) from error
        if result.returncode != 0:
            raise BootstrapExecutionError(
                BootstrapErrorCode.AGENTTEAMS_INSTALL_FAILED,
                "AgentTeams installation did not complete",
                retryable=True,
            )

    async def _controller_token(self) -> str:
        result = await self._commands.run(
            (
                "docker",
                "exec",
                "agentteams-controller",
                "cat",
                "/var/run/agentteams/cli-token",
            )
        )
        token = result.stdout.decode("utf-8", errors="strict").strip()
        if result.returncode != 0 or not token:
            raise BootstrapExecutionError(
                BootstrapErrorCode.CONTROLLER_UNHEALTHY,
                "AgentTeams Controller credentials are unavailable",
                retryable=True,
            )
        return token

    async def _matrix_token(self, environment: dict[str, str]) -> str:
        username = environment.get("AGENTTEAMS_ADMIN_USER", "")
        password = environment.get("AGENTTEAMS_ADMIN_PASSWORD", "")
        if not username or not password:
            raise BootstrapExecutionError(
                BootstrapErrorCode.MATRIX_LOGIN_FAILED,
                "AgentTeams Matrix administrator credentials are unavailable",
                retryable=True,
            )
        payload = json.dumps(
            {
                "type": "m.login.password",
                "identifier": {"type": "m.id.user", "user": username},
                "password": password,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        arguments = (
            "docker",
            "exec",
            "-i",
            "agentteams-controller",
            "curl",
            "-sf",
            "-X",
            "POST",
            "http://127.0.0.1:6167/_matrix/client/v3/login",
            "-H",
            "Content-Type: application/json",
            "--data-binary",
            "@-",
        )
        result = await self._retry.run(
            lambda: self._commands.run(arguments, stdin=payload),
            accept=lambda command: command.returncode == 0,
            retry_exceptions=(BootstrapCommandError,),
        )
        if result.returncode != 0:
            raise BootstrapExecutionError(
                BootstrapErrorCode.MATRIX_LOGIN_FAILED,
                "AgentTeams Matrix login failed",
                retryable=True,
            )
        try:
            response = json.loads(result.stdout)
            token = response.get("access_token", "")
        except (json.JSONDecodeError, AttributeError, UnicodeDecodeError) as error:
            raise BootstrapExecutionError(
                BootstrapErrorCode.MATRIX_LOGIN_FAILED,
                "AgentTeams Matrix login returned an invalid response",
                retryable=True,
            ) from error
        if not isinstance(token, str) or not token:
            raise BootstrapExecutionError(
                BootstrapErrorCode.MATRIX_LOGIN_FAILED,
                "AgentTeams Matrix login returned no access token",
                retryable=True,
            )
        return token

    def _read_agentteams_environment(self) -> dict[str, str]:
        try:
            lines = self._agentteams_env_path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            raise BootstrapExecutionError(
                BootstrapErrorCode.MATRIX_LOGIN_FAILED,
                "AgentTeams installer environment is unavailable",
                retryable=True,
            ) from error
        allowed = {
            "AGENTTEAMS_ADMIN_USER",
            "AGENTTEAMS_ADMIN_PASSWORD",
            "AGENTTEAMS_MINIO_USER",
            "AGENTTEAMS_MINIO_PASSWORD",
        }
        values: dict[str, str] = {}
        for line in lines:
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key in allowed:
                values[key] = value
        return values
