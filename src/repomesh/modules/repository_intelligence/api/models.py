from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class AutoCardCreate(BaseModel):
    """AutoCard payload accepted on repository registration."""

    top_dirs: list[str] = Field(default_factory=list)
    deps: list[str] = Field(default_factory=list)
    recent_commits: list[str] = Field(default_factory=list)
    exposed_apis: list[str] = Field(default_factory=list)
    low_signal: bool = False


class AutoCardView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    top_dirs: tuple[str, ...] = ()
    deps: tuple[str, ...] = ()
    recent_commits: tuple[str, ...] = ()
    exposed_apis: tuple[str, ...] = ()
    low_signal: bool = False


class RepositoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    url: HttpUrl
    description: str = Field(default="", max_length=4000)
    topics: list[str] = Field(default_factory