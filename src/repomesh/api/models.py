from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class RepositoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    url: HttpUrl
    description: str = Field(min_length=1, max_length=4000)
    topics: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)


class RepositoryView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    url: str
    description: str
    topics: tuple[str, ...]
    languages: tuple[str, ...]


class DiscoveryRequest(BaseModel):
    requirement: str = Field(min_length=3, max_length=20_000)
    limit: int = Field(default=5, ge=1, le=50)


class DiscoveryCandidate(BaseModel):
    repository_id: UUID
    repository_name: str
    score: float
    matched_terms: tuple[str, ...]
    rationale: str


class CodingRunCreate(BaseModel):
    task_id: UUID
    repository_url: HttpUrl
    instruction: str = Field(min_length=1, max_length=20_000)
    base_revision: str = Field(default="main", min_length=1, max_length=200)


class CodingRunView(BaseModel):
    run_id: UUID
    status: str
    adapter: str
    summary: str
    changed_files: tuple[str, ...]
    test_command: str | None

