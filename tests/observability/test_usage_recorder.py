"""LLM usage: recorder threading semantics, query aggregation, deepseek sink.

The recorder's contract is the awkward one: ``record`` runs on
``asyncio.to_thread`` worker threads while the flush runs on the event loop,
so these tests exercise that exact shape — usage recorded *from a worker
thread* lands in the database through the background task, and the ambient
usage context survives the two context copies (create_task then to_thread)
that production relies on.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from repomesh.integrations.llm.deepseek import DeepSeekClient, DeepSeekConfig
from repomesh.modules.observability.contracts import UsageContext, current_usage_context
from repomesh.modules.observability.infrastructure.models import LLMUsageRecord
from repomesh.modules.observability.infrastructure.usage_query import UsageQueryStore
from repomesh.modules.observability.infrastructure.usage_recorder import QueuedUsageRecorder
from repomesh.persistence import Database
from repomesh.persistence.base import ALL_SCHEMAS


@pytest.fixture
async def database(tmp_path: Path) -> Database:
    instance = Database(
        f"sqlite+aiosqlite:///{tmp_path / 'usage.db'}",
        schema_translate_map={schema: None for schema in ALL_SCHEMAS},
    )
    await instance.create_all_for_tests()
    yield instance
    await instance.dispose()


async def _count(database: Database) -> int:
    async with database.transaction() as session:
        return int(
            (
                await session.execute(select(func.count(LLMUsageRecord.id)))
            ).scalar_one()
        )


async def _single_row(database: Database) -> LLMUsageRecord:
    async with database.transaction() as session:
        row = await session.scalar(select(LLMUsageRecord))
    assert row is not None
    return row


async def test_usage_recorded_from_a_worker_thread_is_flushed(database: Database) -> None:
    recorder = QueuedUsageRecorder(database, flush_interval_seconds=0.05)
    await recorder.start()
    try:
        for index in range(3):
            # to_thread is exactly how the discovery chain calls chat().
            await asyncio.to_thread(
                recorder.record,
                {
                    "provider": "deepseek",
                    "model": "deepseek-chat",
                    "operation": "chat",
                    "prompt_tokens": 100 + index,
                    "completion_tokens": 20,
                    "total_tokens": 120 + index,
                    "finish_reason": "stop",
                    "latency_ms": 350,
                    "status": "ok",
                },
            )
        await asyncio.sleep(0.2)
        assert await _count(database) == 3
    finally:
        await recorder.close()


async def test_ambient_context_survives_thread_boundary(database: Database) -> None:
    issue_id, step = uuid4(), 3
    recorder = QueuedUsageRecorder(database, flush_interval_seconds=0.05)
    await recorder.start()
    try:
        token = current_usage_context.set(UsageContext(issue_id=issue_id, step=step))
        try:
            # Set on the loop, read on the worker thread: the same path the
            # discovery endpoints use (create_task copies, to_thread copies).
            await asyncio.to_thread(
                recorder.record,
                {
                    "provider": "deepseek",
                    "model": "m",
                    "operation": "chat",
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                    "status": "ok",
                },
            )
        finally:
            current_usage_context.reset(token)
        await asyncio.sleep(0.15)
        row = await _single_row(database)
        assert row.issue_id == issue_id
        assert row.discovery_step == step
    finally:
        await recorder.close()


async def test_full_queue_drops_without_blocking(database: Database) -> None:
    # No background flush running: the queue must absorb exactly max_queue
    # records and drop the rest without the caller ever blocking.
    recorder = QueuedUsageRecorder(database, flush_interval_seconds=0.05, max_queue=1)
    for _ in range(3):
        await asyncio.to_thread(
            recorder.record,
            {
                "provider": "deepseek",
                "model": "m",
                "operation": "chat",
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
                "status": "ok",
            },
        )
    assert recorder.dropped == 2
    await recorder.close()


async def test_query_summary_and_issues_aggregate(database: Database) -> None:
    now = datetime.now(UTC)
    issue_a, issue_b = uuid4(), uuid4()
    async with database.transaction() as session:
        session.add_all(
            [
                _row(issue_a, step=1, model="deepseek-chat", prompt=100, completion=20,
                     latency=300, at=now - timedelta(hours=2)),
                _row(issue_a, step=2, model="deepseek-chat", prompt=50, completion=10,
                     latency=400, at=now - timedelta(hours=1)),
                _row(issue_b, step=2, model="deepseek-reasoner", prompt=200, completion=40,
                     latency=500, at=now - timedelta(hours=3)),
                _row(None, step=None, model="deepseek-chat", prompt=10, completion=5,
                     latency=100, at=now - timedelta(hours=4)),
            ]
        )

    store = UsageQueryStore(database)
    summary = await store.summary(days=7)
    assert summary["calls"] == 4
    assert summary["prompt_tokens"] == 360
    assert summary["completion_tokens"] == 75
    assert summary["total_tokens"] == 435
    assert summary["avg_latency_ms"] == pytest.approx(325.0)
    assert [m["model"] for m in summary["by_model"]] == [
        "deepseek-chat",
        "deepseek-reasoner",
    ]
    assert summary["by_model"][0]["calls"] == 3
    # step 1, step 2, then the unlabelled row (coalesced last).
    assert [(s["step"], s["calls"]) for s in summary["by_step"]] == [
        (1, 1),
        (2, 2),
        (None, 1),
    ]
    assert len(summary["daily"]) == 1
    assert summary["daily"][0]["calls"] == 4

    issues = await store.issues()
    assert [str(i["issue_id"]) for i in issues] == [str(issue_a), str(issue_b)]
    assert issues[0]["calls"] == 2
    assert issues[0]["total_tokens"] == 180


def _row(issue_id, *, step, model, prompt, completion, latency, at) -> LLMUsageRecord:
    return LLMUsageRecord(
        id=uuid4(),
        created_at=at,
        provider="deepseek",
        model=model,
        operation="chat",
        issue_id=issue_id,
        discovery_step=step,
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        finish_reason="stop",
        latency_ms=latency,
        status="ok",
    )


class _FakeResponse:
    def __init__(self, payload: dict, *, error: bool = False) -> None:
        self._payload = payload
        self._error = error

    def raise_for_status(self) -> None:
        if self._error:
            raise RuntimeError("deepseek unreachable")

    def json(self) -> dict:
        return self._payload


def _payload(prompt: int = 42, completion: int = 7, content: str = "plan: abc") -> dict:
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        },
    }


async def test_deepseek_chat_reports_usage_to_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(
        "repomesh.integrations.llm.deepseek.httpx.post",
        lambda *a, **k: _FakeResponse(_payload()),
    )
    client = DeepSeekClient(
        DeepSeekConfig(api_key="k", model="deepseek-chat"),
        usage_sink=captured.append,
    )
    result = client.chat([{"role": "user", "content": "hi"}])
    assert result == "plan: abc"
    assert len(captured) == 1
    usage = captured[0]
    assert usage["provider"] == "deepseek"
    assert usage["model"] == "deepseek-chat"
    assert usage["operation"] == "chat"
    assert usage["prompt_tokens"] == 42
    assert usage["completion_tokens"] == 7
    assert usage["total_tokens"] == 49
    assert usage["finish_reason"] == "stop"
    assert usage["status"] == "ok"
    assert isinstance(usage["latency_ms"], int)


async def test_deepseek_chat_reports_failures_to_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(
        "repomesh.integrations.llm.deepseek.httpx.post",
        lambda *a, **k: _FakeResponse({}, error=True),
    )
    client = DeepSeekClient(DeepSeekConfig(api_key="k"), usage_sink=captured.append)
    with pytest.raises(RuntimeError):
        client.chat([{"role": "user", "content": "hi"}])
    assert len(captured) == 1
    assert captured[0]["status"] == "error"
    assert captured[0]["prompt_tokens"] == 0


async def test_deepseek_sink_attaches_ambient_context(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    issue_id, step = uuid4(), 4
    monkeypatch.setattr(
        "repomesh.integrations.llm.deepseek.httpx.post",
        lambda *a, **k: _FakeResponse(_payload(content="x")),
    )
    recorder = QueuedUsageRecorder(database, flush_interval_seconds=0.05)
    await recorder.start()
    try:
        # The production shape: the sink writes straight into the recorder,
        # which reads the ambient context in the worker thread where chat()
        # runs. Set on the loop, copied into the thread by to_thread.
        client = DeepSeekClient(DeepSeekConfig(api_key="k"), usage_sink=recorder.record)
        token = current_usage_context.set(UsageContext(issue_id=issue_id, step=step))
        try:
            await asyncio.to_thread(client.chat, [{"role": "user", "content": "hi"}])
        finally:
            current_usage_context.reset(token)
        await asyncio.sleep(0.15)
        row = await _single_row(database)
        assert row.issue_id == issue_id
        assert row.discovery_step == step
        assert row.model == "deepseek-chat"
        assert row.provider == "deepseek"
    finally:
        await recorder.close()
