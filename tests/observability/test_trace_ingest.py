"""Trace ingest: parser unit tests over the calibrated fixture + idempotency.

The fixture mirrors the real CoPaw session schema discovered during M0:
``agent.memory.content`` as ``[message, extra]`` pairs, text/tool_use/
tool_result blocks, and a ``repomesh.collaboration.v1`` task package embedded
in the first user message. Expected projection: 7 events —
task, tool x3, skill, mcp, chat.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

from repomesh.bootstrap.container import ApplicationContainer
from repomesh.modules.observability.infrastructure.models import (
    TraceEventRecord,
    TraceSessionRecord,
)
from repomesh.modules.observability.infrastructure.trace_ingest import (
    LocalTraceSource,
    TraceIngester,
    TraceStore,
    classify_tool,
    parse_copaw_session,
    parse_openclaw_session,
)

FIXTURE = Path(__file__).parent / "fixtures" / "copaw_session_sample.json"
SESSION_KEY = "agents/agt-leader-3a15e2e1daed/.copaw/workspaces/default/sessions/sess.json"
TASK_ID = "48de5f6a-73e3-4215-8316-f1addbcc91da"

OPENCLAW_KEY = "agents/agt-worker-b7c2/.openclaw/agents/main/sessions/oc-sess-001.jsonl"


def _fixture_text() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def _write_object(root: Path, key: str, text: str) -> Path:
    path = root / Path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _bump_mtime(path: Path, seconds: float = 5.0) -> None:
    now = time.time()
    os.utime(path, (now + seconds, now + seconds))


def _run(coro) -> object:
    return asyncio.run(coro)


def _event_count(container: ApplicationContainer) -> int:
    async def _query() -> int:
        async with container.database.transaction() as session:
            count = await session.scalar(select(func.count(TraceEventRecord.id)))
            return int(count or 0)

    return int(_run(_query()))


def _session_row(
    container: ApplicationContainer, source_key: str = SESSION_KEY,
) -> dict | None:
    async def _query() -> dict | None:
        async with container.database.transaction() as session:
            row = (
                await session.execute(
                    select(
                        TraceSessionRecord.source_key,
                        TraceSessionRecord.session_id,
                        TraceSessionRecord.agent_name,
                        TraceSessionRecord.runtime,
                        TraceSessionRecord.event_count,
                        TraceSessionRecord.parsing_error,
                        TraceSessionRecord.parsed_at,
                    ).where(TraceSessionRecord.source_key == source_key)
                )
            ).first()
            if row is None:
                return None
            return {
                "source_key": row[0],
                "session_id": row[1],
                "agent_name": row[2],
                "runtime": row[3],
                "event_count": row[4],
                "parsing_error": row[5],
                "parsed_at": row[6],
            }

    return _run(_query())


# ---------------------------------------------------------------------------
# Parser (pure function, no database)
# ---------------------------------------------------------------------------


def test_parse_fixture_full_projection() -> None:
    parsed = parse_copaw_session(_fixture_text())
    assert parsed.error is None
    events = parsed.events
    assert [e.seq for e in events] == [1, 2, 3, 4, 5, 6, 7]
    expected_types = ["task", "tool", "tool", "tool", "skill", "mcp", "chat"]
    assert [e.event_type for e in events] == expected_types
    assert [e.name for e in events] == [
        "task.assignment",
        "execute_shell_command",
        "glob_search",
        "filesync",
        "skill_api_design",
        "mcporter.github.create_branch",
        "assistant",
    ]

    task, shell, glob_t, filesync, skill, mcp, chat = events

    # task event carries the RepoMesh collaboration payload
    assert task.role == "user"
    assert task.payload["task_id"] == TASK_ID
    assert task.payload["kind"] == "task_assignment"
    assert task.payload["schema"] == "repomesh.collaboration.v1"
    assert task.payload["project_id"] == "359303c1-e650-59f5-ba85-accab78f681f"
    assert "MCP tool repomesh-task-control" in task.summary

    # tool events merge the matching tool_result: ok + output summary
    assert shell.name == "execute_shell_command"
    assert shell.role == "tool"
    assert shell.status == "ok"
    assert shell.summary == "drwxr-xr-x 4 root root 4096 ."
    assert shell.payload["raw_input"].startswith("{")
    assert shell.payload["call_id"] == "call-a1"
    assert glob_t.status == "ok"
    assert filesync.summary == "synced 12 files"

    # skill / mcp classification
    assert skill.event_type == "skill"
    assert skill.name == "skill_api_design"
    assert mcp.event_type == "mcp"
    assert mcp.name == "mcporter.github.create_branch"

    # assistant chat text
    assert chat.role == "assistant"
    assert chat.summary == "Branch feat/todo-api is ready for the implementation."

    # timestamps preserved as UTC
    assert task.ts == datetime(2026, 8, 14, 11, 43, 0, 386000, tzinfo=UTC)


def test_parse_classifies_tool_names() -> None:
    assert classify_tool("skill_api_design") == "skill"
    assert classify_tool("mcporter.github.create_branch") == "mcp"
    assert classify_tool("repomesh-task-control.start_assigned_task") == "mcp"
    assert classify_tool("memory_search") == "tool"
    assert classify_tool("execute_shell_command") == "tool"
    assert classify_tool("filesync") == "tool"


def test_parse_malformed_input() -> None:
    with pytest.raises(ValueError):
        parse_copaw_session("")
    with pytest.raises(ValueError):
        parse_copaw_session("{not json")
    with pytest.raises(ValueError):
        parse_copaw_session("[]")
    # structurally valid but without messages -> no events, no error
    parsed = parse_copaw_session('{"agent": {"memory": {"content": []}}}')
    assert parsed.events == ()
    assert parsed.error is None


def test_parse_truncates_long_summary() -> None:
    long_text = "A" * 600
    session = {
        "agent": {
            "memory": {
                "content": [
                    [
                        {
                            "id": "m1",
                            "name": "Friday",
                            "role": "assistant",
                            "content": [{"type": "text", "text": long_text}],
                            "timestamp": "2026-08-14 12:00:00.000",
                        },
                        [],
                    ]
                ]
            }
        }
    }
    parsed = parse_copaw_session(json.dumps(session))
    assert len(parsed.events) == 1
    summary = parsed.events[0].summary
    assert summary is not None
    assert len(summary) <= 500
    assert summary.startswith("A" * 375)
    assert "…" in summary
    assert summary.endswith("A" * 124)


def test_parse_caps_event_count() -> None:
    messages = []
    for index in range(10):
        messages.append(
            [
                {
                    "id": f"m{index}",
                    "name": "Friday",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": f"call-{index}",
                            "name": "read_file",
                            "input": {"path": f"/x/{index}"},
                        }
                    ],
                    "timestamp": "2026-08-14 12:00:00.000",
                },
                [],
            ]
        )
    session = {"agent": {"memory": {"content": messages}}}
    parsed = parse_copaw_session(json.dumps(session), max_events=3)
    assert len(parsed.events) == 3
    assert parsed.error is not None
    assert "截断" in parsed.error


# ---------------------------------------------------------------------------
# Ingest projection (database-backed, LocalTraceSource)
# ---------------------------------------------------------------------------


def test_ingest_projects_fixture(
    application_container: ApplicationContainer, tmp_path: Path,
) -> None:
    _write_object(tmp_path, SESSION_KEY, _fixture_text())
    store = TraceStore(application_container.database)
    ingester = TraceIngester(store, LocalTraceSource(tmp_path))

    stats = _run(ingester.ingest_once())
    assert stats["seen"] == 1
    assert stats["parsed"] == 1
    assert stats["inserted"] == 7
    assert stats["errors"] == 0

    row = _session_row(application_container)
    assert row is not None
    assert row["session_id"] == "sess"
    assert row["agent_name"] == "agt-leader-3a15e2e1daed"
    assert row["event_count"] == 7
    assert row["parsing_error"] is None
    assert row["parsed_at"] is not None
    assert _event_count(application_container) == 7


def test_ingest_is_idempotent_across_polls(
    application_container: ApplicationContainer, tmp_path: Path,
) -> None:
    _write_object(tmp_path, SESSION_KEY, _fixture_text())
    ingester = TraceIngester(
        TraceStore(application_container.database), LocalTraceSource(tmp_path)
    )

    first = _run(ingester.ingest_once())
    assert first["inserted"] == 7

    second = _run(ingester.ingest_once())
    assert second["seen"] == 1
    assert second["unchanged"] == 1
    assert second["parsed"] == 0
    assert second["inserted"] == 0
    assert _event_count(application_container) == 7
    assert _session_row(application_container)["event_count"] == 7


def test_ingest_reparses_changed_object_without_duplication(
    application_container: ApplicationContainer, tmp_path: Path,
) -> None:
    path = _write_object(tmp_path, SESSION_KEY, _fixture_text())
    ingester = TraceIngester(
        TraceStore(application_container.database), LocalTraceSource(tmp_path)
    )
    assert _run(ingester.ingest_once())["inserted"] == 7

    # Same content, newer mtime -> full reparse; (session_id, seq) conflicts
    # are swallowed, so the row count stays 7 instead of doubling.
    _bump_mtime(path)
    stats = _run(ingester.ingest_once())
    assert stats["parsed"] == 1
    assert stats["inserted"] == 0
    assert _event_count(application_container) == 7
    assert _session_row(application_container)["event_count"] == 7


def test_ingest_records_failure_and_recovers_after_change(
    application_container: ApplicationContainer, tmp_path: Path,
) -> None:
    path = _write_object(tmp_path, SESSION_KEY, "{broken json")
    ingester = TraceIngester(
        TraceStore(application_container.database), LocalTraceSource(tmp_path)
    )

    stats = _run(ingester.ingest_once())
    assert stats["errors"] == 1
    row = _session_row(application_container)
    assert row is not None
    assert row["parsing_error"] is not None
    assert row["parsed_at"] is None

    # Unchanged broken object -> skipped, not hot-looped.
    again = _run(ingester.ingest_once())
    assert again["unchanged"] == 1
    assert again["errors"] == 0

    # Object fixed + mtime bumped -> parse succeeds, error cleared.
    _write_object(tmp_path, SESSION_KEY, _fixture_text())
    _bump_mtime(path)
    stats = _run(ingester.ingest_once())
    assert stats["parsed"] == 1
    assert stats["errors"] == 0
    row = _session_row(application_container)
    assert row["parsing_error"] is None
    assert row["event_count"] == 7


def test_ingest_skips_non_session_objects(
    application_container: ApplicationContainer, tmp_path: Path,
) -> None:
    _write_object(tmp_path, "teams/team-a/shared/tasks/not-a-session.json", "{}")
    _write_object(
        tmp_path,
        "agents/agt-leader-3a15e2e1daed/.copaw/workspaces/default/sessions/sess.json",
        _fixture_text(),
    )
    ingester = TraceIngester(
        TraceStore(application_container.database), LocalTraceSource(tmp_path)
    )
    stats = _run(ingester.ingest_once())
    assert stats["seen"] == 1  # only the agents/.copaw/.../sessions object
    assert stats["inserted"] == 7


# ---------------------------------------------------------------------------
# OpenClaw jsonl parser (pure function, no database)
# ---------------------------------------------------------------------------


def _openclaw_fixture_text() -> str:
    return (Path(__file__).parent / "fixtures" / "openclaw_session_sample.jsonl").read_text(
        encoding="utf-8"
    )


def test_parse_openclaw_fixture_full_projection() -> None:
    parsed = parse_openclaw_session(_openclaw_fixture_text())
    assert parsed.error is None
    events = parsed.events
    assert [e.seq for e in events] == [1, 2, 3, 4, 5]
    assert [e.event_type for e in events] == ["chat", "tool", "chat", "skill", "chat"]
    assert [e.status for e in events] == ["ok", "ok", "ok", "error", "ok"]

    user, glob_t, assistant, skill, done = events
    assert user.role == "user"
    assert user.summary == "修复 todo-api 的编译错误"
    assert glob_t.role == "tool"
    assert glob_t.name == "glob_search"
    assert glob_t.status == "ok"
    assert glob_t.summary == "src/api.ts\nsrc/main.ts"
    assert glob_t.payload["call_id"] == "toolu-1"
    assert '"pattern": "src/**/*.ts"' in glob_t.payload["input"]
    assert assistant.role == "assistant"
    assert assistant.summary == "先看一下 src/api.ts 的编译错误。"
    assert skill.event_type == "skill"
    assert skill.name == "skill_code_fix"
    assert skill.status == "error"
    assert skill.summary == "command failed with exit code 1: tsc"
    # The last line is the bare Pi-style message (no type/message wrapper).
    assert done.role == "assistant"
    assert done.summary == "修复完成。"

    # epoch-millisecond timestamps decode to UTC datetimes.
    assert user.ts == datetime(2026, 8, 15, 0, 0, 10, tzinfo=UTC)
    assert skill.ts == datetime(2026, 8, 15, 0, 0, 50, tzinfo=UTC)


def test_parse_openclaw_tolerates_partial_writes() -> None:
    lines = _openclaw_fixture_text().splitlines()
    broken = lines[:3] + ["{not json", ""] + lines[3:]
    parsed = parse_openclaw_session("\n".join(broken))
    assert len(parsed.events) == 5
    assert parsed.error is None


def test_parse_openclaw_skips_bookkeeping_entries() -> None:
    text = (
        '{"type":"session","id":"s1","timestamp":1786752000000}\n'
        '{"id":"e1","type":"compaction","tokensBefore":1000,"firstKeptEntryId":"m1"}\n'
        '{"id":"e2","type":"custom","payload":{}}\n'
        '{"id":"m1","role":"user","content":[{"type":"text","text":"hi"}],'
        '"timestamp":1786752001000}\n'
    )
    parsed = parse_openclaw_session(text)
    assert len(parsed.events) == 1
    assert parsed.events[0].summary == "hi"


def test_parse_openclaw_empty_is_error() -> None:
    with pytest.raises(ValueError):
        parse_openclaw_session("")
    with pytest.raises(ValueError):
        parse_openclaw_session("\n\n  \n")


def test_parse_openclaw_caps_event_count() -> None:
    parsed = parse_openclaw_session(_openclaw_fixture_text(), max_events=2)
    assert len(parsed.events) == 2
    assert parsed.error is not None
    assert "截断" in parsed.error


def test_session_key_recognition_across_runtimes() -> None:
    from repomesh.modules.observability.infrastructure.trace_ingest import (
        is_session_key,
        parse_session_key,
    )

    assert is_session_key(SESSION_KEY)
    assert parse_session_key(SESSION_KEY)["runtime"] == "copaw"
    assert is_session_key(OPENCLAW_KEY)
    assert parse_session_key(OPENCLAW_KEY) == {
        "agent_name": "agt-worker-b7c2",
        "session_id": "oc-sess-001",
        "runtime": "openclaw",
    }
    # The OpenClaw session index is not a transcript.
    assert not is_session_key(
        "agents/agt-worker-b7c2/.openclaw/agents/main/sessions/sessions.json"
    )


# ---------------------------------------------------------------------------
# OpenClaw ingest projection (database-backed, LocalTraceSource)
# ---------------------------------------------------------------------------


def test_ingest_projects_openclaw_fixture(
    application_container: ApplicationContainer, tmp_path: Path,
) -> None:
    _write_object(tmp_path, OPENCLAW_KEY, _openclaw_fixture_text())
    ingester = TraceIngester(
        TraceStore(application_container.database), LocalTraceSource(tmp_path)
    )

    stats = _run(ingester.ingest_once())
    assert stats["seen"] == 1
    assert stats["parsed"] == 1
    assert stats["inserted"] == 5
    assert stats["errors"] == 0

    row = _session_row(application_container, OPENCLAW_KEY)
    assert row is not None
    assert row["session_id"] == "oc-sess-001"
    assert row["agent_name"] == "agt-worker-b7c2"
    assert row["runtime"] == "openclaw"
    assert row["event_count"] == 5
    assert row["parsing_error"] is None
    assert row["parsed_at"] is not None
    assert _event_count(application_container) == 5


def test_ingest_openclaw_is_idempotent_across_polls(
    application_container: ApplicationContainer, tmp_path: Path,
) -> None:
    _write_object(tmp_path, OPENCLAW_KEY, _openclaw_fixture_text())
    ingester = TraceIngester(
        TraceStore(application_container.database), LocalTraceSource(tmp_path)
    )

    assert _run(ingester.ingest_once())["inserted"] == 5
    second = _run(ingester.ingest_once())
    assert second["seen"] == 1
    assert second["unchanged"] == 1
    assert second["inserted"] == 0
    assert _event_count(application_container) == 5


def test_ingest_mixed_runtimes_in_one_root(
    application_container: ApplicationContainer, tmp_path: Path,
) -> None:
    _write_object(tmp_path, SESSION_KEY, _fixture_text())
    _write_object(tmp_path, OPENCLAW_KEY, _openclaw_fixture_text())
    ingester = TraceIngester(
        TraceStore(application_container.database), LocalTraceSource(tmp_path)
    )

    stats = _run(ingester.ingest_once())
    assert stats["seen"] == 2
    assert stats["parsed"] == 2
    assert stats["inserted"] == 12  # 7 copaw + 5 openclaw
    assert stats["errors"] == 0
    assert _event_count(application_container) == 12
    assert _session_row(application_container, SESSION_KEY)["runtime"] == "copaw"
    assert _session_row(application_container, OPENCLAW_KEY)["runtime"] == "openclaw"


def test_ingest_skips_openclaw_sessions_json_index(
    application_container: ApplicationContainer, tmp_path: Path,
) -> None:
    _write_object(
        tmp_path,
        "agents/agt-worker-b7c2/.openclaw/agents/main/sessions/sessions.json",
        '{"agent:main": {"sessionId": "oc-sess-001"}}',
    )
    _write_object(tmp_path, OPENCLAW_KEY, _openclaw_fixture_text())
    ingester = TraceIngester(
        TraceStore(application_container.database), LocalTraceSource(tmp_path)
    )

    stats = _run(ingester.ingest_once())
    assert stats["seen"] == 1  # only the transcript; sessions.json ignored
    assert stats["inserted"] == 5
