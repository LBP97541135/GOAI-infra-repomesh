from functools import lru_cache
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
