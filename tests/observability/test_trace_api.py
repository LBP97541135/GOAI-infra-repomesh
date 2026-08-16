"""The console trace endpoints over a real app + SQLite container."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from repomesh.bootstrap.app import create_app
from repomesh.bootstrap.container import ApplicationContainer
from repomesh.modules.observability.infrastructure.models import LLMUsageRecord
from repomesh.modules.observability.infrastructure.trace_ingest import parse_copaw_session
from repomesh.settings import get_settings

_TOKEN = "internal-secret"
_HEADERS = {"Authorization": f"Bearer {_TOKEN}"}

_FIXTURE = Path(__file__).parent / "fixtures" / "copaw_session_sample.json"


def _seed(container: ApplicationContainer, *specs: dict) -> list[str]:
    """Ingest the calibrated fixture as one or more sessions.

    All sessions go through the real store path (``TraceStore.upsert_session``
    + ``ingest_events``) inside a single event loop, matching how the poller
    projects objects. Returns the inserted session row ids.
    """
    text = _FIXTURE.read_text(encoding="utf-8")
    parsed = parse_copaw_session(text)

    async def _insert() -> list[str]:
        store = container.trace_store()
        ids: list[str] = []
        for spec in specs or ({},):
            agent_name = spec.get("agent_name", "agt-leader-3a15e2e1daed")
            session_id = spec.get("session_id", "sess-1")
            source_key = spec.get("source_key") or (
                f"agents/{agent_name}/.copaw/workspaces/w1/sessions/{session_id}.json"
            )
            row_id = await store.upsert_session(
                source_key=source_key,
                session_id=session_id,
                agent_name=agent_name,
                runtime="copaw",
                object_mtime=datetime.now(UTC) - timedelta(minutes=5),
                object_size=len(text.encode("utf-8")),
                parsing_error=None,
                parsed_at=datetime.now(UTC),
            )
            await store.ingest_events(row_id, list(parsed.events))
            ids.append(str(row_id))
        return ids

    return asyncio.run(_insert())


def _client(container: ApplicationContainer, monkeypatch) -> TestClient:
    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", _TOKEN)
    get_settings.cache_clear()
    return TestClient(create_app(container))


def test_trace_endpoints_require_the_action_token(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    client = _client(application_container, monkeypatch)
    assert client.get("/api/v1/observe/trace/sessions").status_code == 401
    assert client.get("/api/v1/observe/trace/events").status_code == 401


def test_trace_issue_groups_and_issue_filter_approximate(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    """The by-issue trace view uses temporal overlap.

    A session whose first-seen time falls inside an issue's activity window
    (usage ∪ logs, padded) is reported as suspected; the ``issue_id`` filter
    narrows the session list to that window. An issue with no activity
    window yields an empty page.
    """
    _seed(application_container)
    client = _client(application_container, monkeypatch)
    session = client.get("/api/v1/observe/trace/sessions", headers=_HEADERS).json()[
        "sessions"
    ][0]
    issue_id = uuid4()

    # Anchor the issue's activity window exactly on the session's first-seen
    # time: a single usage row gives min == max == first_seen, and the
    # ±15 min slack keeps the overlap deterministic.
    async def _insert_usage() -> None:
        async with application_container.database.transaction() as session:
            session.add(
                LLMUsageRecord(
                    id=uuid4(),
                    created_at=datetime.fromisoformat(session_ts),
                    provider="deepseek",
                    model="deepseek-chat",
                    operation="chat",
                    issue_id=issue_id,
                    discovery_step=1,
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                    finish_reason="stop",
                    latency_ms=1200,
                    status="ok",
                )
            )

    session_ts = session["first_seen_at"]
    asyncio.run(_insert_usage())

    groups = client.get("/api/v1/observe/trace/issues", headers=_HEADERS).json()
    assert groups["issues"][0]["issue_id"] == str(issue_id)
    assert groups["issues"][0]["suspected_sessions"] == 1

    filtered = client.get(
        f"/api/v1/observe/trace/sessions?issue_id={issue_id}", headers=_HEADERS
    ).json()
    assert len(filtered["sessions"]) == 1
    assert filtered["sessions"][0]["session_id"] == "sess-1"

    empty = client.get(
        f"/api/v1/observe/trace/sessions?issue_id={uuid4()}", headers=_HEADERS
    ).json()
    assert empty["sessions"] == []
    assert empty["next_cursor"] is None


def test_trace_sessions_lists_the_seeded_session(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    _seed(application_container)
    client = _client(application_container, monkeypatch)

    body = client.get("/api/v1/observe/trace/sessions", headers=_HEADERS).json()
    assert body["next_cursor"] is None
    assert len(body["sessions"]) == 1
    session = body["sessions"][0]
    assert session["agent_name"] == "agt-leader-3a15e2e1daed"
    assert session["session_id"] == "sess-1"
    assert session["runtime"] == "copaw"
    assert session["event_count"] == 7
    assert session["parsing_error"] is None
    assert session["source_key"].endswith("sessions/sess-1.json")
    assert session["first_seen_at"] is not None


def test_trace_sessions_keyset_pagination(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    ids = _seed(
        application_container,
        {"session_id": "sess-a"},
        {"session_id": "sess-b"},
        {"session_id": "sess-c"},
    )
    client = _client(application_container, monkeypatch)

    page1 = client.get(
        "/api/v1/observe/trace/sessions?limit=2", headers=_HEADERS
    ).json()
    assert len(page1["sessions"]) == 2
    assert page1["next_cursor"] is not None

    page2 = client.get(
        f"/api/v1/observe/trace/sessions?limit=2&cursor={page1['next_cursor']}",
        headers=_HEADERS,
    ).json()
    assert len(page2["sessions"]) == 1
    assert page2["next_cursor"] is None

    seen = [s["id"] for s in page1["sessions"]] + [s["id"] for s in page2["sessions"]]
    assert sorted(seen) == sorted(ids)


def test_trace_sessions_filter_by_agent(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    _seed(application_container, {"agent_name": "agt-leader-x", "session_id": "sess-l"})
    _seed(application_container, {"agent_name": "agt-worker-y", "session_id": "sess-w"})
    client = _client(application_container, monkeypatch)

    body = client.get(
        "/api/v1/observe/trace/sessions?agent_name=agt-worker-y", headers=_HEADERS
    ).json()
    assert len(body["sessions"]) == 1
    assert body["sessions"][0]["session_id"] == "sess-w"


def test_trace_session_events_in_trace_order(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    (row_id,) = _seed(application_container)
    client = _client(application_container, monkeypatch)

    body = client.get(
        f"/api/v1/observe/trace/sessions/{row_id}/events", headers=_HEADERS
    ).json()
    assert body["next_seq"] is None
    events = body["events"]
    assert len(events) == 7
    assert [e["event_type"] for e in events] == [
        "task", "tool", "tool", "tool", "skill", "mcp", "chat",
    ]
    assert [e["seq"] for e in events] == [1, 2, 3, 4, 5, 6, 7]
    # Join fields stay empty in the session-detail timeline.
    assert all(e["agent_name"] is None for e in events)

    task_event = events[0]
    assert task_event["payload"]["task_id"] == "48de5f6a-73e3-4215-8316-f1addbcc91da"
    mcp_event = next(e for e in events if e["event_type"] == "mcp")
    assert mcp_event["status"] == "ok"
    assert "Branch created" in (mcp_event["summary"] or "")


def test_trace_session_events_paginate_by_seq(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    (row_id,) = _seed(application_container)
    client = _client(application_container, monkeypatch)

    page1 = client.get(
        f"/api/v1/observe/trace/sessions/{row_id}/events?limit=3", headers=_HEADERS
    ).json()
    assert len(page1["events"]) == 3
    assert page1["next_seq"] == 3

    page2 = client.get(
        f"/api/v1/observe/trace/sessions/{row_id}/events"
        f"?limit=3&after_seq={page1['next_seq']}",
        headers=_HEADERS,
    ).json()
    assert len(page2["events"]) == 3
    assert page2["next_seq"] == 6

    page3 = client.get(
        f"/api/v1/observe/trace/sessions/{row_id}/events"
        f"?limit=3&after_seq={page2['next_seq']}",
        headers=_HEADERS,
    ).json()
    assert len(page3["events"]) == 1
    assert page3["next_seq"] is None

    seqs = (
        [e["seq"] for e in page1["events"]]
        + [e["seq"] for e in page2["events"]]
        + [e["seq"] for e in page3["events"]]
    )
    assert seqs == list(range(1, 8))


def test_trace_session_events_404_for_unknown_session(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    client = _client(application_container, monkeypatch)
    response = client.get(
        f"/api/v1/observe/trace/sessions/{uuid4()}/events", headers=_HEADERS
    )
    assert response.status_code == 404


def test_trace_events_global_stream_filters_and_joins(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    _seed(
        application_container,
        {"agent_name": "agt-leader-x", "session_id": "sess-leader"},
        {"agent_name": "agt-worker-y", "session_id": "sess-worker"},
    )
    client = _client(application_container, monkeypatch)

    tools = client.get(
        "/api/v1/observe/trace/events?event_type=tool", headers=_HEADERS
    ).json()
    assert len(tools["events"]) == 6  # 3 tool events × 2 sessions
    assert all(e["event_type"] == "tool" for e in tools["events"])
    assert all(
        e["agent_name"] in {"agt-leader-x", "agt-worker-y"} for e in tools["events"]
    )

    # The calibrated fixture contains no failed tools: the error filter must
    # return an empty page while the ok filter returns everything.
    ok_events = client.get(
        "/api/v1/observe/trace/events?status=ok", headers=_HEADERS
    ).json()
    assert len(ok_events["events"]) == 14

    errors = client.get(
        "/api/v1/observe/trace/events?status=error", headers=_HEADERS
    ).json()
    assert len(errors["events"]) == 0

    worker_chat = client.get(
        "/api/v1/observe/trace/events?agent_name=agt-worker-y&event_type=chat",
        headers=_HEADERS,
    ).json()
    assert len(worker_chat["events"]) == 1
    assert worker_chat["events"][0]["session_external_id"] == "sess-worker"


def test_trace_events_global_keyset_pagination(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    _seed(
        application_container,
        {"agent_name": "agt-leader-x", "session_id": "sess-leader"},
        {"agent_name": "agt-worker-y", "session_id": "sess-worker"},
    )
    client = _client(application_container, monkeypatch)

    page1 = client.get(
        "/api/v1/observe/trace/events?limit=5", headers=_HEADERS
    ).json()
    assert len(page1["events"]) == 5
    assert page1["next_cursor"] is not None

    page2 = client.get(
        f"/api/v1/observe/trace/events?limit=5&cursor={page1['next_cursor']}",
        headers=_HEADERS,
    ).json()
    assert len(page2["events"]) == 5
    assert page2["next_cursor"] is not None

    page3 = client.get(
        f"/api/v1/observe/trace/events?limit=5&cursor={page2['next_cursor']}",
        headers=_HEADERS,
    ).json()
    assert len(page3["events"]) == 4
    assert page3["next_cursor"] is None

    ids = (
        [e["id"] for e in page1["events"]]
        + [e["id"] for e in page2["events"]]
        + [e["id"] for e in page3["events"]]
    )
    assert len(set(ids)) == 14


def test_trace_events_rejects_unknown_filters(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    client = _client(application_container, monkeypatch)
    assert (
        client.get(
            "/api/v1/observe/trace/events?event_type=bogus", headers=_HEADERS
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/v1/observe/trace/events?status=bogus", headers=_HEADERS
        ).status_code
        == 422
    )
