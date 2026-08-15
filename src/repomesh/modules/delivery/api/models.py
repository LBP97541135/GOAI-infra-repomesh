from uuid import UUID

from pydantic import BaseModel, Field

from repomesh.modules.delivery.contracts import (
    GovernanceDecisionKind,
    RecoveryActionStatus,
    RecoveryTrigger,
    ReviewState,
)


class RepositoryCandidateCreate(BaseModel):
    repository_id: UUID
    task_id: UUID
    commit_sha: str
    base_sha: str
    branch_name: str
    depends_on: list[UUID] = Field(default_factory=list)
    required_checks: list[str] = Field(default_factory=list)
    required_approvals: int = Field(default=1, ge=0)


class ChangeSetCreate(BaseModel):
    organization_id: UUID
    project_id: UUID
    created_by_agent_id: UUID
    title: str
    validation_snapshot_id: UUID | None = None
    candidates: list[RepositoryCandidateCreate]
    idempotency_key: str


class DeliveryPolicyUpdate(BaseModel):
    auto_merge: bool = False
    base_branch: str = Field(default="main", min_length=1, max_length=255)
    required_checks: list[str] = Field(default_factory=list)
    required_approvals: int = Field(default=1, ge=0)
    contract_gate: bool = False
    add_label: bool = False


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


class ReviewObservationCreate(BaseModel):
    repository_id: UUID
    review_id: str
    reviewer: str
    state: ReviewState
    head_sha: str
    summary: str = ""


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


class ChangeSetRollbackCreate(BaseModel):
    """Whole-ChangeSet rollback request (GUI batch E-1, contract v0.1 §4.6).

    No ``repository_id``: rollback granularity is the whole change set by GUI
    ruling #4, and offering the field would imply a choice that does not exist.
    No ``head_sha`` either — the decision covers every candidate at whatever
    head it currently carries, and the server reads those heads itself rather
    than trusting a browser to enumerate them.
    """

    change_set_id: UUID
    reason: str
    requested_by_agent_id: UUID
    idempotency_key: str


class GovernanceDecisionCreate(BaseModel):
    change_set_id: UUID
    repository_id: UUID
    head_sha: str
    decision: GovernanceDecisionKind
    reason: str
    idempotency_key: str
    decided_by_agent_id: UUID
