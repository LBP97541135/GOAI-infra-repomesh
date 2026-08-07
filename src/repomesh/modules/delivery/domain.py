from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import UUID

from repomesh.shared.domain import new_id

from .contracts import (
    ChangeSetStatus,
    ChangeSetView,
    CICheckObservationView,
    RecoveryActionKind,
    RecoveryActionStatus,
    RecoveryActionView,
    RecoveryPlanView,
    RecoveryTrigger,
    RepositoryDeliveryStatus,
    RepositoryDeliveryView,
)


class DeliveryError(Exception):
    pass


class DeliveryConflict(DeliveryError):
    pass


class DeliveryNotFound(DeliveryError):
    pass


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
        checks = tuple(
            item for item in self.ci_checks if item.check_name != normalized_name
        ) + (observation,)
        required = {name.strip().lower() for name in self.required_checks}
        by_name = {item.check_name: item for item in checks}
        if not required:
            status = (
                RepositoryDeliveryStatus.READY_TO_MERGE
                if passed
                else RepositoryDeliveryStatus.CI_FAILED
            )
        elif any(name in by_name and not by_name[name].passed for name in required):
            status = RepositoryDeliveryStatus.CI_FAILED
        elif required <= by_name.keys() and all(by_name[name].passed for name in required):
            status = RepositoryDeliveryStatus.READY_TO_MERGE
        else:
            status = RepositoryDeliveryStatus.CI_PENDING
        return replace(
            self,
            status=status,
            ci_check_run_id=check_run_id.strip(),
            ci_summary=summary.strip(),
            ci_checks=checks,
        )

    def observe_merge(self, merge_sha: str) -> "RepositoryDelivery":
        if self.status is not RepositoryDeliveryStatus.READY_TO_MERGE:
            raise DeliveryConflict("repository is not ready to merge")
        return replace(
            self,
            status=RepositoryDeliveryStatus.MERGED,
            merge_sha=merge_sha.strip().lower(),
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
class ChangeSet:
    organization_id: UUID
    project_id: UUID
    created_by_agent_id: UUID
    title: str
    validation_snapshot_id: UUID
    repositories: tuple[RepositoryDelivery, ...]
    id: UUID = field(default_factory=new_id)
    status: ChangeSetStatus = ChangeSetStatus.READY
    recovery_plans: tuple[RecoveryPlan, ...] = ()
    version: int = 1
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
        elif any(item.status is RepositoryDeliveryStatus.CI_FAILED for item in repositories):
            status = ChangeSetStatus.BLOCKED
        return replace(
            self,
            repositories=repositories,
            status=status,
            version=self.version + 1,
            updated_at=datetime.now(UTC),
        )

    def add_recovery(self, plan: RecoveryPlan) -> "ChangeSet":
        return replace(
            self,
            status=ChangeSetStatus.COMPENSATING,
            recovery_plans=(*self.recovery_plans, plan),
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
            repositories=tuple(item.to_view() for item in self.repositories),
            recovery_plans=tuple(item.to_view() for item in self.recovery_plans),
            created_at=self.created_at,
            updated_at=self.updated_at,
        )
