from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class RecoverySourceType(StrEnum):
    WORKER_EXECUTION = "worker_execution"
    DELIVERY_CONFLICT = "delivery_conflict"
    DELIVERY_RECOVERY = "delivery_recovery"
    HUMAN_REVIEW = "human_review"


class RecoveryCaseStatus(StrEnum):
    DETECTED = "detected"
    AUTOMATIC_RECOVERY = "automatic_recovery"
    AWAITING_DECISION = "awaiting_decision"
    APPROVED = "approved"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    RESOLVED = "resolved"
    FAILED = "failed"


class RecoverySeverity(StrEnum):
    WARNING = "warning"
    CRITICAL = "critical"


class RecoveryAction(StrEnum):
    RESUME_SESSION = "resume_session"
    REASSIGN_WORKER = "reassign_worker"
    CREATE_CONFLICT_TASK = "create_conflict_task"
    APPROVE_PLAN_REVISION = "approve_plan_revision"
    ROLLBACK_CHANGE_SET = "rollback_change_set"
    RETRY = "retry"
    CANCEL_TASK = "cancel_task"
    MANUAL_RESOLUTION = "manual_resolution"


@dataclass(frozen=True, slots=True)
class RecoveryCaseUpsert:
    source_type: RecoverySourceType
    source_id: UUID
    organization_id: UUID
    project_id: UUID
    evidence_version: str
    summary: str
    severity: RecoverySeverity
    available_actions: tuple[RecoveryAction, ...]
    repository_id: UUID | None = None
    task_id: UUID | None = None
    change_set_id: UUID | None = None
    automatic: bool = False


@dataclass(frozen=True, slots=True)
class RecoveryCaseView:
    id: UUID
    source_type: RecoverySourceType
    source_id: UUID
    organization_id: UUID
    project_id: UUID
    repository_id: UUID | None
    task_id: UUID | None
    change_set_id: UUID | None
    status: RecoveryCaseStatus
    severity: RecoverySeverity
    summary: str
    evidence_version: str
    available_actions: tuple[RecoveryAction, ...]
    version: int
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None


@dataclass(frozen=True, slots=True)
class RecoveryDecisionView:
    id: UUID
    case_id: UUID
    case_version: int
    evidence_version: str
    action: RecoveryAction
    decided_by_human_id: UUID
    reason: str
    decided_at: datetime


@dataclass(frozen=True, slots=True)
class RecoveryOperationView:
    id: UUID
    case_id: UUID
    decision_id: UUID
    action: RecoveryAction
    state: str
    attempts: int
    lease_owner: str | None
    error_code: str | None


class RecoveryCaseSink(Protocol):
    async def ensure_case(self, command: RecoveryCaseUpsert) -> RecoveryCaseView: ...
    async def resolve_source(self, source_type: RecoverySourceType, source_id: UUID) -> None: ...
