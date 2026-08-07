from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="REPOMESH_", extra="ignore")

    app_name: str = "RepoMesh"
    environment: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://repomesh:repomesh@localhost:5432/repomesh"
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
    direct_worker_mcp_enabled: bool = False
    runner_workspace_root: Path = Path(".repomesh-workspaces")
    capability_root: Path = Path(".")
    github_webhook_secret: str | None = None
    github_app_id: int | None = None
    github_app_private_key_file: Path | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
