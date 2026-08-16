"""Alert rules, evaluation, and the console alert endpoints."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from repomesh.bootstrap.app import create_app
from repomesh.bootstrap.container import ApplicationContainer
from repomesh.modules.observability.infrastructure.models import (
    LLMUsageRecord,
    TraceEventRecord,
    TraceSessionRecord,
)
from repomesh.settings import get_settings

_TOKEN = "internal-secret"
_HEADERS = {"Authorization": f"Bearer {_TOKEN}"}


def _seed_call(
    container: ApplicationContainer,
    *,
    status: str = "ok",
    latency_ms: int | None = 300,
    minutes_ago: int = 1,
) -> None:
    async def _insert() -> None:
        async with container.database.transaction() as session:
            session.add(
                LLMUsageRecord(
                    id=uuid4(),
                    created_at=datetime.now(UTC) - timedelta(minutes=minutes_ago),
                    provider="deepseek",
                    model="deepseek-chat",
                    operation="chat",
                    prompt_tokens=100,
                    completion_tokens=20,
                    total_tokens=120,
                    finish_reason="stop" if status == "ok" else None,
                    latency_ms=latency_ms,
                    status=status,
                )
            )

    asyncio.run(_insert())


def _seed_trace(
    container: ApplicationContainer,
    *,
    status: str = "ok",
    minutes_ago: int = 1,
) -> None:
    """One trace session with a single terminal event (for trace alert rules)."""

    async def _insert() -> None:
        async with container.database.transaction() as session:
            session_row_id = uuid4()
            session.add(
                TraceSessionRecord(
                    id=session_row_id,
                    session_id=f"trace-{session_row_id.hex[:8]}",
                    agent_name="agt-trace",
                    runtime="openclaw",
                    source_key=(
                        f"agents/agt-trace/.openclaw/agents/main/sessions/"
                        f"trace-{session_row_id.hex[:8]}.jsonl"
                    ),
                    object_mtime=datetime.now(UTC),
                    object_size=1,
                    first_seen_at=datetime.now(UTC),
                    parsed_at=datetime.now(UTC),
                    parsing_error=None,
                    event_count=1,
                )
            )
            session.add(
                TraceEventRecord(
                    id=uuid4(),
                    session_id=session_row_id,
                    seq=1,
                    ts=datetime.now(UTC) - timedelta(minutes=minutes_ago),
                    event_type="tool",
                    name="execute_shell_command",
                    role="tool",
                    summary="seed",
                    status=status,
                    payload=None,
                )
            )

    asyncio.run(_insert())


def _client(container: ApplicationContainer, monkeypatch) -> TestClient:
    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", _TOKEN)
    get_settings.cache_clear()
    return TestClient(create_app(container))


def test_alert_endpoints_require_the_action_token(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    client = _client(application_container, monkeypatch)
    assert client.get("/api/v1/observe/alerts").status_code == 401
    assert client.get("/api/v1/observe/alerts/active").status_code == 401
    assert client.get("/api/v1/observe/alert-rules").status_code == 401


def test_default_rules_are_seeded_on_first_listing(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    client = _client(application_container, monkeypatch)
    rules = client.get("/api/v1/observe/alert-rules", headers=_HEADERS)
    assert rules.status_code == 200
    body = rules.json()["rules"]
    assert len(body) == 3
    by_metric = {r["metric"]: r for r in body}
    assert by_metric["success_rate"]["operator"] == "lt"
    assert by_metric["success_rate"]["threshold"] == 0.8
    assert by_metric["error_count"]["operator"] == "gt"
    assert by_metric["latency_p95_ms"]["operator"] == "gt"


def test_alert_rule_crud_and_validation(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    client = _client(application_container, monkeypatch)

    created = client.post(
        "/api/v1/observe/alert-rules",
        headers=_HEADERS,
        json={
            "name": "测试成本超限",
            "metric": "estimated_cost_usd",
            "operator": "gt",
            "threshold": 0.01,
            "window_minutes": 60,
        },
    )
    assert created.status_code == 201
    rule = created.json()
    assert rule["metric"] == "estimated_cost_usd"
    assert rule["window_minutes"] == 60

    updated = client.put(
        f"/api/v1/observe/alert-rules/{rule['id']}",
        headers=_HEADERS,
        json={"threshold": 0.5, "enabled": False},
    )
    assert updated.status_code == 200
    assert updated.json()["threshold"] == 0.5
    assert updated.json()["enabled"] is False

    # Unsupported metric / missing name are rejected.
    bad_metric = client.post(
        "/api/v1/observe/alert-rules",
        headers=_HEADERS,
        json={"name": "x", "metric": "tokens", "operator": "gt", "threshold": 1},
    )
    assert bad_metric.status_code == 422
    no_name = client.post(
        "/api/v1/observe/alert-rules",
        headers=_HEADERS,
        json={"metric": "calls", "operator": "gt", "threshold": 1},
    )
    assert no_name.status_code == 422

    deleted = client.delete(
        f"/api/v1/observe/alert-rules/{rule['id']}", headers=_HEADERS
    )
    assert deleted.status_code == 204
    missing = client.delete(
        f"/api/v1/observe/alert-rules/{rule['id']}", headers=_HEADERS
    )
    assert missing.status_code == 404


def test_evaluator_fires_and_resolves(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    # One error out of one call → success_rate 0.0.
    _seed_call(application_container, status="error")
    client = _client(application_container, monkeypatch)

    rule = client.post(
        "/api/v1/observe/alert-rules",
        headers=_HEADERS,
        json={
            "name": "成功率过低(测试)",
            "metric": "success_rate",
            "operator": "lt",
            "threshold": 0.8,
            "window_minutes": 1440,
        },
    ).json()

    # The console "evaluate now" endpoint fires the alert.
    fired = client.post("/api/v1/observe/alerts/evaluate", headers=_HEADERS)
    assert fired.status_code == 200
    active = fired.json()["events"]
    assert len(active) == 1
    assert active[0]["rule_id"] == rule["id"]
    assert active[0]["status"] == "firing"
    assert "成功率" in active[0]["message"]

    # A second pass does not create a duplicate firing event.
    fired_again = client.post("/api/v1/observe/alerts/evaluate", headers=_HEADERS)
    assert len(fired_again.json()["events"]) == 1

    # Recovers: 10 ok + 1 error → success_rate 0.909 >= 0.8 → resolved.
    for _ in range(10):
        _seed_call(application_container, status="ok")
    recovered = client.post("/api/v1/observe/alerts/evaluate", headers=_HEADERS)
    assert recovered.json()["events"] == []

    history = client.get("/api/v1/observe/alerts?days=7", headers=_HEADERS)
    events = history.json()["events"]
    assert len(events) == 1
    assert events[0]["status"] == "resolved"
    assert events[0]["resolved_at"] is not None


def test_evaluator_skips_empty_windows(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    client = _client(application_container, monkeypatch)
    client.post(
        "/api/v1/observe/alert-rules",
        headers=_HEADERS,
        json={
            "name": "成功率过低(空窗口)",
            "metric": "success_rate",
            "operator": "lt",
            "threshold": 0.8,
        },
    )
    # No usage rows at all → unknown, not firing.
    fired = client.post("/api/v1/observe/alerts/evaluate", headers=_HEADERS)
    assert fired.json()["events"] == []
    assert client.get("/api/v1/observe/alerts", headers=_HEADERS).json()["events"] == []


# ---------------------------------------------------------------------------
# trace_* metrics: trace events feed the same rule engine
# ---------------------------------------------------------------------------


def test_trace_success_rate_rule_fires_and_resolves(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    client = _client(application_container, monkeypatch)
    _seed_trace(application_container, status="error")

    rule = client.post(
        "/api/v1/observe/alert-rules",
        headers=_HEADERS,
        json={
            "name": "trace 成功率过低(测试)",
            "metric": "trace_success_rate",
            "operator": "lt",
            "threshold": 0.8,
            "window_minutes": 1440,
        },
    ).json()
    assert rule["metric"] == "trace_success_rate"

    fired = client.post("/api/v1/observe/alerts/evaluate", headers=_HEADERS)
    assert fired.status_code == 200
    events = fired.json()["events"]
    assert len(events) == 1
    assert events[0]["rule_id"] == rule["id"]
    assert events[0]["status"] == "firing"
    assert "trace 成功率" in events[0]["message"]

    # A second pass returns the same active event, without creating a duplicate.
    again = client.post("/api/v1/observe/alerts/evaluate", headers=_HEADERS).json()
    assert len(again["events"]) == 1
    history = client.get("/api/v1/observe/alerts?days=7", headers=_HEADERS).json()
    assert len(history["events"]) == 1

    # 窗口补进 10 个 ok 事件 → 成功率 10/11 ≈ 0.909 ≥ 0.8 → resolved。
    for _ in range(10):
        _seed_trace(application_container, status="ok")
    recovered = client.post("/api/v1/observe/alerts/evaluate", headers=_HEADERS).json()
    assert recovered["events"] == []
    active_after = client.get("/api/v1/observe/alerts/active", headers=_HEADERS).json()
    assert active_after["events"] == []
    history = client.get("/api/v1/observe/alerts?days=7", headers=_HEADERS).json()
    assert len(history["events"]) == 1
    assert history["events"][0]["status"] == "resolved"
    assert history["events"][0]["resolved_at"] is not None


def test_trace_error_count_rule_fires(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    client = _client(application_container, monkeypatch)
    for _ in range(3):
        _seed_trace(application_container, status="error")

    rule = client.post(
        "/api/v1/observe/alert-rules",
        headers=_HEADERS,
        json={
            "name": "trace 错误数过多(测试)",
            "metric": "trace_error_count",
            "operator": "gt",
            "threshold": 2,
            "window_minutes": 1440,
        },
    ).json()
    events = client.post("/api/v1/observe/alerts/evaluate", headers=_HEADERS).json()[
        "events"
    ]
    assert len(events) == 1
    assert events[0]["rule_id"] == rule["id"]
    assert "trace 错误数" in events[0]["message"]


def test_trace_rule_without_trace_data_is_unknown(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    """窗口内没有 trace 数据 → 指标为 None → 规则视为 unknown，不触发。"""
    client = _client(application_container, monkeypatch)
    client.post(
        "/api/v1/observe/alert-rules",
        headers=_HEADERS,
        json={
            "name": "trace 空窗口(测试)",
            "metric": "trace_error_count",
            "operator": "gt",
            "threshold": 0,
            "window_minutes": 1440,
        },
    )
    fired = client.post("/api/v1/observe/alerts/evaluate", headers=_HEADERS)
    assert fired.status_code == 200
    assert fired.json()["events"] == []
