"""Alert rules + evaluation over usage/trace window metrics.

Threshold rules are evaluated on a trailing window of data. Metrics with a
``trace_`` prefix read ``observability.trace_events`` (via a trace query
store); all other metrics read ``observability.llm_usage``. A rule fires when
its metric crosses the threshold, creating a ``firing`` event; when the window
recovers, the evaluator resolves the event. The evaluator runs both as a
background service (the online-monitoring story) and on demand (the console
"evaluate now" button, and tests).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update

from repomesh.persistence import Database

from .models import AlertEventRecord, AlertRuleRecord

logger = logging.getLogger(__name__)

#: Human-readable labels for rule messages.
METRIC_LABELS: dict[str, str] = {
    "calls": "调用次数",
    "success_rate": "成功率",
    "error_count": "错误次数",
    "latency_p95_ms": "P95 延迟",
    "estimated_cost_usd": "估算成本",
    # trace-event metrics (evaluated against trace_events, not llm_usage)
    "trace_calls": "trace 事件数",
    "trace_success_rate": "trace 成功率",
    "trace_error_count": "trace 错误数",
}
OPERATOR_LABELS: dict[str, str] = {"lt": "低于", "gt": "高于"}

#: Created once when the rules table is empty, so a fresh deployment gets a
#: sensible baseline without hard-coding it in a migration.
DEFAULT_RULES: tuple[dict, ...] = (
    {
        "name": "成功率过低",
        "metric": "success_rate",
        "operator": "lt",
        "threshold": 0.8,
        "window_minutes": 1440,
    },
    {
        "name": "错误数过多",
        "metric": "error_count",
        "operator": "gt",
        "threshold": 10,
        "window_minutes": 1440,
    },
    {
        "name": "P95 延迟过高",
        "metric": "latency_p95_ms",
        "operator": "gt",
        "threshold": 30_000,
        "window_minutes": 1440,
    },
)

SUPPORTED_METRICS = frozenset(METRIC_LABELS)
SUPPORTED_OPERATORS = frozenset(OPERATOR_LABELS)


def _rule_to_dict(rule: AlertRuleRecord) -> dict:
    return {
        "id": rule.id,
        "name": rule.name,
        "metric": rule.metric,
        "operator": rule.operator,
        "threshold": rule.threshold,
        "window_minutes": rule.window_minutes,
        "enabled": rule.enabled,
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
    }


def _event_to_dict(event: AlertEventRecord, rule_name: str | None = None) -> dict:
    return {
        "id": event.id,
        "rule_id": event.rule_id,
        "rule_name": rule_name,
        "status": event.status,
        "message": event.message,
        "value": event.value,
        "window_minutes": event.window_minutes,
        "triggered_at": event.triggered_at,
        "resolved_at": event.resolved_at,
    }


def _violates(value: float, operator: str, threshold: float) -> bool:
    return value < threshold if operator == "lt" else value > threshold


def _message(rule: dict, value: float) -> str:
    metric = METRIC_LABELS.get(rule["metric"], rule["metric"])
    op = OPERATOR_LABELS.get(rule["operator"], rule["operator"])
    return f"{rule['name']}：{metric} {value} {op} 阈值 {rule['threshold']}"


class AlertingStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def ensure_default_rules(self) -> None:
        async with self._database.transaction() as session:
            exists = await session.execute(
                select(AlertRuleRecord.id).limit(1)
            )
            if exists.first() is not None:
                return
            session.add_all(
                AlertRuleRecord(
                    id=uuid4(),
                    name=str(rule["name"]),
                    metric=str(rule["metric"]),
                    operator=str(rule["operator"]),
                    threshold=float(rule["threshold"]),
                    window_minutes=int(rule["window_minutes"]),
                    enabled=True,
                )
                for rule in DEFAULT_RULES
            )

    async def list_rules(self, *, enabled_only: bool = False) -> list[dict]:
        async with self._database.transaction() as session:
            stmt = select(AlertRuleRecord).order_by(AlertRuleRecord.created_at)
            if enabled_only:
                stmt = stmt.where(AlertRuleRecord.enabled.is_(True))
            rows = (await session.execute(stmt)).scalars().all()
            return [_rule_to_dict(r) for r in rows]

    async def get_rule(self, rule_id: UUID) -> dict | None:
        async with self._database.transaction() as session:
            rule = await session.get(AlertRuleRecord, rule_id)
            return _rule_to_dict(rule) if rule else None

    async def create_rule(
        self,
        *,
        name: str,
        metric: str,
        operator: str,
        threshold: float,
        window_minutes: int = 1440,
        enabled: bool = True,
    ) -> dict:
        rule = AlertRuleRecord(
            id=uuid4(),
            name=name,
            metric=metric,
            operator=operator,
            threshold=float(threshold),
            window_minutes=int(window_minutes),
            enabled=enabled,
        )
        async with self._database.transaction() as session:
            session.add(rule)
            await session.flush()
            return _rule_to_dict(rule)

    async def update_rule(self, rule_id: UUID, **fields) -> dict | None:
        allowed = {"name", "metric", "operator", "threshold",
                   "window_minutes", "enabled"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return await self.get_rule(rule_id)
        updates["updated_at"] = datetime.now(UTC)
        async with self._database.transaction() as session:
            result = await session.execute(
                update(AlertRuleRecord)
                .where(AlertRuleRecord.id == rule_id)
                .values(**updates)
            )
            if result.rowcount == 0:
                return None
            rule = await session.get(AlertRuleRecord, rule_id)
            return _rule_to_dict(rule) if rule else None

    async def delete_rule(self, rule_id: UUID) -> bool:
        async with self._database.transaction() as session:
            result = await session.execute(
                delete(AlertRuleRecord).where(AlertRuleRecord.id == rule_id)
            )
            return bool(result.rowcount)

    async def active_event_for(self, rule_id: UUID) -> dict | None:
        """The unresolved ``firing`` event for a rule, if any."""
        async with self._database.transaction() as session:
            stmt = (
                select(AlertEventRecord)
                .where(
                    AlertEventRecord.rule_id == rule_id,
                    AlertEventRecord.status == "firing",
                    AlertEventRecord.resolved_at.is_(None),
                )
                .order_by(AlertEventRecord.triggered_at.desc())
                .limit(1)
            )
            event = (await session.execute(stmt)).scalar_one_or_none()
            return _event_to_dict(event) if event else None

    async def create_event(self, rule: dict, value: float) -> dict:
        event = AlertEventRecord(
            id=uuid4(),
            rule_id=rule["id"],
            status="firing",
            message=_message(rule, value),
            value=float(value),
            window_minutes=int(rule["window_minutes"]),
            triggered_at=datetime.now(UTC),
        )
        async with self._database.transaction() as session:
            session.add(event)
            await session.flush()
            return _event_to_dict(event, rule["name"])

    async def resolve_event(self, event_id: UUID) -> None:
        async with self._database.transaction() as session:
            await session.execute(
                update(AlertEventRecord)
                .where(AlertEventRecord.id == event_id)
                .values(status="resolved", resolved_at=datetime.now(UTC))
            )

    async def list_events(self, *, days: int = 7) -> list[dict]:
        since = datetime.now(UTC) - timedelta(days=max(days, 1))
        async with self._database.transaction() as session:
            rows = (
                await session.execute(
                    select(AlertEventRecord, AlertRuleRecord.name)
                    .join(AlertRuleRecord, AlertEventRecord.rule_id == AlertRuleRecord.id)
                    .where(AlertEventRecord.triggered_at >= since)
                    .order_by(AlertEventRecord.triggered_at.desc())
                    .limit(200)
                )
            ).all()
            return [
                _event_to_dict(event, name)
                for event, name in rows
            ]

    async def list_active_events(self) -> list[dict]:
        async with self._database.transaction() as session:
            rows = (
                await session.execute(
                    select(AlertEventRecord, AlertRuleRecord.name)
                    .join(AlertRuleRecord, AlertEventRecord.rule_id == AlertRuleRecord.id)
                    .where(
                        AlertEventRecord.status == "firing",
                        AlertEventRecord.resolved_at.is_(None),
                    )
                    .order_by(AlertEventRecord.triggered_at.desc())
                )
            ).all()
            return [
                _event_to_dict(event, name)
                for event, name in rows
            ]


class AlertingEvaluator:
    """Evaluate enabled rules on an interval and on demand."""

    def __init__(
        self,
        store: AlertingStore,
        usage_query,
        trace_query=None,
        transition_handler=None,
        *,
        interval_seconds: int = 60,
    ) -> None:
        self._store = store
        self._usage_query = usage_query
        self._trace_query = trace_query
        self._transition_handler = transition_handler
        self._interval = interval_seconds
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="alerting-evaluator")

    async def close(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def evaluate_now(self) -> None:
        """One synchronous pass — the console "evaluate now" and tests."""
        await self.evaluate_once()

    async def evaluate_once(self) -> None:
        await self._store.ensure_default_rules()
        rules = await self._store.list_rules(enabled_only=True)
        for rule in rules:
            try:
                await self._evaluate_rule(rule)
            except Exception:
                logger.exception("alert rule %s failed to evaluate", rule["id"])

    async def _evaluate_rule(self, rule: dict) -> None:
        metric = rule["metric"]
        if metric.startswith("trace_"):
            # trace_* rules read trace events; without a wired trace store the
            # rule is unknown (no data), never firing.
            if self._trace_query is None:
                return
            metrics = await self._trace_query.window_metrics(
                minutes=rule["window_minutes"]
            )
        else:
            metrics = await self._usage_query.window_metrics(
                minutes=rule["window_minutes"]
            )
        value = metrics.get(metric)
        # No data in the window is "unknown", not "firing" — an empty system
        # should not page the operator.
        if value is None:
            return
        firing = _violates(float(value), rule["operator"], rule["threshold"])
        active = await self._store.active_event_for(rule["id"])
        if firing and active is None:
            event = await self._store.create_event(rule, float(value))
            if self._transition_handler is not None:
                await self._transition_handler.firing(event)
        elif not firing and active is not None:
            await self._store.resolve_event(active["id"])
            if self._transition_handler is not None:
                await self._transition_handler.resolved(active["id"])

    async def _run(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._interval)
                try:
                    await self.evaluate_once()
                except Exception:
                    logger.exception("alerting evaluation cycle failed")
        except asyncio.CancelledError:
            pass
