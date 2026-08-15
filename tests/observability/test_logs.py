"""Structured log capture pipeline, query store, and the /observe/logs API."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from repomesh.bootstrap.app import create_app
from repomesh.bootstrap.container import ApplicationContainer
from repomesh.modules.observability.contracts import UsageContext, current_usage_context
from repomesh.modules.observability.infrastructure.log_query import LogQueryStore
from repomesh.modules.observability.infrastructure.log_recorder import LogRecorder
from repomesh.modules.observability.infrastructure.models import LogEntryRecord
from repomesh.settings import get_settings

_TOKEN = "internal-secret"
_HEADERS = {"Authorization": f"Bearer {_TOKEN}"}


def _seed_log(
    container: ApplicationContainer,
    *,
    level: str = "INFO",
    source: str = "repomesh.modules.task_orchestration",
    issue_id=None,
    message: str = "seed message",
    exc_info: str | None = None,
    minutes_ago: int = 1,
) -> None:
    async def _insert() -> None:
        async with container.database.transaction() as session:
            session.add(
                LogEntryRecord(
                    id=uuid4(),
                    ts=datetime.now(UTC) - timedelta(minutes=minutes_ago),
                    level=level,
                    source=source,
                    issue_id=issue_id,
                    message=message,
                    exc_info=exc_info,
                )
            )

    asyncio.run(_insert())


async def _rows(container: ApplicationContainer) -> list[LogEntryRecord]:
    from sqlalchemy import select

    async with container.database.transaction() as session:
        result = await session.execute(select(LogEntryRecord))
        return list(result.scalars().all())


def _client(container: ApplicationContainer, monkeypatch) -> TestClient:
    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", _TOKEN)
    get_settings.cache_clear()
    return TestClient(create_app(container))


# --- Pipeline: handler → queue → flush → table ---------------------------


def test_pipeline_captures_and_flushes_log_lines(
    application_container: ApplicationContainer,
) -> None:
    issue_id = uuid4()

    async def _scenario() -> None:
        recorder = LogRecorder(
            application_container.database, flush_interval_seconds=0.02
        )
        await recorder.start()
        try:
            logger = logging.getLogger("repomesh.test.pipeline")
            logger.setLevel(logging.INFO)
            logger.info("hello world", extra={"issue_id": str(issue_id)})
            logger.warning("warn line")
            try:
                raise RuntimeError("boom")
            except RuntimeError:
                logger.exception("failed task")
            await asyncio.sleep(0.15)
        finally:
            await recorder.close()

    asyncio.run(_scenario())

    rows = asyncio.run(_rows(application_container))
    assert len(rows) == 3
    by_message = {row.message: row for row in rows}
    info = by_message["hello world"]
    assert info.level == "INFO"
    assert info.source == "repomesh.test.pipeline"
    assert info.issue_id == issue_id
    assert info.exc_info is None
    error = by_message["failed task"]
    assert error.level == "ERROR"
    assert error.exc_info is not None
    assert "RuntimeError: boom" in error.exc_info


def test_pipeline_stores_unparseable_issue_id_as_null(
    application_container: ApplicationContainer,
) -> None:
    async def _scenario() -> None:
        recorder = LogRecorder(
            application_container.database, flush_interval_seconds=0.02
        )
        await recorder.start()
        try:
            logger = logging.getLogger("repomesh.test.pipeline")
            logger.setLevel(logging.INFO)
            logger.info("ambient line")
            logger.info(
                "bad issue line", extra={"issue_id": "not-a-uuid"}
            )
            await asyncio.sleep(0.15)
        finally:
            await recorder.close()

    asyncio.run(_scenario())

    rows = asyncio.run(_rows(application_container))
    assert len(rows) == 2
    assert all(row.issue_id is None for row in rows)


def test_pipeline_attributes_issue_from_ambient_context(
    application_container: ApplicationContainer,
) -> None:
    """Logs inside a discovery step inherit its issue without any extra=.

    The discovery endpoints set ``current_usage_context`` around a step and
    run it in a ``to_thread`` worker; this exercises that exact shape: a log
    fired from the worker thread lands tagged with the step's issue, a log
    fired outside any context stays unlabelled, and an explicit ``extra``
    still wins over the ambient context.
    """
    issue_id, other_id = uuid4(), uuid4()

    async def _scenario() -> None:
        recorder = LogRecorder(
            application_container.database, flush_interval_seconds=0.02
        )
        await recorder.start()
        try:
            logger = logging.getLogger("repomesh.test.ambient")
            logger.setLevel(logging.INFO)
            token = current_usage_context.set(
                UsageContext(issue_id=issue_id, step=2)
            )
            try:
                await asyncio.to_thread(logger.info, "inside discovery step")
                logger.info("explicit extra wins", extra={"issue_id": str(other_id)})
            finally:
                current_usage_context.reset(token)
            logger.info("outside context stays unlabelled")
            await asyncio.sleep(0.15)
        finally:
            await recorder.close()

    asyncio.run(_scenario())

    rows = asyncio.run(_rows(application_container))
    by_message = {row.message: row for row in rows}
    assert by_message["inside discovery step"].issue_id == issue_id
    assert by_message["outside context stays unlabelled"].issue_id is None
    # The explicit extra takes precedence over the ambient context.
    assert by_message["explicit extra wins"].issue_id == other_id


def test_pipeline_drops_when_queue_is_full(
    application_container: ApplicationContainer,
) -> None:
    async def _scenario() -> None:
        recorder = LogRecorder(
            application_container.database,
            flush_interval_seconds=3600,
            max_queue=1,
        )
        await recorder.start()
        try:
            logger = logging.getLogger("repomesh.test.pipeline")
            logger.setLevel(logging.INFO)
            logger.info("one")
            logger.info("two")
            logger.info("three")
        finally:
            await recorder.close()

    asyncio.run(_scenario())

    # Only the first line fits in the bounded queue before the final drain.
    rows = asyncio.run(_rows(application_container))
    assert len(rows) == 1
    assert rows[0].message == "one"


def test_pipeline_detaches_handler_on_close(
    application_container: ApplicationContainer,
) -> None:
    async def _scenario() -> None:
        recorder = LogRecorder(
            application_container.database, flush_interval_seconds=0.02
        )
        await recorder.start()
        logger = logging.getLogger("repomesh.test.pipeline")
        logger.setLevel(logging.INFO)
        logger.info("before close")
        await asyncio.sleep(0.1)
        await recorder.close()
        logger.info("after close")
        assert recorder._queue.qsize() == 0  # noqa: SLF001 - detach check

    asyncio.run(_scenario())

    rows = asyncio.run(_rows(application_container))
    assert [row.message for row in rows] == ["before close"]


# --- Query store -----------------------------------------------------------


def test_query_filters_by_level_source_and_issue_id(
    application_container: ApplicationContainer,
) -> None:
    issue_id = uuid4()
    _seed_log(application_container, level="WARNING", message="w1", minutes_ago=3)
    _seed_log(application_container, level="ERROR", message="e1", minutes_ago=2)
    _seed_log(
        application_container,
        level="INFO",
        message="i1",
        issue_id=issue_id,
        source="repomesh.modules.repository_intelligence",
        minutes_ago=1,
    )
    store = LogQueryStore(application_container.database)

    async def _query(**filters) -> dict:
        return await store.list_logs(**filters)

    only_error = asyncio.run(_query(level="ERROR"))
    assert [row["message"] for row in only_error["logs"]] == ["e1"]
    by_source = asyncio.run(_query(source="repository_intelligence"))
    assert [row["message"] for row in by_source["logs"]] == ["i1"]
    by_issue = asyncio.run(_query(issue_id=issue_id))
    assert [row["message"] for row in by_issue["logs"]] == ["i1"]
    missing_issue = asyncio.run(_query(issue_id=uuid4()))
    assert missing_issue["logs"] == []


def test_query_full_text_search_matches_message_and_exc_info(
    application_container: ApplicationContainer,
) -> None:
    _seed_log(application_container, message="plan snapshot taken", minutes_ago=2)
    _seed_log(
        application_container,
        message="task dispatched",
        exc_info="Traceback (most recent call last):\nRuntimeError: kboom",
        minutes_ago=1,
    )
    store = LogQueryStore(application_container.database)

    async def _query(query: str) -> dict:
        return await store.list_logs(query=query)

    hits_message = asyncio.run(_query("snapshot"))
    assert [row["message"] for row in hits_message["logs"]] == ["plan snapshot taken"]
    hits_exc = asyncio.run(_query("kboom"))
    assert [row["message"] for row in hits_exc["logs"]] == ["task dispatched"]
    assert asyncio.run(_query("nope"))["logs"] == []


def test_query_keyset_pagination(application_container: ApplicationContainer) -> None:
    for i in range(5):
        _seed_log(
            application_container,
            message=f"line-{i}",
            minutes_ago=5 - i,
        )
    store = LogQueryStore(application_container.database)

    async def _query(**filters) -> dict:
        return await store.list_logs(**filters)

    page1 = asyncio.run(_query(limit=2))
    assert [row["message"] for row in page1["logs"]] == ["line-4", "line-3"]
    assert page1["next_cursor"] is not None
    page2 = asyncio.run(_query(limit=2, cursor=page1["next_cursor"]))
    assert [row["message"] for row in page2["logs"]] == ["line-2", "line-1"]
    page3 = asyncio.run(_query(limit=2, cursor=page2["next_cursor"]))
    assert [row["message"] for row in page3["logs"]] == ["line-0"]
    assert page3["next_cursor"] is None


def test_query_stale_cursor_returns_empty_page(
    application_container: ApplicationContainer,
) -> None:
    _seed_log(application_container, message="only line", minutes_ago=1)
    store = LogQueryStore(application_container.database)

    async def _query() -> dict:
        return await store.list_logs(cursor=uuid4())

    assert asyncio.run(_query()) == {"logs": [], "next_cursor": None}


# --- API -------------------------------------------------------------------


def test_logs_endpoint_requires_the_action_token(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    client = _client(application_container, monkeypatch)
    assert client.get("/api/v1/observe/logs").status_code == 401


def test_logs_endpoint_returns_seeded_entries(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    _seed_log(application_container, message="alpha", minutes_ago=2)
    _seed_log(application_container, message="beta", minutes_ago=1)
    client = _client(application_container, monkeypatch)

    response = client.get("/api/v1/observe/logs", headers=_HEADERS, params={"limit": 1})
    assert response.status_code == 200
    body = response.json()
    assert len(body["logs"]) == 1
    assert body["logs"][0]["message"] == "beta"
    assert body["next_cursor"] is not None

    page2 = client.get(
        "/api/v1/observe/logs",
        headers=_HEADERS,
        params={"limit": 1, "cursor": body["next_cursor"]},
    )
    assert page2.status_code == 200
    assert [row["message"] for row in page2.json()["logs"]] == ["alpha"]


def test_logs_endpoint_filters_and_validates(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    _seed_log(application_container, level="WARNING", message="warn line", minutes_ago=1)
    _seed_log(application_container, level="ERROR", message="error line", minutes_ago=2)
    client = _client(application_container, monkeypatch)

    warning_only = client.get(
        "/api/v1/observe/logs", headers=_HEADERS, params={"level": "WARNING"}
    )
    assert [row["message"] for row in warning_only.json()["logs"]] == ["warn line"]

    search = client.get(
        "/api/v1/observe/logs", headers=_HEADERS, params={"query": "error"}
    )
    assert [row["message"] for row in search.json()["logs"]] == ["error line"]

    bad_level = client.get(
        "/api/v1/observe/logs", headers=_HEADERS, params={"level": "TRACE"}
    )
    assert bad_level.status_code == 422
