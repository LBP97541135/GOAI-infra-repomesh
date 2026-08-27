from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

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


class ExternalWorkerProvisionRequest(BaseModel):
    """A body with no fields, and that is the point.

    The whole request is the path id: which principal becomes external. Every
    other fact about the resulting worker is owned by somebody who is not the
    caller — the resource name by the agent directory, the runtime, model and
    skills by the projection the ordinary project path already uses, and
    ``containerManaged`` by the controller's own answer — so there is nothing
    here to fill in.

    ``extra="forbid"`` makes a body that states one of those a 422 rather than
    a silent drop. Ignoring it would be worse than refusing: an operator who
    wrote ``{"containerManaged": true}`` and read 200 would believe they had
    asked for something, and what they would have got is the opposite.

    Declared optional at the route, so both an absent body and ``{}`` are
    accepted; there is no difference between them to preserve.
    """

    model_config = ConfigDict(extra="forbid")


class WorkerTaskStartView(BaseModel):
    task_id: UUID
    run_id: UUID
    status: str
    workspace_id: str
    workspace_path: str
    base_sha: str
