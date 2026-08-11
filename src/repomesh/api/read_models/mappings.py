"""Status mappings of delivery-read-model contract v0.1 §2 and §5.

These functions are the single implementation of the contract's derivations;
the frontend must not re-map statuses.
"""

from enum import StrEnum

from repomesh.modules.delivery.contracts import (
    ChangeSetStatus,
    ChangeSetView,
    RecoveryActionKind,
    RecoveryActionStatus,
    RepositoryDeliveryStatus,
)
from repomesh.modules.task_orchestration.contracts import ExecutionPlanStatus, TaskStatus


class DeliveryPhase(StrEnum):
    CONTRACT = "contract"
    PLAN = "plan"
    EXECUTE = "execute"
    VALIDATE = "validate"
    RELEASE = "release"
    DELIVERED = "delivered"
    FAILED = "failed"
    ARCHIVED = "archived"


class TaskDisplayStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    REPAIRING = "repairing"
    BLOCKED = "blocked"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class GateDisplay(StrEnum):
    OPEN = "open"
    BLOCKED = "blocked"
    RUNNING = "running"
    WAITING = "waiting"


_TERMINAL_ACTION_STATUSES = frozenset(
    {RecoveryActionStatus.SUCCEEDED, RecoveryActionStatus.SKIPPED}
)


def has_active_recovery(change_set: ChangeSetView) -> bool:
    if not change_set.recovery_plans:
        return False
    active = change_set.recovery_plans[-1]
    return any(
        action.status not in _TERMINAL_ACTION_STATUSES for action in active.actions
    )


def has_manual_intervention(change_set: ChangeSetView) -> bool:
    """Whether an unfinished MANUAL_INTERVENTION recovery action exists (§5.2)."""

    for plan in change_set.recovery_plans:
        for action in plan.actions:
            if (
                action.kind is RecoveryActionKind.MANUAL_INTERVENTION
                and action.status not in _TERMINAL_ACTION_STATUSES
            ):
                return True
    return False


def derive_phase(
    *,
    archived: bool,
    plan_status: ExecutionPlanStatus | None,
    change_set: ChangeSetView | None,
    has_validation_snapshot: bool,
    has_plan_snapshot: bool,
    materialized: bool,
) -> DeliveryPhase:
    """§2 phase derivation, evaluated strictly in table order."""

    if archived:
        return DeliveryPhase.ARCHIVED
    failure_signal = plan_status is ExecutionPlanStatus.FAILED or (
        change_set is not None
        and change_set.status
        in {ChangeSetStatus.MANUAL_INTERVENTION, ChangeSetStatus.COMPENSATED}
    )
    if failure_signal and (change_set is None or not has_active_recovery(change_set)):
        return DeliveryPhase.FAILED
    if change_set is not None:
        if change_set.status is ChangeSetStatus.DELIVERED:
            return DeliveryPhase.DELIVERED
        return DeliveryPhase.RELEASE
    if has_validation_snapshot:
        return DeliveryPhase.VALIDATE
    if plan_status is ExecutionPlanStatus.IN_PROGRESS:
        return DeliveryPhase.EXECUTE
    if plan_status is not None:
        # A completed plan whose delivery pipeline has not produced evidence yet.
        return DeliveryPhase.VALIDATE
    if has_plan_snapshot and not materialized:
        return DeliveryPhase.PLAN
    return DeliveryPhase.CONTRACT


def task_display_status(
    status: TaskStatus, *, has_active_rework: bool
) -> TaskDisplayStatus | None:
    """§5.1 mapping; SUPERSEDED returns None and is filtered from lists."""

    if status is TaskStatus.SUPERSEDED:
        return None
    if status is TaskStatus.BLOCKED:
        return TaskDisplayStatus.BLOCKED
    if status is TaskStatus.IN_PROGRESS:
        return (
            TaskDisplayStatus.REPAIRING if has_active_rework else TaskDisplayStatus.RUNNING
        )
    if status is TaskStatus.ASSIGNED:
        return TaskDisplayStatus.PENDING
    if status is TaskStatus.SUCCEEDED:
        return TaskDisplayStatus.SUCCEEDED
    return TaskDisplayStatus.FAILED


_GATE_DISPLAY = {
    RepositoryDeliveryStatus.READY_TO_MERGE: GateDisplay.OPEN,
    RepositoryDeliveryStatus.MERGE_REQUESTED: GateDisplay.OPEN,
    RepositoryDeliveryStatus.MERGED: GateDisplay.OPEN,
    RepositoryDeliveryStatus.CI_FAILED: GateDisplay.BLOCKED,
    RepositoryDeliveryStatus.REVIEW_CHANGES_REQUESTED: GateDisplay.BLOCKED,
    RepositoryDeliveryStatus.MANUAL_INTERVENTION: GateDisplay.BLOCKED,
    RepositoryDeliveryStatus.PR_OPEN: GateDisplay.RUNNING,
    RepositoryDeliveryStatus.CI_PENDING: GateDisplay.RUNNING,
    RepositoryDeliveryStatus.REVIEW_PENDING: GateDisplay.RUNNING,
    RepositoryDeliveryStatus.COMPENSATION_PENDING: GateDisplay.RUNNING,
    RepositoryDeliveryStatus.COMPENSATED: GateDisplay.RUNNING,
    RepositoryDeliveryStatus.PENDING: GateDisplay.WAITING,
}


def gate_display(status: RepositoryDeliveryStatus) -> GateDisplay:
    """§5.3 mapping: 12 repository delivery states onto 4 gate displays."""

    return _GATE_DISPLAY[status]
