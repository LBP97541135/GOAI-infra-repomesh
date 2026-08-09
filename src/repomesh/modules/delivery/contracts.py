from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class ChangeSetStatus(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    DELIVERING = "delivering"
    BLOCKED = "blocked"
    DELIVERED = "delivered"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"
    MANUAL_INTERVENTION = "manual_intervention"


class RepositoryDeliveryStatus(StrEnum):
    PENDING = "pending"
    PR_OPEN = "pr_open"
    CI_PENDING = "ci_pending"
    CI_FAILED = "ci_failed"
    REVIEW_PENDING = "review_pending"
    REVIEW_CHANGES_REQUESTED = "review_changes_requested"
    READY_TO_MERGE = "ready_to_merge"
    MERGED = "merged"
    COMPENSATION_PENDING = "compensation_pending"
    COMPENSATED = "compensated"
    MANUAL_INTERVENTION = "manual_intervention"


class RecoveryTrigger(StrEnum):
    RUNNER_FAILED = "runner_failed"
    RUNNER_INTERRUPTED = "runner_interrupted"
    CI_FAILED = "ci_failed"
    PR_CONFLICT = "pr_conflict"
    PARTIAL_MERGE = "partial_merge"
    OPERATOR_REQUESTED = "operator_requested"


class RecoveryActionKind(StrEnum):
    RESUME_RUNNER_SESSION = "resume_runner_session"
    RETRY_RUNNER = "retry_runner"
    CLOSE_PULL_REQUEST = "close_pull_request"
    CREATE_REVERT_PULL_REQUEST = "create_revert_pull_request"
    MERGE_REVERT_PULL_REQUEST = "merge_revert_pull_request"
    REVALIDATE_CHANGESET = "revalidate_changeset"
    MANUAL_INTERVENTION = "manual_intervention"


class RecoveryActionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class ReviewState(StrEnum):
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    DISMISSED = "dismissed"


class SCMObservationSource(StrEnum):
    WEBHOOK = "webhook"
    POLLER = "poller"
    RECONCILIATION = "reconciliation"


class SCMObservationStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RecordSCMObservationCommand:
    provider: str
    source: SCMObservationSource
    external_id: str
    event_type: str
    payload: dict[str, object]
    payload_hash: str
    observed_at: datetime
    change_set_id: UUID | None = None
    repository_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class SCMObservationView:
    id: UUID
    provider: str
    source: SCMObservationSource
    external_id: str
    event_type: str
    payload: dict[str, object]
    payload_hash: str
    status: SCMObservationStatus
    change_set_id: UUID | None
    repository_id: UUID | None
    attempts: int
    version: int
    last_error: str | None
    observed_at: datetime
    received_at: datetime
    claimed_at: datetime | None
    processed_at: datetime | None


@dataclass(frozen=True, slots=True)
class RecordedSCMObservation:
    observation: SCMObservationView
    created: bool


@dataclass(frozen=True, slots=True)
class RepositoryCandidateInput:
    repository_id: UUID
    task_id: UUID
    commit_sha: str
    base_sha: str
    branch_name: str
    depends_on: tuple[UUID, ...] = ()
    required_checks: tuple[str, ...] = ()
    required_approvals: int = 0


@dataclass(frozen=True, slots=True)
class PrepareChangeSetCommand:
    organization_id: UUID
    project_id: UUID
    created_by_agent_id: UUID
    title: str
    validation_snapshot_id: UUID | None
    candidates: tuple[RepositoryCandidateInput, ...]


@dataclass(frozen=True, slots=True)
class PullRequestObservationCommand:
    change_set_id: UUID
    repository_id: UUID
    pull_request_number: int
    pull_request_url: str
    head_sha: str


@dataclass(frozen=True, slots=True)
class CIObservationCommand:
    change_set_id: UUID
    repository_id: UUID
    passed: bool
    check_run_id: str
    summary: str
    check_name: str = ""


@dataclass(frozen=True, slots=True)
class ReviewObservationCommand:
    change_set_id: UUID
    repository_id: UUID
    review_id: str
    reviewer: str
    state: ReviewState
    head_sha: str
    summary: str = ""


@dataclass(frozen=True, slots=True)
class CICheckObservationView:
    check_name: str
    check_run_id: str
    passed: bool
    summary: str


@dataclass(frozen=True, slots=True)
class ReviewObservationView:
    review_id: str
    reviewer: str
    state: ReviewState
    summary: str


@dataclass(frozen=True, slots=True)
class MergeObservationCommand:
    change_set_id: UUID
    repository_id: UUID
    merge_sha: str


@dataclass(frozen=True, slots=True)
class PlanRecoveryCommand:
    change_set_id: UUID
    trigger: RecoveryTrigger
    reason: str
    repository_id: UUID | None = None
    run_id: UUID | None = None
    native_session_id: str | None = None


@dataclass(frozen=True, slots=True)
class RecordRecoveryActionCommand:
    change_set_id: UUID
    recovery_plan_id: UUID
    action_id: UUID
    status: RecoveryActionStatus
    detail: str


@dataclass(frozen=True, slots=True)
class RepositoryDeliveryView:
    repository_id: UUID
    task_id: UUID
    commit_sha: str
    base_sha: str
    branch_name: str
    depends_on: tuple[UUID, ...]
    merge_order: int
    status: RepositoryDeliveryStatus
    pull_request_number: int | None
    pull_request_url: str | None
    ci_check_run_id: str | None
    ci_summary: str | None
    merge_sha: str | None
    required_checks: tuple[str, ...]
    ci_checks: tuple[CICheckObservationView, ...]
    required_approvals: int
    reviews: tuple[ReviewObservationView, ...]


@dataclass(frozen=True, slots=True)
class RecoveryActionView:
    id: UUID
    sequence: int
    kind: RecoveryActionKind
    status: RecoveryActionStatus
    repository_id: UUID | None
    run_id: UUID | None
    detail: str


@dataclass(frozen=True, slots=True)
class RecoveryPlanView:
    id: UUID
    trigger: RecoveryTrigger
    reason: str
    created_at: datetime
    actions: tuple[RecoveryActionView, ...]


@dataclass(frozen=True, slots=True)
class ChangeSetView:
    id: UUID
    organization_id: UUID
    project_id: UUID
    created_by_agent_id: UUID
    title: str
    validation_snapshot_id: UUID | None
    status: ChangeSetStatus
    version: int
    repositories: tuple[RepositoryDeliveryView, ...]
    recovery_plans: tuple[RecoveryPlanView, ...]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class MergeGateDecision:
    change_set_id: UUID
    repository_id: UUID
    allowed: bool
    reasons: tuple[str, ...]
