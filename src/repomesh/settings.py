from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from repomesh.modules.agent_runtime.ports.agent_team import ManagerRuntime, WorkerRuntime


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="REPOMESH_", extra="ignore")

    app_name: str = "RepoMesh"
    environment: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "INFO"
    supervised: bool = False
    database_url: str = "postgresql+asyncpg://repomesh:repomesh@localhost:5432/repomesh"
    local_session_ttl_seconds: int = Field(default=28800, ge=300, le=604800)
    agentteams_required: bool = False
    agentteams_controller_url: str = "http://localhost:8090"
    agentteams_controller_token: str | None = None
    agentteams_matrix_url: str = "http://localhost:6167"
    agentteams_matrix_access_token: str | None = None
    #: Which runtime the projected Manager/Worker resources ask the controller
    #: for. Not a free choice: the controller pairs each runtime with its own
    #: image env (``AGENTTEAMS_COPAW_WORKER_IMAGE`` vs ``AGENTTEAMS_WORKER_IMAGE``
    #: for openclaw), so asking for a runtime whose image that controller has
    #: not been given spawns containers that exit(1) on boot, never obtain a
    #: Matrix identity, and fail every dispatch — the root cause under defect
    #: A-6. Hardcoded ``openclaw`` on both projection paths before this;
    #: ``copaw`` is the pairing this deployment actually has. Typed as the wire
    #: enums so an unknown value fails at startup rather than at first
    #: dispatch, and so the allowed set is not copied here.
    agentteams_manager_runtime: ManagerRuntime = ManagerRuntime.COPAW
    agentteams_worker_runtime: WorkerRuntime = WorkerRuntime.COPAW
    runner_control_token: str | None = None
    agent_action_token: str | None = None
    #: Hosts POST /repositories/scan-org may reach, comma separated. Anything
    #: else is refused before a request leaves this process.
    repository_scan_allowed_hosts: str = "github.com"
    #: Credentials the *console* scan endpoints use to read private
    #: repositories. The console's request bodies carry no token field on
    #: purpose (a browser is not a place to type a PAT), so this env is the
    #: only way to reach a private repo from the GUI. The native RI endpoints
    #: still accept a token in the body for scripts and operators.
    repository_scan_github_token: str = ""
    repository_scan_gitlab_token: str = ""
    #: AgentTeams probe budget per row, and how many may be in flight at once.
    runtime_probe_timeout_seconds: float = 2.0
    runtime_probe_concurrency: int = 16
    worker_task_control_url: str | None = None
    agentteams_storage_root: Path = Path(".agentteams-storage")
    agentteams_storage_endpoint: str | None = None
    agentteams_storage_access_key: str | None = None
    agentteams_storage_secret_key: str | None = None
    agentteams_storage_bucket: str = "agentteams-storage"
    mcp_gateway_token: str | None = None
    mcp_gateway_tokens: tuple[str, ...] = ()
    direct_worker_mcp_enabled: bool = False
    runner_workspace_root: Path = Path(".repomesh-workspaces")
    worker_execution_reservation_lease_seconds: int = Field(default=300, ge=30)
    worker_execution_reservation_wait_seconds: int = Field(default=30, ge=1)
    capability_root: Path = Path(".")
    # OTLP/HTTP collector base URL (e.g. a local AgentScope Studio). None keeps
    # tracing off; see docs/development/observability-instrumentation-plan-20260807.md.
    otlp_endpoint: str | None = None
    otlp_service_name: str = "repomesh-api"
    # The product exposes one default model connection. Legacy planning-specific
    # names remain aliases so existing deployments can override it independently.
    deepseek_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "REPOMESH_DEEPSEEK_API_KEY",
            "REPOMESH_MODEL_API_KEY",
            "DEEPSEEK_API_KEY",
        ),
    )
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com/v1",
        validation_alias=AliasChoices(
            "REPOMESH_DEEPSEEK_BASE_URL",
            "REPOMESH_MODEL_BASE_URL",
        ),
    )
    deepseek_model: str = Field(
        default="deepseek-chat",
        validation_alias=AliasChoices(
            "REPOMESH_DEEPSEEK_MODEL",
            "REPOMESH_MODEL",
        ),
    )
    github_app_id: int | None = None
    github_app_private_key_file: Path | None = None
    github_app_private_key_base64: SecretStr | None = None
    github_webhook_secret: str | None = None
    delivery_auto_enabled: bool = False
    # Local-dev alternative to the GitHub App pair above: one personal
    # token for every repository (StaticTokenProvider). The App path
    # wins when both are configured.
    delivery_github_token: str = ""
    delivery_base_branch: str = "main"
    delivery_required_checks: tuple[str, ...] = ()
    delivery_required_approvals: int = Field(default=1, ge=0)
    delivery_contract_gate: bool = False
    delivery_pr_label: bool = False
    delivery_reconcile_interval_seconds: int = Field(default=60, ge=5)
    delivery_recovery_interval_seconds: int = Field(default=30, ge=5)
    replan_auto_commit: bool = True
    """Default replan mode when the request says ``auto`` (PR-4).

    ``True`` preserves the pre-PR-4 behaviour — a replan request executes the
    full commit immediately. Set ``REPOMESH_REPLAN_AUTO_COMMIT=false`` to
    require an explicit approval round-trip: ``auto`` requests then run in
    ``preview`` mode (zero side effects) and a second call with
    ``mode=commit`` applies the change.
    """
    scm_observation_replay_interval_seconds: int = Field(default=15, ge=5)
    scm_poll_interval_seconds: int = Field(default=60, ge=5)
    scm_poll_scan_interval_seconds: int = Field(default=15, ge=5)
    scm_command_dispatch_interval_seconds: int = Field(default=5, ge=1)
    scm_command_lease_seconds: int = Field(default=300, ge=10)
    scm_command_lease_renew_interval_seconds: int = Field(default=60, ge=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
