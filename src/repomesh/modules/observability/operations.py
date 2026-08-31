from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from uuid import UUID

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from repomesh.persistence import Database

from .infrastructure.models import (
    LLMUsageRecord,
    LogEntryRecord,
    OperationalResponseRecord,
    TraceSessionRecord,
)

logger = logging.getLogger(__name__)


class CapacityState(StrEnum):
    AVAILABLE = "available"
    PRESSURED = "pressured"
    SATURATED = "saturated"
    UNKNOWN = "unknown"


class OperationalAction(StrEnum):
    NONE = "none"
    DEGRADE_WRITES = "degrade_writes"
    PAUSE_INTAKE = "pause_intake"


class ReadinessState(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    UNKNOWN = "unknown"
    BLOCKED_EXTERNAL = "blocked_external"


@dataclass(frozen=True, slots=True)
class CapacitySnapshot:
    resource: str
    current: int | None
    limit: int
    state: CapacityState
    utilization: float | None
    retry_after_seconds: int | None
    accept_new_work: bool


@dataclass(frozen=True, slots=True)
class ReadinessCheck:
    name: str
    state: ReadinessState
    detail: str


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    usage_days: int = 90
    log_days: int = 30
    trace_days: int = 30
    batch_size: int = 500

    def __post_init__(self) -> None:
        if min(self.usage_days, self.log_days, self.trace_days) < 7:
            raise ValueError("operational telemetry retention must be at least 7 days")
        if not 1 <= self.batch_size <= 10_000:
            raise ValueError("retention batch_size must be between 1 and 10000")


@dataclass(frozen=True, slots=True)
class RetentionResult:
    usage_deleted: int
    logs_deleted: int
    trace_sessions_deleted: int


class ObservabilityRetentionService:
    def __init__(self, database: Database, policy: RetentionPolicy) -> None:
        self._database = database
        self._policy = policy

    async def run_once(self, *, now: datetime | None = None) -> RetentionResult:
        current = now or datetime.now(UTC)
        usage = await self._delete_ids(
            LLMUsageRecord,
            LLMUsageRecord.created_at
            < current - timedelta(days=self._policy.usage_days),
        )
        logs = await self._delete_ids(
            LogEntryRecord,
            LogEntryRecord.ts < current - timedelta(days=self._policy.log_days),
        )
        traces = await self._delete_ids(
            TraceSessionRecord,
            TraceSessionRecord.first_seen_at
            < current - timedelta(days=self._policy.trace_days),
        )
        return RetentionResult(usage, logs, traces)

    async def _delete_ids(self, model, predicate) -> int:
        async with self._database.transaction() as session:
            ids = (
                await session.scalars(
                    select(model.id)
                    .where(predicate)
                    .order_by(model.id)
                    .limit(self._policy.batch_size)
                )
            ).all()
            if not ids:
                return 0
            result = await session.execute(delete(model).where(model.id.in_(ids)))
            return int(result.rowcount or 0)


class CapacityPolicy:
    def __init__(self, *, production: bool, retry_after_seconds: int = 30) -> None:
        if retry_after_seconds < 1:
            raise ValueError("retry_after_seconds must be positive")
        self._production = production
        self._retry_after = retry_after_seconds

    def evaluate(self, resource: str, current: int | None, limit: int) -> CapacitySnapshot:
        if limit < 1:
            raise ValueError("capacity limit must be positive")
        if current is None:
            return CapacitySnapshot(
                resource,
                None,
                limit,
                CapacityState.UNKNOWN,
                None,
                self._retry_after if self._production else None,
                not self._production,
            )
        if current < 0:
            raise ValueError("capacity current value cannot be negative")
        utilization = current / limit
        state = (
            CapacityState.SATURATED
            if utilization >= 1
            else CapacityState.PRESSURED
            if utilization >= 0.8
            else CapacityState.AVAILABLE
        )
        return CapacitySnapshot(
            resource,
            current,
            limit,
            state,
            utilization,
            self._retry_after if state is CapacityState.SATURATED else None,
            state is not CapacityState.SATURATED,
        )


class NotificationSink(Protocol):
    async def send(self, payload: dict[str, object]) -> None: ...


class OperationalGate(Protocol):
    async def apply(self, alert_event_id: UUID, action: OperationalAction) -> None: ...

    async def clear(self, alert_event_id: UUID, action: OperationalAction) -> None: ...


class LoggingNotificationSink:
    async def send(self, payload: dict[str, object]) -> None:
        logger.warning("operational alert notification %s", payload)


class InMemoryOperationalGate:
    def __init__(self) -> None:
        self.actions: dict[UUID, OperationalAction] = {}

    async def apply(self, alert_event_id: UUID, action: OperationalAction) -> None:
        if action is not OperationalAction.NONE:
            self.actions[alert_event_id] = action

    async def clear(self, alert_event_id: UUID, action: OperationalAction) -> None:
        if self.actions.get(alert_event_id) is action:
            self.actions.pop(alert_event_id, None)

    def intake_paused(self) -> bool:
        return OperationalAction.PAUSE_INTAKE in self.actions.values()

    def writes_degraded(self) -> bool:
        return OperationalAction.DEGRADE_WRITES in self.actions.values()


class OperationalResponseStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def reserve(
        self, *, alert_event_id: UUID, action: OperationalAction
    ) -> tuple[OperationalResponseRecord, bool]:
        record = OperationalResponseRecord(
            alert_event_id=alert_event_id,
            action=action.value,
            notification_status="pending",
            action_status="pending" if action is not OperationalAction.NONE else "skipped",
        )
        try:
            async with self._database.transaction() as session:
                session.add(record)
                await session.flush()
            return record, True
        except IntegrityError:
            async with self._database.transaction() as session:
                existing = await session.scalar(
                    select(OperationalResponseRecord).where(
                        OperationalResponseRecord.alert_event_id == alert_event_id
                    )
                )
            if existing is None:
                raise
            return existing, False

    async def mark_notification(
        self, response_id: UUID, status: str, error_code: str | None
    ) -> None:
        async with self._database.transaction() as session:
            record = await session.get(OperationalResponseRecord, response_id)
            if record is not None:
                record.notification_status = status
                record.error_code = error_code

    async def mark_action(self, response_id: UUID, status: str, error_code: str | None) -> None:
        async with self._database.transaction() as session:
            record = await session.get(OperationalResponseRecord, response_id)
            if record is not None:
                record.action_status = status
                record.error_code = error_code

    async def get_by_alert(self, alert_event_id: UUID) -> OperationalResponseRecord | None:
        async with self._database.transaction() as session:
            return await session.scalar(
                select(OperationalResponseRecord).where(
                    OperationalResponseRecord.alert_event_id == alert_event_id
                )
            )


class OperationalResponseCoordinator:
    def __init__(
        self,
        store: OperationalResponseStore,
        notifications: NotificationSink,
        gate: OperationalGate,
        *,
        default_action: OperationalAction = OperationalAction.NONE,
    ) -> None:
        self._store = store
        self._notifications = notifications
        self._gate = gate
        self._default_action = default_action

    async def firing(self, event: dict[str, object]) -> None:
        event_id = UUID(str(event["id"]))
        response, created = await self._store.reserve(
            alert_event_id=event_id, action=self._default_action
        )
        if not created:
            return
        payload = {
            "event_id": str(event_id),
            "rule_id": str(event["rule_id"]),
            "rule_name": str(event.get("rule_name") or ""),
            "status": "firing",
            "value": float(event["value"]),
            "triggered_at": str(event["triggered_at"]),
        }
        try:
            await self._notifications.send(payload)
        except Exception:
            await self._store.mark_notification(response.id, "failed", "notification_failed")
        else:
            await self._store.mark_notification(response.id, "sent", None)
        if self._default_action is OperationalAction.NONE:
            return
        try:
            await self._gate.apply(event_id, self._default_action)
        except Exception:
            await self._store.mark_action(response.id, "failed", "action_failed")
        else:
            await self._store.mark_action(response.id, "applied", None)

    async def resolved(self, alert_event_id: UUID) -> None:
        response = await self._store.get_by_alert(alert_event_id)
        if response is None:
            return
        action = OperationalAction(response.action)
        if action is OperationalAction.NONE:
            return
        try:
            await self._gate.clear(alert_event_id, action)
        except Exception:
            await self._store.mark_action(response.id, "clear_failed", "action_clear_failed")
        else:
            await self._store.mark_action(response.id, "cleared", None)


def operational_readiness(
    *,
    alembic_heads: tuple[str, ...],
    backup_configured: bool,
    last_backup_age_hours: int | None,
    restore_drill_age_days: int | None,
) -> tuple[ReadinessCheck, ...]:
    checks = [
        ReadinessCheck(
            "alembic_single_head",
            ReadinessState.PASSED if len(alembic_heads) == 1 else ReadinessState.FAILED,
            f"heads={len(alembic_heads)}",
        )
    ]
    if not backup_configured:
        checks.extend(
            (
                ReadinessCheck(
                    "database_backup",
                    ReadinessState.BLOCKED_EXTERNAL,
                    "backup target is not configured",
                ),
                ReadinessCheck(
                    "restore_drill",
                    ReadinessState.BLOCKED_EXTERNAL,
                    "restore infrastructure is not configured",
                ),
            )
        )
        return tuple(checks)
    checks.append(
        ReadinessCheck(
            "database_backup",
            ReadinessState.UNKNOWN
            if last_backup_age_hours is None
            else ReadinessState.PASSED
            if last_backup_age_hours <= 24
            else ReadinessState.FAILED,
            "last backup age is unavailable"
            if last_backup_age_hours is None
            else f"last_backup_age_hours={last_backup_age_hours}",
        )
    )
    checks.append(
        ReadinessCheck(
            "restore_drill",
            ReadinessState.UNKNOWN
            if restore_drill_age_days is None
            else ReadinessState.PASSED
            if restore_drill_age_days <= 90
            else ReadinessState.FAILED,
            "restore drill age is unavailable"
            if restore_drill_age_days is None
            else f"restore_drill_age_days={restore_drill_age_days}",
        )
    )
    return tuple(checks)


def discover_alembic_heads() -> tuple[str, ...]:
    repository_root = Path(__file__).resolve().parents[4]
    config_path = repository_root / "alembic.ini"
    if not config_path.is_file():
        return ()
    try:
        config = Config(str(config_path))
        config.set_main_option("script_location", str(repository_root / "migrations"))
        return tuple(ScriptDirectory.from_config(config).get_heads())
    except Exception:
        logger.exception("failed to inspect alembic heads")
        return ()
