from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from repomesh.modules.observability.infrastructure.alerting import AlertingStore
from repomesh.modules.observability.infrastructure.models import (
    LLMUsageRecord,
    LogEntryRecord,
    TraceSessionRecord,
)
from repomesh.modules.observability.operations import (
    CapacityPolicy,
    CapacityState,
    InMemoryOperationalGate,
    ObservabilityRetentionService,
    OperationalAction,
    OperationalResponseCoordinator,
    OperationalResponseStore,
    ReadinessState,
    RetentionPolicy,
    discover_alembic_heads,
    operational_readiness,
)


class RecordingNotifications:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    async def send(self, payload: dict[str, object]) -> None:
        self.payloads.append(payload)


def test_capacity_boundaries_and_unknown_production_fail_closed() -> None:
    policy = CapacityPolicy(production=True, retry_after_seconds=45)

    assert policy.evaluate("runner", 7, 10).state is CapacityState.AVAILABLE
    pressured = policy.evaluate("runner", 8, 10)
    saturated = policy.evaluate("runner", 10, 10)
    unknown = policy.evaluate("runner", None, 10)

    assert pressured.state is CapacityState.PRESSURED
    assert pressured.accept_new_work
    assert saturated.state is CapacityState.SATURATED
    assert not saturated.accept_new_work
    assert saturated.retry_after_seconds == 45
    assert unknown.state is CapacityState.UNKNOWN
    assert not unknown.accept_new_work


def test_unknown_development_capacity_is_visible_but_not_blocking() -> None:
    snapshot = CapacityPolicy(production=False).evaluate("scm", None, 5)
    assert snapshot.state is CapacityState.UNKNOWN
    assert snapshot.accept_new_work
    assert snapshot.retry_after_seconds is None


@pytest.mark.asyncio
async def test_firing_alert_notifies_and_applies_action_once(application_container) -> None:
    alerts = AlertingStore(application_container.database)
    rule = await alerts.create_rule(
        name="runner saturated",
        metric="calls",
        operator="gt",
        threshold=1,
    )
    event = await alerts.create_event(rule, 2)
    notifications = RecordingNotifications()
    gate = InMemoryOperationalGate()
    coordinator = OperationalResponseCoordinator(
        OperationalResponseStore(application_container.database),
        notifications,
        gate,
        default_action=OperationalAction.PAUSE_INTAKE,
    )

    await coordinator.firing(event)
    await coordinator.firing(event)

    assert len(notifications.payloads) == 1
    assert set(notifications.payloads[0]) == {
        "event_id", "rule_id", "rule_name", "status", "value", "triggered_at"
    }
    assert gate.intake_paused()

    await coordinator.resolved(event["id"])
    assert not gate.intake_paused()


@pytest.mark.asyncio
async def test_resolving_one_alert_does_not_clear_another_alert_action(
    application_container,
) -> None:
    alerts = AlertingStore(application_container.database)
    rule = await alerts.create_rule(
        name="pressure", metric="calls", operator="gt", threshold=1
    )
    first = await alerts.create_event(rule, 2)
    second = {**first, "id": uuid4(), "triggered_at": datetime.now(UTC)}
    notifications = RecordingNotifications()
    gate = InMemoryOperationalGate()
    coordinator = OperationalResponseCoordinator(
        OperationalResponseStore(application_container.database),
        notifications,
        gate,
        default_action=OperationalAction.DEGRADE_WRITES,
    )
    await coordinator.firing(first)
    # The second event needs a real FK target in production. A second rule/event
    # supplies it while retaining the same governed action.
    other_rule = await alerts.create_rule(
        name="errors", metric="error_count", operator="gt", threshold=1
    )
    second = await alerts.create_event(other_rule, 2)
    await coordinator.firing(second)

    await coordinator.resolved(first["id"])
    assert gate.writes_degraded()
    await coordinator.resolved(second["id"])
    assert not gate.writes_degraded()


def test_readiness_never_calls_missing_backup_a_pass() -> None:
    checks = operational_readiness(
        alembic_heads=("20260831_0051",),
        backup_configured=False,
        last_backup_age_hours=None,
        restore_drill_age_days=None,
    )
    by_name = {item.name: item for item in checks}
    assert by_name["alembic_single_head"].state is ReadinessState.PASSED
    assert by_name["database_backup"].state is ReadinessState.BLOCKED_EXTERNAL
    assert by_name["restore_drill"].state is ReadinessState.BLOCKED_EXTERNAL


def test_repository_has_one_discoverable_migration_head() -> None:
    assert discover_alembic_heads() == ("20260831_0051",)


@pytest.mark.asyncio
async def test_retention_is_bounded_and_keeps_recent_rows(application_container) -> None:
    database = application_container.database
    now = datetime.now(UTC)
    old = now.replace(year=now.year - 1)
    async with database.transaction() as session:
        for index in range(2):
            session.add(
                LLMUsageRecord(
                    id=uuid4(),
                    created_at=old,
                    provider="test",
                    model="test",
                    operation=f"old-{index}",
                    prompt_tokens=1,
                    completion_tokens=1,
                    total_tokens=2,
                    status="ok",
                )
            )
        session.add(
            LLMUsageRecord(
                id=uuid4(),
                created_at=now,
                provider="test",
                model="test",
                operation="recent",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                status="ok",
            )
        )
        session.add(
            LogEntryRecord(
                id=uuid4(), ts=old, level="INFO", source="test", message="old"
            )
        )
        session.add(
            TraceSessionRecord(
                id=uuid4(),
                session_id="old-session",
                agent_name="worker",
                source_key="old-session.jsonl",
                object_mtime=old,
                first_seen_at=old,
            )
        )

    service = ObservabilityRetentionService(
        database, RetentionPolicy(usage_days=30, log_days=30, trace_days=30, batch_size=1)
    )
    first = await service.run_once(now=now)
    second = await service.run_once(now=now)

    assert first.usage_deleted == 1
    assert second.usage_deleted == 1
    assert first.logs_deleted == 1
    assert first.trace_sessions_deleted == 1
    async with database.transaction() as session:
        remaining = await session.scalar(select(func.count()).select_from(LLMUsageRecord))
    assert remaining == 1


def test_retention_refuses_dangerously_short_windows() -> None:
    with pytest.raises(ValueError, match="at least 7 days"):
        RetentionPolicy(usage_days=1)
