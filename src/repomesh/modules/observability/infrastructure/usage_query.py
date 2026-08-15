"""Read-side aggregation over ``observability.llm_usage``.

The two endpoints the observability page needs:
- ``summary``: a system-level dashboard over a trailing window (calls, token
  totals, latency incl. p50/p95, per-model cost estimate, per-discovery-step
  splits, a daily trend, and the most recent failed calls);
- ``issues``: an issue-level roll-up so the page can rank issues by how much
  inference they consumed.

SQL is plain SQLAlchemy over the model. Bucketing by calendar date uses
``func.date`` which is portable across PostgreSQL and the SQLite test driver.
Percentiles are computed in Python with linear interpolation (same as
PostgreSQL's ``percentile_cont``) because SQLite has no percentile aggregate.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select

from repomesh.persistence import Database

from .models import LLMUsageRecord

# Estimated list prices in USD per 1M tokens. Real invoices vary by gateway /
# vendor — this is a calibration constant for the "评估与优化" story, not a
# meter reading. Unknown models fall back so cost never silently under-reports.
MODEL_PRICE_USD_PER_1M: dict[str, float] = {
    "gemini-3-flash": 0.35,
    "gemini-3-pro": 1.25,
    "deepseek-chat": 0.27,
    "deepseek-reasoner": 0.55,
}
DEFAULT_PRICE_USD_PER_1M = 0.5


def _model_price_usd_per_1m(model: str) -> float:
    return MODEL_PRICE_USD_PER_1M.get(model, DEFAULT_PRICE_USD_PER_1M)


def _cost_usd(prompt_tokens: int, completion_tokens: int, model: str) -> float:
    return (prompt_tokens + completion_tokens) / 1_000_000 * _model_price_usd_per_1m(model)


def _percentile(sorted_values: list[float], q: float) -> float | None:
    """Linear-interpolated percentile (matches PostgreSQL ``percentile_cont``)."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return round(sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac, 1)


class UsageQueryStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def summary(self, *, days: int) -> dict:
        """Dashboard over the trailing ``days`` (>= 1)."""
        since = datetime.now(UTC) - timedelta(days=max(days, 1))
        async with self._database.transaction() as session:
            ok_flag = case((LLMUsageRecord.status == "ok", 1), else_=0)
            totals = await session.execute(
                select(
                    func.count(LLMUsageRecord.id),
                    func.coalesce(func.sum(LLMUsageRecord.prompt_tokens), 0),
                    func.coalesce(func.sum(LLMUsageRecord.completion_tokens), 0),
                    func.coalesce(func.sum(LLMUsageRecord.total_tokens), 0),
                    func.avg(LLMUsageRecord.latency_ms),
                    func.min(LLMUsageRecord.created_at),
                    func.max(LLMUsageRecord.created_at),
                    func.coalesce(func.sum(ok_flag), 0),
                ).where(LLMUsageRecord.created_at >= since)
            )
            row = totals.one()
            calls, prompt_tokens, completion_tokens, total_tokens = row[:4]
            avg_latency, first_at, last_at, success_calls = row[4:]
            success_calls = int(success_calls)
            error_calls = calls - success_calls

            lat_rows = await session.execute(
                select(LLMUsageRecord.latency_ms).where(
                    LLMUsageRecord.created_at >= since,
                    LLMUsageRecord.latency_ms.is_not(None),
                )
            )
            latencies = sorted(float(v) for (v,) in lat_rows)

            by_model = await session.execute(
                select(
                    LLMUsageRecord.model,
                    func.count(LLMUsageRecord.id),
                    func.coalesce(func.sum(LLMUsageRecord.prompt_tokens), 0),
                    func.coalesce(func.sum(LLMUsageRecord.completion_tokens), 0),
                )
                .where(LLMUsageRecord.created_at >= since)
                .group_by(LLMUsageRecord.model)
                .order_by(func.count(LLMUsageRecord.id).desc())
            )
            by_step = await session.execute(
                select(
                    LLMUsageRecord.discovery_step,
                    func.count(LLMUsageRecord.id),
                    func.coalesce(func.sum(LLMUsageRecord.prompt_tokens), 0),
                    func.coalesce(func.sum(LLMUsageRecord.completion_tokens), 0),
                )
                .where(LLMUsageRecord.created_at >= since)
                .group_by(LLMUsageRecord.discovery_step)
                # NULLs sort first in SQLite but last in PostgreSQL; coalesce
                # pins "outside discovery" to the same position everywhere.
                .order_by(func.coalesce(LLMUsageRecord.discovery_step, 999))
            )
            daily = await session.execute(
                select(
                    func.date(LLMUsageRecord.created_at),
                    func.count(LLMUsageRecord.id),
                    func.coalesce(func.sum(LLMUsageRecord.prompt_tokens), 0),
                    func.coalesce(func.sum(LLMUsageRecord.completion_tokens), 0),
                )
                .where(LLMUsageRecord.created_at >= since)
                .group_by(func.date(LLMUsageRecord.created_at))
                .order_by(func.date(LLMUsageRecord.created_at))
            )
            recent = await session.execute(
                select(
                    LLMUsageRecord.created_at,
                    LLMUsageRecord.model,
                    LLMUsageRecord.operation,
                    LLMUsageRecord.finish_reason,
                    LLMUsageRecord.prompt_tokens,
                    LLMUsageRecord.completion_tokens,
                    LLMUsageRecord.latency_ms,
                )
                .where(
                    LLMUsageRecord.created_at >= since,
                    LLMUsageRecord.status != "ok",
                )
                .order_by(LLMUsageRecord.created_at.desc())
                .limit(5)
            )

        model_metrics = [
            {
                "model": model,
                "calls": int(count),
                "prompt_tokens": int(prompt),
                "completion_tokens": int(completion),
                "estimated_cost_usd": round(
                    _cost_usd(int(prompt), int(completion), model), 6
                ),
            }
            for model, count, prompt, completion in by_model
        ]
        return {
            "calls": int(calls),
            "success_calls": success_calls,
            "error_calls": error_calls,
            "success_rate": round(success_calls / calls, 4) if calls else None,
            "prompt_tokens": int(prompt_tokens),
            "completion_tokens": int(completion_tokens),
            "total_tokens": int(total_tokens),
            "estimated_cost_usd": round(sum(m["estimated_cost_usd"] for m in model_metrics), 6),
            "avg_latency_ms": round(float(avg_latency), 1) if avg_latency is not None else None,
            "latency_p50_ms": _percentile(latencies, 0.50),
            "latency_p95_ms": _percentile(latencies, 0.95),
            "first_usage_at": first_at,
            "last_usage_at": last_at,
            "by_model": model_metrics,
            "by_step": [
                {
                    "step": step,
                    "calls": int(count),
                    "prompt_tokens": int(prompt),
                    "completion_tokens": int(completion),
                }
                for step, count, prompt, completion in by_step
            ],
            "daily": [
                {
                    "date": str(day),
                    "calls": int(count),
                    "prompt_tokens": int(prompt),
                    "completion_tokens": int(completion),
                }
                for day, count, prompt, completion in daily
            ],
            "recent_errors": [
                {
                    "created_at": created_at,
                    "model": model,
                    "operation": operation,
                    "finish_reason": finish_reason,
                    "prompt_tokens": int(prompt),
                    "completion_tokens": int(completion),
                    "latency_ms": round(float(latency), 1) if latency is not None else None,
                }
                for created_at, model, operation, finish_reason, prompt, completion, latency
                in recent
            ],
        }

    async def window_metrics(self, *, minutes: int) -> dict:
        """Compact metric snapshot over a trailing window (for alert rules).

        Returns the fields alert rules can evaluate against; ``None`` means
        "no data in the window", which the evaluator treats as "not firing"
        rather than a broken rule.
        """
        since = datetime.now(UTC) - timedelta(minutes=max(minutes, 1))
        async with self._database.transaction() as session:
            ok_flag = case((LLMUsageRecord.status == "ok", 1), else_=0)
            totals = await session.execute(
                select(
                    func.count(LLMUsageRecord.id),
                    func.coalesce(func.sum(ok_flag), 0),
                    func.coalesce(func.sum(LLMUsageRecord.prompt_tokens), 0),
                    func.coalesce(func.sum(LLMUsageRecord.completion_tokens), 0),
                ).where(LLMUsageRecord.created_at >= since)
            )
            calls, success_calls, prompt_tokens, completion_tokens = totals.one()
            calls = int(calls)
            success_calls = int(success_calls)

            lat_rows = await session.execute(
                select(LLMUsageRecord.latency_ms).where(
                    LLMUsageRecord.created_at >= since,
                    LLMUsageRecord.latency_ms.is_not(None),
                )
            )
            latencies = sorted(float(v) for (v,) in lat_rows)

            cost_rows = await session.execute(
                select(
                    LLMUsageRecord.model,
                    func.coalesce(func.sum(LLMUsageRecord.prompt_tokens), 0),
                    func.coalesce(func.sum(LLMUsageRecord.completion_tokens), 0),
                )
                .where(LLMUsageRecord.created_at >= since)
                .group_by(LLMUsageRecord.model)
            )
        total_cost = 0.0
        for model, prompt, completion in cost_rows:
            total_cost += _cost_usd(int(prompt), int(completion), model)
        return {
            "calls": calls,
            "success_rate": round(success_calls / calls, 4) if calls else None,
            "error_count": calls - success_calls,
            "latency_p95_ms": _percentile(latencies, 0.95),
            "estimated_cost_usd": round(total_cost, 6) if calls else None,
        }

    async def issues(self, *, limit: int = 100) -> list[dict]:
        """Per-issue roll-up, most recently active first.

        Rows without an issue (no ambient context) are excluded — the page's
        issue table has no row to put them in, and the system dashboard still
        counts them.
        """
        async with self._database.transaction() as session:
            rows = await session.execute(
                select(
                    LLMUsageRecord.issue_id,
                    func.count(LLMUsageRecord.id),
                    func.coalesce(func.sum(LLMUsageRecord.prompt_tokens), 0),
                    func.coalesce(func.sum(LLMUsageRecord.completion_tokens), 0),
                    func.coalesce(func.sum(LLMUsageRecord.total_tokens), 0),
                    func.avg(LLMUsageRecord.latency_ms),
                    func.max(LLMUsageRecord.created_at),
                )
                .where(LLMUsageRecord.issue_id.is_not(None))
                .group_by(LLMUsageRecord.issue_id)
                .order_by(func.max(LLMUsageRecord.created_at).desc())
                .limit(max(1, min(limit, 500)))
            )
            # Cost is per model, so the issue roll-up needs a second grouping.
            cost_rows = await session.execute(
                select(
                    LLMUsageRecord.issue_id,
                    LLMUsageRecord.model,
                    func.coalesce(func.sum(LLMUsageRecord.prompt_tokens), 0),
                    func.coalesce(func.sum(LLMUsageRecord.completion_tokens), 0),
                )
                .where(LLMUsageRecord.issue_id.is_not(None))
                .group_by(LLMUsageRecord.issue_id, LLMUsageRecord.model)
            )
        issue_cost: dict = {}
        for issue_id, model, prompt, completion in cost_rows:
            issue_cost.setdefault(issue_id, 0.0)
            issue_cost[issue_id] += _cost_usd(int(prompt), int(completion), model)
        return [
            {
                "issue_id": issue_id,
                "calls": int(count),
                "prompt_tokens": int(prompt),
                "completion_tokens": int(completion),
                "total_tokens": int(total),
                "estimated_cost_usd": round(issue_cost.get(issue_id, 0.0), 6),
                "avg_latency_ms": round(float(avg_latency), 1) if avg_latency is not None else None,
                "last_usage_at": last_at,
            }
            for issue_id, count, prompt, completion, total, avg_latency, last_at in rows
        ]
