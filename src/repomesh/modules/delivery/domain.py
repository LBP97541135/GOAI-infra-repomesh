from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from repomesh.shared.domain import new_id

from .contracts import (
    CandidateRevisionView,
    ChangeSetStatus,
    ChangeSetView,
    CICheckObservationView,
    GovernanceDecisionKind,
    GovernanceDecisionView,
    RecoveryActionKind,
    RecoveryActionStatus,
    RecoveryActionView,
    RecoveryPlanView,
    RecoveryTrigger,
    RepositoryDeliveryStatus,
    RepositoryDeliveryView,
    ReviewObservationView,
    ReviewState,
    SCMCommandKind,
    SCMCommandStatus,
    SCMCommandView,
    SCMObservationSource,
    SCMObservationStatus,
    SCMObservationView,
    SCMPollCursorView,
)


@dataclass(frozen=True, slots=True)
class SCMCommand:
    change_set_id: UUID
    repository_id: UUID
    kind: SCMCommandKind
    idempotency_key: str
    payload: dict[str, object]
    id: UUID = field(default_factory=new_id)
    status: SCMCommandStatus = SCMCommandStatus.PENDING
    attempts: int = 0
    version: int = 1
    last_error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    claimed_at: datetime | None = None
    completed_at: datetime | None = None

    def claim(self, now: datetime) -> "SCMCommand":
        if self.status not in {SCMCommandStatus.PENDING, SCMCommandStatus.FAILED}:
            raise DeliveryConflict("SCM command is not claimable")
        return replace(
            self,
            status=SCMCommandStatus.PROCESSING,
            attempts=self.attempts + 1,
            claimed_at=now,
            version=self.version + 1,
        )

    def accept(self, now: datetime) -> "SCMCommand":
        if self.status is not SCMCommandStatus.PROCESSING:
            raise DeliveryConflict("SCM command is not processing")
        return replace(
            self,
            status=SCMCommandStatus.ACCEPTED,
            completed_at=now,
            last_error=None,
            version=self.version + 1,
        )

    def fail(self, error: str) -> "SCMCommand":
        if self.status is not SCMCommandStatus.PROCESSING:
            raise DeliveryConflict("SCM command is not processing")
        return replace(
            self,
            status=SCMCommandStatus.FAILED,
            last_error=error[:2000],
            version=self.version + 1,
        )

    def to_view(self) -> SCMCommandView:
        return SCMCommandView(
            id=self.id,
            change_set_id=self.change_set_id,
            repository_id=self.repository_id,
            kind=self.kind,
            idempotency_key=self.idempotency_key,
            payload=self.payload,
            status=self.status,
            attempts=self.attempts,
            version=self.version,
            last_error=self.last_error,
            created_at=self.created_at,
            claimed_at=self.claimed_at,
            completed_at=self.completed_at,
        )


@dataclass(frozen=True, slots=True)
class SCMPollCursor:
    change_set_id: UUID
    repository_id: UUID
    next_poll_at: datetime
    consecutive_failures: int = 0
    last_polled_at: datetime | None = None
    last_error: str | None = None
    version: int = 0

    def succeed(self, now: datetime, interval_seconds: float) -> "SCMPollCursor":
        return replace(
            self,
            consecutive_failures=0,
            last_polled_at=now,
            next_poll_at=now + timedelta(seconds=interval_seconds),
            last_error=None,
            version=self.version + 1,
        )

    def fail(
        self, now: datetime, error: str, *, base_seconds: float, retry_after: int | None
    ) -> "SCMPollCursor":
        failures = self.consecutive_failures + 1
        delay = retry_after or min(base_seconds * (2 ** (failures - 1)), 3600)
        return replace(
            self,
            consecutive_failures=failures,
            next_poll_at=now + timedelta(seconds=delay),
            last_error=error[:2000],
            version=self.version + 1,
        )

    def to_view(self) -> SCMPollCursorView:
        return SCMPollCursorView(
            change_set_id=self.change_set_id,
            repository_id=self.repository_id,
            consecutive_failures=self.consecutive_failures,
            last_polled_at=self.last_polled_at,
            next_poll_at=self.next_poll_at,
            last_error=self.last_error,
            version=self.version,
        )


class DeliveryError(Exception):
    pass


class DeliveryConflict(DeliveryError):
    pass


class DeliveryNotFound(DeliveryError):
    pass


@dataclass(frozen=True, slots=True)
class SCMObservation:
    provider: str
    source: SCMObservationSource
    external_id: str
    event_type: str
    payload: dict[str, object]
    payload_hash: str
    observed_at: datetime
    change_set_id: UUID | None = None
    repository_id: UUID | None = None
    id: UUID = field(default_factory=new_id)
    status: SCMObservationStatus = SCMObservationStatus.PENDING
    attempts: int = 0
    version: int = 1
    last_error: str | None = None
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    claimed_at: datetime | None = None
    processed_at: datetime | None = None

    def __post_init__(self) -> None:
        required = (self.provider, self.external_id, self.event_type)
        if any(not value.strip() for value in required):
            raise ValueError("provider, external_id and event_type are required")
        normalized_hash = self.payload_hash.strip().lower()
        if len(normalized_hash) != 64 or any(
            character not in "0123456789abcdef" for character in normalized_hash
        ):
            raise ValueError("payload_hash must be a SHA-256 digest")
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")

    def is_claimable(
        self,
        now: datetime,
        *,
        lease_timeout: timedelta,
        max_attempts: int,
    ) -> bool:
        if self.status is SCMObservationStatus.PROCESSED or self.attempts >= max_attempts:
            return False
        if self.status in {SCMObservationStatus.PENDING, SCMObservationStatus.FAILED}:
            return True
        return (
            self.status is SCMObservationStatus.PROCESSING
            and self.claimed_at is not None
            and self.claimed_at <= now - lease_timeout
        )

    def claim(self, now: datetime) -> "SCMObservation":
        return replace(
            self,
            status=SCMObservationStatus.PROCESSING,
            attempts=self.attempts + 1,
            version=self.version + 1,
            last_error=None,
            claimed_at=now,
            processed_at=None,
        )

    def complete(self, now: datetime) -> "SCMObservation":
        if self.status is not SCMObservationStatus.PROCESSING:
            raise DeliveryConflict("SCM observation is not being processed")
        return replace(
            self,
            status=SCMObservationStatus.PROCESSED,
            version=self.version + 1,
            processed_at=now,
        )

    def fail(self, error: str) -> "SCMObservation":
        if self.status is not SCMObservationStatus.PROCESSING:
            raise DeliveryConflict("SCM observation is not being processed")
        detail = error.strip()
        if not detail:
            raise ValueError("SCM observation failure detail is required")
        return replace(
            self,
            status=SCMObservationStatus.FAILED,
            version=self.version + 1,
            last_error=detail[:2000],
        )

    def to_view(self) -> SCMObservationView:
        return SCMObservationView(
            id=self.id,
            provider=self.provider,
            source=self.source,
            external_id=self.external_id,
            event_type=self.event_type,
            payload=self.payload,
            payload_hash=self.payload_hash,
            status=self.status,
            change_set_id=self.change_set_id,
            repository_id=self.repository_id,
            attempts=self.attempts,
            version=self.version,
            last_error=self.last_error,
            observed_at=self.observed_at,
            received_at=self.received_at,
            claimed_at=self.claimed_at,
            processed_at=self.processed_at,
        )


@dataclass(frozen=True, slots=True)
class CICheckObservation:
    check_name: str
    check_run_id: str
    passed: bool
    summary: str

    def to_view(self) -> CICheckObservationView:
        return CICheckObservationView(
            check_name=self.check_name,
            check_run_id=self.check_run_id,
            passed=self.passed,
            summary=self.summary,
        )


@dataclass(frozen=True, slots=True)
class ReviewObservation:
    review_id: str
    reviewer: str
    state: ReviewState
    summary: str

    def to_view(self) -> ReviewObservationView:
        return ReviewObservationView(
            review_id=self.review_id,
            reviewer=self.reviewer,
            state=self.state,
            summary=self.summary,
        )


@dataclass(frozen=True, slots=True)
class RepositoryDelivery:
    repository_id: UUID
    task_id: UUID
    commit_sha: str
    base_sha: str
    branch_name: str
    depends_on: tuple[UUID, ...]
    merge_order: int
    required_checks: tuple[str, ...] = ()
    ci_checks: tuple[CICheckObservation, ...] = ()
    required_approvals: int = 0
    reviews: tuple[ReviewObservation, ...] = ()
    status: RepositoryDeliveryStatus = RepositoryDeliveryStatus.PENDING
    pull_request_number: int | None = None
    pull_request_url: str | None = None
    ci_check_run_id: str | None = None
    ci_summary: str | None = None
    merge_sha: str | None = None

    def __post_init__(self) -> None:
        for value, label in ((self.commit_sha, "commit_sha"), (self.base_sha, "base_sha")):
            normalized = value.strip().lower()
            if len(normalized) != 40 or any(char not in "0123456789abcdef" for char in normalized):
                raise ValueError(f"{label} must be a full Git object id")
        if not self.branch_name.strip():
            raise ValueError("branch_name is required")
        normalized = tuple(name.strip().lower() for name in self.required_checks)
        if any(not name for name in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("required_checks must be non-empty and unique")
        if self.required_approvals < 0:
            raise ValueError("required_approvals cannot be negative")

    def observe_pr(self, number: int, url: str, head_sha: str) -> "RepositoryDelivery":
        if head_sha.strip().lower() != self.commit_sha:
            raise DeliveryConflict("pull request head does not match candidate commit")
        if self.status not in {RepositoryDeliveryStatus.PENDING, RepositoryDeliveryStatus.PR_OPEN}:
            raise DeliveryConflict(f"cannot attach pull request from {self.status.value}")
        return replace(
            self,
            status=RepositoryDeliveryStatus.PR_OPEN,
            pull_request_number=number,
            pull_request_url=url.strip(),
        )

    def observe_ci(
        self, passed: bool, check_run_id: str, summary: str, check_name: str = ""
    ) -> "RepositoryDelivery":
        if self.pull_request_number is None:
            raise DeliveryConflict("CI observation requires an open pull request")
        normalized_name = (check_name.strip() or check_run_id.strip()).lower()
        observation = CICheckObservation(
            check_name=normalized_name,
            check_run_id=check_run_id.strip(),
            passed=passed,
            summary=summary.strip(),
        )
        checks = tuple(item for item in self.ci_checks if item.check_name != normalized_name) + (
            observation,
        )
        updated = replace(
            self,
            ci_check_run_id=check_run_id.strip(),
            ci_summary=summary.strip(),
            ci_checks=checks,
        )
        return replace(updated, status=updated._readiness_status())

    def observe_review(
        self,
        review_id: str,
        reviewer: str,
        state: ReviewState,
        head_sha: str,
        summary: str,
    ) -> "RepositoryDelivery":
        if self.pull_request_number is None:
            raise DeliveryConflict("review observation requires an open pull request")
        if head_sha.strip().lower() != self.commit_sha:
            raise DeliveryConflict("review head does not match candidate commit")
        normalized_reviewer = reviewer.strip().lower()
        if not review_id.strip() or not normalized_reviewer:
            raise ValueError("review id and reviewer are required")
        observation = ReviewObservation(
            review_id=review_id.strip(),
            reviewer=normalized_reviewer,
            state=state,
            summary=summary.strip(),
        )
        reviews = tuple(item for item in self.reviews if item.reviewer != normalized_reviewer) + (
            observation,
        )
        updated = replace(self, reviews=reviews)
        return replace(updated, status=updated._readiness_status())

    def _readiness_status(self) -> RepositoryDeliveryStatus:
        required = {name.strip().lower() for name in self.required_checks}
        by_name = {item.check_name: item for item in self.ci_checks}
        if any(name in by_name and not by_name[name].passed for name in required):
            return RepositoryDeliveryStatus.CI_FAILED
        if required and not (
            required <= by_name.keys() and all(by_name[name].passed for name in required)
        ):
            return RepositoryDeliveryStatus.CI_PENDING
        if not required and self.ci_checks and not self.ci_checks[-1].passed:
            return RepositoryDeliveryStatus.CI_FAILED
        if any(item.state is ReviewState.CHANGES_REQUESTED for item in self.reviews):
            return RepositoryDeliveryStatus.REVIEW_CHANGES_REQUESTED
        approvals = {item.reviewer for item in self.reviews if item.state is ReviewState.APPROVED}
        if len(approvals) < self.required_approvals:
            return RepositoryDeliveryStatus.REVIEW_PENDING
        if not required and not self.ci_checks:
            return RepositoryDeliveryStatus.CI_PENDING
        return RepositoryDeliveryStatus.READY_TO_MERGE

    def observe_merge(self, merge_sha: str) -> "RepositoryDelivery":
        if self.status not in {
            RepositoryDeliveryStatus.READY_TO_MERGE,
            RepositoryDeliveryStatus.MERGE_REQUESTED,
        }:
            raise DeliveryConflict("repository is not ready to merge")
        return replace(
            self,
            status=RepositoryDeliveryStatus.MERGED,
            merge_sha=merge_sha.strip().lower(),
        )

    def request_merge(self, head_sha: str) -> "RepositoryDelivery":
        if head_sha.strip().lower() != self.commit_sha:
            raise DeliveryConflict("merge request head does not match candidate commit")
        if self.status is RepositoryDeliveryStatus.MERGE_REQUESTED:
            return self
        if self.status is not RepositoryDeliveryStatus.READY_TO_MERGE:
            raise DeliveryConflict("repository is not ready to request merge")
        return replace(self, status=RepositoryDeliveryStatus.MERGE_REQUESTED)

    def revise(
        self, task_id: UUID, previous_head_sha: str, new_head_sha: str
    ) -> "RepositoryDelivery":
        if self.status is RepositoryDeliveryStatus.MERGED:
            raise DeliveryConflict("merged candidate cannot be revised")
        if previous_head_sha.strip().lower() != self.commit_sha:
            raise DeliveryConflict("candidate revision is based on a stale head")
        normalized = new_head_sha.strip().lower()
        if len(normalized) != 40 or any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("new candidate head must be a full Git object id")
        if normalized == self.commit_sha:
            raise DeliveryConflict("candidate revision must change the head SHA")
        return replace(
            self,
            task_id=task_id,
            base_sha=self.commit_sha,
            commit_sha=normalized,
            status=(
                RepositoryDeliveryStatus.PR_OPEN
                if self.pull_request_number is not None
                else RepositoryDeliveryStatus.PENDING
            ),
            ci_check_run_id=None,
            ci_summary=None,
            ci_checks=(),
            reviews=(),
            merge_sha=None,
        )

    def to_view(self) -> RepositoryDeliveryView:
        return RepositoryDeliveryView(
            repository_id=self.repository_id,
            task_id=self.task_id,
            commit_sha=self.commit_sha,
            base_sha=self.base_sha,
            branch_name=self.branch_name,
            depends_on=self.depends_on,
            merge_order=self.merge_order,
            status=self.status,
            pull_request_number=self.pull_request_number,
            pull_request_url=self.pull_request_url,
            ci_check_run_id=self.ci_check_run_id,
            ci_summary=self.ci_summary,
            merge_sha=self.merge_sha,
            required_checks=self.required_checks,
            ci_checks=tuple(item.to_view() for item in self.ci_checks),
            required_approvals=self.required_approvals,
            reviews=tuple(item.to_view() for item in self.reviews),
        )


@dataclass(frozen=True, slots=True)
class RecoveryAction:
    sequence: int
    kind: RecoveryActionKind
    repository_id: UUID | None
    run_id: UUID | None
    detail: str
    id: UUID = field(default_factory=new_id)
    status: RecoveryActionStatus = RecoveryActionStatus.PENDING

    def record(self, status: RecoveryActionStatus, detail: str) -> "RecoveryAction":
        if self.status in {RecoveryActionStatus.SUCCEEDED, RecoveryActionStatus.SKIPPED}:
            if status is not self.status:
                raise DeliveryConflict("completed recovery action cannot change status")
            return self
        if self.status is RecoveryActionStatus.WAITING_WORKER and status not in {
            RecoveryActionStatus.RUNNING,
            RecoveryActionStatus.SUCCEEDED,
            RecoveryActionStatus.FAILED,
        }:
            raise DeliveryConflict("waiting recovery action requires Worker resolution")
        return replace(self, status=status, detail=detail.strip() or self.detail)

    def to_view(self) -> RecoveryActionView:
        return RecoveryActionView(
            id=self.id,
            sequence=self.sequence,
            kind=self.kind,
            status=self.status,
            repository_id=self.repository_id,
            run_id=self.run_id,
            detail=self.detail,
        )


@dataclass(frozen=True, slots=True)
class RecoveryPlan:
    trigger: RecoveryTrigger
    reason: str
    actions: tuple[RecoveryAction, ...]
    id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def record_action(
        self, action_id: UUID, status: RecoveryActionStatus, detail: str
    ) -> "RecoveryPlan":
        if not any(action.id == action_id for action in self.actions):
            raise DeliveryNotFound(f"recovery action not found: {action_id}")
        return replace(
            self,
            actions=tuple(
                action.record(status, detail) if action.id == action_id else action
                for action in self.actions
            ),
        )

    def to_view(self) -> RecoveryPlanView:
        return RecoveryPlanView(
            id=self.id,
            trigger=self.trigger,
            reason=self.reason,
            created_at=self.created_at,
            actions=tuple(action.to_view() for action in self.actions),
        )


@dataclass(frozen=True, slots=True)
class GovernanceDecision:
    repository_id: UUID
    head_sha: str
    decision: GovernanceDecisionKind
    decided_by_agent_id: UUID
    reason: str
    id: UUID = field(default_factory=new_id)
    decided_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        normalized = self.head_sha.strip().lower()
        if len(normalized) != 40 or any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("governance decision head_sha must be a full Git object id")
        if not self.reason.strip():
            raise ValueError("governance decision reason is required")

    def to_view(self) -> GovernanceDecisionView:
        return GovernanceDecisionView(
            id=self.id,
            repository_id=self.repository_id,
            head_sha=self.head_sha,
            decision=self.decision,
            decided_by_agent_id=self.decided_by_agent_id,
            reason=self.reason,
            decided_at=self.decided_at,
        )


@dataclass(frozen=True, slots=True)
class CandidateRevision:
    repository_id: UUID
    task_id: UUID
    sequence: int
    head_sha: str
    previous_head_sha: str | None
    reason: str
    id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_view(self) -> CandidateRevisionView:
        return CandidateRevisionView(
            id=self.id,
            repository_id=self.repository_id,
            task_id=self.task_id,
            sequence=self.sequence,
            head_sha=self.head_sha,
            previous_head_sha=self.previous_head_sha,
            reason=self.reason,
            created_at=self.created_at,
        )


@dataclass(frozen=True, slots=True)
class ChangeSet:
    organization_id: UUID
    project_id: UUID
    created_by_agent_id: UUID
    title: str
    validation_snapshot_id: UUID | None
    repositories: tuple[RepositoryDelivery, ...]
    id: UUID = field(default_factory=new_id)
    status: ChangeSetStatus = ChangeSetStatus.READY
    recovery_plans: tuple[RecoveryPlan, ...] = ()
    governance_decisions: tuple[GovernanceDecision, ...] = ()
    candidate_revisions: tuple[CandidateRevision, ...] = ()
    version: int = 1
    merge_cursor: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.title.strip() or not self.repositories:
            raise ValueError("ChangeSet title and repositories are required")
        ids = [item.repository_id for item in self.repositories]
        if len(ids) != len(set(ids)):
            raise ValueError("ChangeSet contains duplicate repositories")

    def with_repositories(self, repositories: tuple[RepositoryDelivery, ...]) -> "ChangeSet":
        status = ChangeSetStatus.DELIVERING
        if all(item.status is RepositoryDeliveryStatus.MERGED for item in repositories):
            status = ChangeSetStatus.DELIVERED
        elif any(
            item.status
            in {
                RepositoryDeliveryStatus.CI_FAILED,
                RepositoryDeliveryStatus.REVIEW_CHANGES_REQUESTED,
            }
            for item in repositories
        ):
            status = ChangeSetStatus.BLOCKED
        pending_orders = [
            item.merge_order
            for item in repositories
            if item.status is not RepositoryDeliveryStatus.MERGED
        ]
        return replace(
            self,
            repositories=repositories,
            status=status,
            version=self.version + 1,
            merge_cursor=min(pending_orders, default=len(repositories)),
            updated_at=datetime.now(UTC),
        )

    def append_repositories(self, repositories: tuple[RepositoryDelivery, ...]) -> "ChangeSet":
        """Extend an existing ChangeSet with a later batch's repositories.

        Used by batch-by-batch delivery: the first batch prepares the
        ChangeSet and subsequent batches append their candidates. Existing
        repository delivery records and their merge order are preserved.
        """
        known = {item.repository_id for item in self.repositories}
        for item in repositories:
            if item.repository_id in known:
                raise DeliveryConflict("ChangeSet already contains the repository candidate")
        merged = self.repositories + repositories
        revisions = self.candidate_revisions + tuple(
            CandidateRevision(
                repository_id=item.repository_id,
                task_id=item.task_id,
                sequence=0,
                head_sha=item.commit_sha,
                previous_head_sha=None,
                reason="initial candidate",
            )
            for item in repositories
        )
        updated = self.with_repositories(merged)
        return replace(updated, candidate_revisions=revisions)

    def add_recovery(self, plan: RecoveryPlan) -> "ChangeSet":
        return replace(
            self,
            status=ChangeSetStatus.COMPENSATING,
            recovery_plans=(*self.recovery_plans, plan),
            version=self.version + 1,
            updated_at=datetime.now(UTC),
        )

    def record_governance(self, decision: GovernanceDecision) -> "ChangeSet":
        candidate = next(
            (item for item in self.repositories if item.repository_id == decision.repository_id),
            None,
        )
        if candidate is None:
            raise DeliveryNotFound("governance repository is not in ChangeSet")
        if decision.head_sha != candidate.commit_sha:
            raise DeliveryConflict("governance decision does not match candidate head")
        retained = tuple(
            item
            for item in self.governance_decisions
            if not (
                item.repository_id == decision.repository_id and item.head_sha == decision.head_sha
            )
        )
        return replace(
            self,
            governance_decisions=(*retained, decision),
            version=self.version + 1,
            updated_at=datetime.now(UTC),
        )

    def record_candidate_revision(
        self,
        repository_id: UUID,
        task_id: UUID,
        previous_head_sha: str,
        new_head_sha: str,
        reason: str,
    ) -> "ChangeSet":
        candidate = next(
            (item for item in self.repositories if item.repository_id == repository_id), None
        )
        if candidate is None:
            raise DeliveryNotFound("revision repository is not in ChangeSet")
        revised = candidate.revise(task_id, previous_head_sha, new_head_sha)
        sequence = 1 + max(
            (
                item.sequence
                for item in self.candidate_revisions
                if item.repository_id == repository_id
            ),
            default=0,
        )
        revision = CandidateRevision(
            repository_id=repository_id,
            task_id=task_id,
            sequence=sequence,
            head_sha=revised.commit_sha,
            previous_head_sha=candidate.commit_sha,
            reason=reason.strip() or "candidate rework",
        )
        repositories = tuple(
            revised if item.repository_id == repository_id else item for item in self.repositories
        )
        return replace(
            self,
            repositories=repositories,
            candidate_revisions=(*self.candidate_revisions, revision),
            status=ChangeSetStatus.DELIVERING,
            merge_cursor=min(
                item.merge_order
                for item in repositories
                if item.status is not RepositoryDeliveryStatus.MERGED
            ),
            version=self.version + 1,
            updated_at=datetime.now(UTC),
        )

    def record_recovery_action(
        self,
        plan_id: UUID,
        action_id: UUID,
        status: RecoveryActionStatus,
        detail: str,
    ) -> "ChangeSet":
        if not any(plan.id == plan_id for plan in self.recovery_plans):
            raise DeliveryNotFound(f"recovery plan not found: {plan_id}")
        plans = tuple(
            plan.record_action(action_id, status, detail) if plan.id == plan_id else plan
            for plan in self.recovery_plans
        )
        active = next(plan for plan in plans if plan.id == plan_id)
        if any(action.status is RecoveryActionStatus.FAILED for action in active.actions):
            change_set_status = ChangeSetStatus.MANUAL_INTERVENTION
        elif all(
            action.status in {RecoveryActionStatus.SUCCEEDED, RecoveryActionStatus.SKIPPED}
            for action in active.actions
        ):
            change_set_status = ChangeSetStatus.COMPENSATED
        else:
            change_set_status = ChangeSetStatus.COMPENSATING
        return replace(
            self,
            status=change_set_status,
            recovery_plans=plans,
            version=self.version + 1,
            updated_at=datetime.now(UTC),
        )

    def to_view(self) -> ChangeSetView:
        return ChangeSetView(
            id=self.id,
            organization_id=self.organization_id,
            project_id=self.project_id,
            created_by_agent_id=self.created_by_agent_id,
            title=self.title,
            validation_snapshot_id=self.validation_snapshot_id,
            status=self.status,
            version=self.version,
            merge_cursor=self.merge_cursor,
            repositories=tuple(item.to_view() for item in self.repositories),
            recovery_plans=tuple(item.to_view() for item in self.recovery_plans),
            governance_decisions=tuple(item.to_view() for item in self.governance_decisions),
            candidate_revisions=tuple(item.to_view() for item in self.candidate_revisions),
            created_at=self.created_at,
            updated_at=self.updated_at,
        )
