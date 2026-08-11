"""Contract §2 phase derivation and §5 status mappings, every branch."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from repomesh.api.read_models import (
    DeliveryPhase,
    GateDisplay,
    TaskDisplayStatus,
    derive_phase,
    gate_display,
    task_display_status,
)
from repomesh.modules.delivery.contracts import (
    ChangeSetStatus,
    ChangeSetView,
    RecoveryActionKind,
    RecoveryActionStatus,
    RecoveryActionView,
    RecoveryPlanView,
    RecoveryTrigger,
    RepositoryDeliveryStatus,
)
from repomesh.modules.task_orchestration.contracts import ExecutionPlanStatus, TaskStatus


def _change_set(
    status: ChangeSetStatus,
    *,
    recovery_action_status: RecoveryActionStatus | None = None,
) -> ChangeSetView:
    recovery_plans = ()
    if recovery_action_status is not None:
        recovery_plans = (
            RecoveryPlanView(
                id=uuid4(),
                trigger=RecoveryTrigger.CI_FAILED,
                reason="ci failed",
                created_at=datetime.now(UTC),
                actions=(
                    RecoveryActionView(
                        id=uuid4(),
                        sequence=1,
                        kind=RecoveryActionKind.MANUAL_INTERVENTION,
                        status=recovery_action_status,
                        repository_id=None,
                        run_id=None,
                        detail="",
                    ),
                ),
            ),
        )
    return ChangeSetView(
        id=uuid4(),
        organization_id=uuid4(),
        project_id=uuid4(),
        created_by_agent_id=uuid4(),
        title="cs",
        validation_snapshot_id=None,
        status=status,
        version=1,
        merge_cursor=0,
        repositories=(),
        recovery_plans=recovery_plans,
        governance_decisions=(),
        candidate_revisions=(),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _phase(**overrides) -> DeliveryPhase:
    arguments = {
        "archived": False,
        "plan_status": None,
        "change_set": None,
        "has_validation_snapshot": False,
        "has_plan_snapshot": False,
        "materialized": False,
    }
    arguments.update(overrides)
    return derive_phase(**arguments)


def test_archived_wins_over_everything() -> None:
    assert (
        _phase(archived=True, change_set=_change_set(ChangeSetStatus.DELIVERED))
        is DeliveryPhase.ARCHIVED
    )


def test_failed_plan_or_manual_intervention_without_active_recovery() -> None:
    assert _phase(plan_status=ExecutionPlanStatus.FAILED) is DeliveryPhase.FAILED
    assert (
        _phase(
            plan_status=ExecutionPlanStatus.COMPLETED,
            change_set=_change_set(ChangeSetStatus.MANUAL_INTERVENTION),
        )
        is DeliveryPhase.FAILED
    )
    assert (
        _phase(
            plan_status=ExecutionPlanStatus.COMPLETED,
            change_set=_change_set(ChangeSetStatus.COMPENSATED),
        )
        is DeliveryPhase.FAILED
    )


def test_active_recovery_keeps_a_failing_delivery_in_release() -> None:
    change_set = _change_set(
        ChangeSetStatus.MANUAL_INTERVENTION,
        recovery_action_status=RecoveryActionStatus.RUNNING,
    )
    assert (
        _phase(plan_status=ExecutionPlanStatus.COMPLETED, change_set=change_set)
        is DeliveryPhase.RELEASE
    )


def test_delivered_and_release_phases() -> None:
    assert (
        _phase(
            plan_status=ExecutionPlanStatus.COMPLETED,
            change_set=_change_set(ChangeSetStatus.DELIVERED),
        )
        is DeliveryPhase.DELIVERED
    )
    for status in (
        ChangeSetStatus.DRAFT,
        ChangeSetStatus.READY,
        ChangeSetStatus.DELIVERING,
        ChangeSetStatus.BLOCKED,
    ):
        assert (
            _phase(plan_status=ExecutionPlanStatus.COMPLETED, change_set=_change_set(status))
            is DeliveryPhase.RELEASE
        )


def test_validate_execute_plan_and_contract_phases() -> None:
    assert (
        _phase(plan_status=ExecutionPlanStatus.COMPLETED, has_validation_snapshot=True)
        is DeliveryPhase.VALIDATE
    )
    assert _phase(plan_status=ExecutionPlanStatus.IN_PROGRESS) is DeliveryPhase.EXECUTE
    assert _phase(plan_status=ExecutionPlanStatus.COMPLETED) is DeliveryPhase.VALIDATE
    assert _phase(has_plan_snapshot=True) is DeliveryPhase.PLAN
    assert _phase(has_plan_snapshot=True, materialized=True) is DeliveryPhase.CONTRACT
    assert _phase() is DeliveryPhase.CONTRACT


@pytest.mark.parametrize(
    ("status", "active_rework", "expected"),
    [
        (TaskStatus.ASSIGNED, False, TaskDisplayStatus.PENDING),
        (TaskStatus.IN_PROGRESS, False, TaskDisplayStatus.RUNNING),
        (TaskStatus.IN_PROGRESS, True, TaskDisplayStatus.REPAIRING),
        (TaskStatus.BLOCKED, False, TaskDisplayStatus.BLOCKED),
        (TaskStatus.BLOCKED, True, TaskDisplayStatus.BLOCKED),
        (TaskStatus.SUCCEEDED, False, TaskDisplayStatus.SUCCEEDED),
        (TaskStatus.FAILED, False, TaskDisplayStatus.FAILED),
        (TaskStatus.CANCELLED, False, TaskDisplayStatus.FAILED),
    ],
)
def test_task_display_status_mapping(status, active_rework, expected) -> None:
    assert task_display_status(status, has_active_rework=active_rework) is expected


def test_superseded_tasks_are_filtered() -> None:
    assert task_display_status(TaskStatus.SUPERSEDED, has_active_rework=False) is None


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (RepositoryDeliveryStatus.READY_TO_MERGE, GateDisplay.OPEN),
        (RepositoryDeliveryStatus.MERGE_REQUESTED, GateDisplay.OPEN),
        (RepositoryDeliveryStatus.MERGED, GateDisplay.OPEN),
        (RepositoryDeliveryStatus.CI_FAILED, GateDisplay.BLOCKED),
        (RepositoryDeliveryStatus.REVIEW_CHANGES_REQUESTED, GateDisplay.BLOCKED),
        (RepositoryDeliveryStatus.MANUAL_INTERVENTION, GateDisplay.BLOCKED),
        (RepositoryDeliveryStatus.PR_OPEN, GateDisplay.RUNNING),
        (RepositoryDeliveryStatus.CI_PENDING, GateDisplay.RUNNING),
        (RepositoryDeliveryStatus.REVIEW_PENDING, GateDisplay.RUNNING),
        (RepositoryDeliveryStatus.COMPENSATION_PENDING, GateDisplay.RUNNING),
        (RepositoryDeliveryStatus.COMPENSATED, GateDisplay.RUNNING),
        (RepositoryDeliveryStatus.PENDING, GateDisplay.WAITING),
    ],
)
def test_gate_display_covers_all_twelve_states(status, expected) -> None:
    assert gate_display(status) is expected
