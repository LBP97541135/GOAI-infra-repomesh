"""The console observability endpoints over a real app + SQLite container."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from repomesh.bootstrap.app import create_app
from repomesh.bootstrap.container import ApplicationContainer
from repomesh.modules.observability.infrastructure.models import (
    LLMUsageRecord,
    LogEntryRecord,
)
from repomesh.settings import get_settings

_TOKEN = "internal-secret"
_HEADERS = {"Authorization": f"Bearer {_TOKEN}"}


def _seed(container: ApplicationContainer) -> None:
    now = datetime.now(UTC)

    async def _insert() -> None:
        async with container.database.transaction() as session:
            session.add_all(
                [
                    LLMUsageRecord(
                        id=uuid4(),
                        created_at=now - timedelta(minutes=1),
                        provider="deepseek",
                        model="deepseek-chat",
                        operation="chat",
                        issue_id=uuid4(),
                        discovery_step=2,
                        prompt_tokens=100,
                        completion_tokens=20,
                        total_tokens=120,
                        finish_reason="stop",
                        latency_ms=300,
                        status="ok",
                    ),
                    LLMUsageRecord(
                        id=uuid4(),
                        created_at=now - timedelta(minutes=2),
                        provider="deepseek",
                        model="deepseek-chat",
                        operation="chat",
                        issue_id=None,
                        discovery_step=None,
                        prompt_tokens=10,
                        completion_tokens=5,
                        total_tokens=15,
                        finish_reason=None,
                        latency_ms=None,
                        status="error",
                    ),
                ]
            )

    asyncio.run(_insert())


def _seed_log(
    container: ApplicationContainer,
    *,
    issue_id,
    message: str = "discovery step log",
    minutes_ago: int = 1,
    level: str = "INFO",
) -> None:
    async def _insert() -> None:
        async with container.database.transaction() as session:
            session.add(
                LogEntryRecord(
                    id=uuid4(),
                    ts=datetime.now(UTC) - timedelta(minutes=minutes_ago),
                    level=level,
                    source="repomesh.modules.repository_intelligence",
                    issue_id=issue_id,
                    message=message,
                    exc_info=None,
                )
            )

    asyncio.run(_insert())


def _client(container: ApplicationContainer, monkeypatch) -> TestClient:
    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", _TOKEN)
    get_settings.cache_clear()
    return TestClient(create_app(container))


def test_observe_summary_requires_the_action_token(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    client = _client(application_container, monkeypatch)
    assert client.get("/api/v1/observe/summary").status_code == 401
    assert client.get("/api/v1/observe/issues").status_code == 401


def test_observe_summary_and_issues_over_the_seeded_database(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    _seed(application_container)
    client = _client(application_container, monkeypatch)

    summary = client.get("/api/v1/observe/summary?days=7", headers=_HEADERS)
    assert summary.status_code == 200
    body = summary.json()
    assert body["calls"] == 2
    assert body["success_calls"] == 1
    assert body["error_calls"] == 1
    assert body["success_rate"] == 0.5
    assert body["prompt_tokens"] == 110
    assert body["completion_tokens"] == 25
    assert body["total_tokens"] == 135
    # 135 tokens × deepseek-chat $0.27/1M = 0.00003645 → rounded to 6 dp.
    assert body["estimated_cost_usd"] == 0.000036
    assert body["avg_latency_ms"] == 300.0
    assert body["latency_p50_ms"] == 300.0
    assert body["latency_p95_ms"] == 300.0
    assert body["by_model"] == [
        {"model": "deepseek-chat", "calls": 2, "prompt_tokens": 110,
         "completion_tokens": 25, "estimated_cost_usd": 0.000036}
    ]
    assert [(s["step"], s["calls"]) for s in body["by_step"]] == [(2, 1), (None, 1)]
    assert body["daily"][0]["calls"] == 2
    # The failed call (finish_reason=None, latency=None) shows up in recent_errors.
    assert len(body["recent_errors"]) == 1
    err = body["recent_errors"][0]
    assert err["model"] == "deepseek-chat"
    assert err["finish_reason"] is None
    assert err["latency_ms"] is None

    issues = client.get("/api/v1/observe/issues", headers=_HEADERS)
    assert issues.status_code == 200
    rows = issues.json()["issues"]
    assert len(rows) == 1
    assert rows[0]["calls"] == 1
    assert rows[0]["prompt_tokens"] == 100
    # 120 tokens × deepseek-chat $0.27/1M = 0.0000324 → rounded to 6 dp.
    assert rows[0]["estimated_cost_usd"] == 0.000032
    assert rows[0]["avg_latency_ms"] == 300.0
    assert rows[0]["last_usage_at"] is not None


def test_observe_summary_rejects_out_of_range_windows(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    client = _client(application_container, monkeypatch)
    assert client.get("/api/v1/observe/summary?days=0", headers=_HEADERS).status_code == 422
    assert client.get("/api/v1/observe/summary?days=999", headers=_HEADERS).status_code == 422


def test_observe_log_issue_groups(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    issue_a, issue_b = uuid4(), uuid4()
    _seed_log(application_container, issue_id=issue_a, message="a", minutes_ago=2)
    _seed_log(application_container, issue_id=issue_b, message="b1", minutes_ago=1)
    _seed_log(application_container, issue_id=issue_b, message="b2", minutes_ago=3)
    _seed_log(application_container, issue_id=None, message="ambient", minutes_ago=0)
    client = _client(application_container, monkeypatch)

    response = client.get("/api/v1/observe/logs/issues", headers=_HEADERS)
    assert response.status_code == 200
    groups = response.json()["issues"]
    assert len(groups) == 2
    assert groups[0]["issue_id"] == str(issue_b)
    assert groups[0]["count"] == 2
    assert groups[1]["issue_id"] == str(issue_a)
    assert groups[1]["count"] == 1
