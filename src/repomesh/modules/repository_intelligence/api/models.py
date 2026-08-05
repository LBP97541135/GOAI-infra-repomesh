from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class AutoCardCreate(BaseModel):
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
    topics: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    auto_card: AutoCardCreate | None = None


class RepositoryView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    url: str
    description: str
    topics: tuple[str, ...]
    languages: tuple[str, ...]
    auto_card: AutoCardView | None = None


class DiscoveryRequest(BaseModel):
    requirement: str = Field(min_length=3, max_length=20_000)
    limit: int = Field(default=5, ge=1, le=50)
    entry_point: str | None = None


class DiscoveryCandidate(BaseModel):
    repository_id: UUID
    repository_name: str
    score: float
    matched_terms: tuple[str, ...]
    rationale: str
    is_entry_point: bool = False
