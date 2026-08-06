from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl

MockScenarioName = Literal[
    "success",
    "test_failed",
    "failed",
    "timeout",
    "cancelled",
    "interrupted",
    "question_required",
]


class CodingRunCreate(BaseModel):
    task_id: UUID
    repository_url: HttpUrl
    instruction: str = Field(min_length=1, max_length=20_000)
    base_revision: str = Field(default="main", min_length=1, max_length=200)
    scenario: MockScenarioName = "success"


class RunEventView(BaseModel):
    type: str
    message: str


class CodingRunView(BaseModel):
    run_id: UUID
    status: str
    adapter: str
    summary: str
    changed_files: tuple[str, ...]
    test_command: str | None
    events: tuple[RunEventView, ...]


class WorkerTaskStartCreate(BaseModel):
    task_id: UUID
    worker_agent_id: UUID
    adapter_id: str = Field(min_length=1, max_length=100)
    base_revision: str = Field(default="main", min_length=1, max_length=200)
    task_features: frozenset[str] = frozenset()


class WorkerTaskStartView(BaseModel):
    task_id: UUID
    run_id: UUID
    status: str
    workspace_id: str
    workspace_path: str
    base_sha: str
