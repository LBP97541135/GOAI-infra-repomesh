from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="REPOMESH_", extra="ignore")

    app_name: str = "RepoMesh"
    environment: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://repomesh:repomesh@localhost:5432/repomesh"
    local_session_ttl_seconds: int = Field(default=28800, ge=300, le=604800)
    agentteams_required: bool = False
    agentteams_controller_url: str = "http://localhost:8090"
    agentteams_controller_token: str | None = None
    agentteams_matrix_url: str = "http://localhost:6167"
    agentteams_matrix_access_token: str | None = None
    runner_control_token: str | None = None
    agent_action_token: str | None = None
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
    delivery_base_branch: str = "main"
    delivery_required_checks: tuple[str, ...] = ()
    delivery_required_approvals: int = Field(default=1, ge=0)
    delivery_contract_gate: bool = False
    delivery_pr_label: bool = False
    delivery_reconcile_interval_seconds: int = Field(default=60, ge=5)
    scm_observation_replay_interval_seconds: int = Field(default=15, ge=5)
    scm_poll_interval_seconds: int = Field(default=60, ge=5)
    scm_poll_scan_interval_seconds: int = Field(default=15, ge=5)
    scm_command_dispatch_interval_seconds: int = Field(default=5, ge=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
