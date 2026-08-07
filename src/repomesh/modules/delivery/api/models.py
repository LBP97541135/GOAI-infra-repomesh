from uuid import UUID

from pydantic import BaseModel, Field

from repomesh.modules.delivery.contracts import RecoveryActionStatus, RecoveryTrigger


class RepositoryCandidateCreate(BaseModel):
    repository_id: UUID
    task_id: UUID
    commit_sha: str
    base_sha: str
    branch_name: str
    depends_on: list[UUID] = Field(default_factory=list)
    required_checks: list[str] = Field(default_factory=list)


class ChangeSetCreate(BaseModel):
    organization_id: UUID
    project_id: UUID
    created_by_agent_id: UUID
    title: str
    validation_snapshot_id: UUID
    candidates: list[RepositoryCandidateCreate]
    idempotency_key: str


class PullRequestObservationCreate(BaseModel):
    repository_id: UUID
    pull_request_number: int
    pull_request_url: str
    head_sha: str


class CIObservationCreate(BaseModel):
    repository_id: UUID
    passed: bool
    check_run_id: str
    summary: str
    check_name: str = ""


class MergeObservationCreate(BaseModel):
    repository_id: UUID
    merge_sha: str


class RecoveryPlanCreate(BaseModel):
    trigger: RecoveryTrigger
    reason: str
    repository_id: UUID | None = None
    run_id: UUID | None = None
    native_session_id: str | None = None


class RecoveryActionUpdate(BaseModel):
    status: RecoveryActionStatus
    detail: str
