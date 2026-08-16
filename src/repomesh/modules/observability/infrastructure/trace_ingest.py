"""Trace ingest: agent session files -> observability.trace_sessions / trace_events.

Two runtime transcript formats are supported (the ``runtime`` column keeps them
apart):

- CoPaw: each container writes its per-session message log to shared storage
  (FileSync.push_loop) at ``agents/{name}/.copaw/workspaces/{ws}/sessions/{id}.json``.
- OpenClaw: the OpenClaw Gateway persists a JSONL transcript per session at
  ``agents/{name}/.openclaw/agents/main/sessions/{id}.jsonl`` — the first line
  is a ``type: "session"`` header, then one event per line.

This module is the read side: a background poller lists those objects, parses
each session into normalized events, and projects them into PostgreSQL. Object
storage is the source of truth; the Postgres rows are a queryable projection.

Idempotency: ``trace_events`` is unique per ``(session_id, seq)`` and writes
use ``INSERT ... ON CONFLICT DO NOTHING``, so re-polling the same object —
unchanged or changed — never duplicates rows. Unchanged objects are skipped
entirely; an object whose parse previously failed is only retried after its
mtime/size changes, so a broken object cannot hot-loop the poller.

``parse_copaw_session`` is a pure function over the session JSON text so the
mapping can be unit-tested against the calibrated fixture without a database.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from repomesh.persistence import Database

from .models import TraceEventRecord, TraceSessionRecord

logger = logging.getLogger(__name__)

#: Cap per-file parsed events; a giant/compacted session must not stall the loop.
DEFAULT_MAX_EVENTS = 5000
#: Summary truncation budget (head + tail kept, middle elided).
DEFAULT_SUMMARY_LIMIT = 500
#: Poll interval, aligned with the trace page refresh cadence.
DEFAULT_INTERVAL_SECONDS = 30
#: Payload fields are truncated to stay well below a Postgres row budget.
_PAYLOAD_LIMIT = 2000
#: mtime/size both match within tolerance -> object unchanged, skip.
_UNCHANGED_TOLERANCE_SECONDS = 1.0

#: OpenClaw transcript entry types that carry no user-visible trace (extension
#: state, compaction bookkeeping) and are skipped by the parser.
_OPENCLAW_SKIP_TYPES = frozenset(
    {"custom", "custom_message", "compaction", "branch_summary"}
)

#: Output markers that classify a tool result as failed.
_ERROR_MARKERS = (
    "command failed with exit code",
    "traceback",
    "authorization denied",
    "permission denied",
    "error: unknown command",
    "no such file or directory",
)


@dataclass(frozen=True, slots=True)
class TraceEventDraft:
    seq: int
    ts: datetime | None
    event_type: str
    name: str
    role: str | None
    summary: str | None
    token_count: int | None = None
    latency_ms: int | None = None
    status: str = "ok"
    payload: dict | None = None


@dataclass(frozen=True, slots=True)
class ParsedSession:
    events: tuple[TraceEventDraft, ...]
    #: Non-fatal note (e.g. event cap hit); fatal JSON errors raise ValueError.
    error: str | None = None


@dataclass(frozen=True, slots=True)
class TraceObject:
    key: str
    mtime: datetime
    size: int


class TraceSource(Protocol):
    async def list_objects(self) -> list[TraceObject]: ...

    async def read_object(self, key: str) -> bytes | None: ...


# ---------------------------------------------------------------------------
# Pure parser
# ---------------------------------------------------------------------------


def parse_copaw_session(
    text: str,
    *,
    max_events: int = DEFAULT_MAX_EVENTS,
    summary_limit: int = DEFAULT_SUMMARY_LIMIT,
) -> ParsedSession:
    """Parse one CoPaw session file into normalized trace events.

    Raises ``ValueError`` when the whole object is unparseable (caller records
    it as ``parsing_error`` and stops retrying until the object changes).
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"session JSON 解析失败: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("session 顶层不是对象")

    agent = data.get("agent")
    memory = agent.get("memory") if isinstance(agent, dict) else None
    content = memory.get("content") if isinstance(memory, dict) else None
    if not isinstance(content, list):
        return ParsedSession(events=())

    events: list[dict] = []
    pending: dict[str, dict] = {}  # tool_use call_id -> event dict
    truncated = False

    for entry in content:
        msg = entry[0] if isinstance(entry, list) and entry else None
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        ts = _parse_ts(msg.get("timestamp"))
        blocks = msg.get("content")
        if not isinstance(blocks, list):
            continue

        if role == "assistant":
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    if not _maybe_emit(events, _tool_use_event(block, ts), max_events):
                        truncated = True
                        break
                    call_id = str(block.get("id") or "")
                    if call_id:
                        pending[call_id] = events[-1]
                elif block.get("type") == "text":
                    text = str(block.get("text") or "").strip()
                    if text and not _maybe_emit(
                        events, _chat_event("assistant", text, ts), max_events
                    ):
                        truncated = True
                        break
            if truncated:
                break
        elif role == "user":
            text = "\n".join(_text_blocks(blocks)).strip()
            if not text:
                continue
            if not _maybe_emit(events, _user_event(text, ts), max_events):
                truncated = True
                break
        elif role == "system":
            for block in blocks:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                call_id = str(block.get("id") or "")
                event = pending.pop(call_id, None)
                if event is not None:
                    _apply_result(event, block)

    drafts = tuple(TraceEventDraft(**event) for event in events)
    note = f"事件数超限，已截断至 {max_events} 条" if truncated else None
    return ParsedSession(events=drafts, error=note)


def parse_openclaw_session(
    text: str,
    *,
    max_events: int = DEFAULT_MAX_EVENTS,
    summary_limit: int = DEFAULT_SUMMARY_LIMIT,
) -> ParsedSession:
    """Parse one OpenClaw JSONL transcript into normalized trace events.

    OpenClaw (and its underlying Pi session manager) persists a session as a
    line-delimited transcript. The first line is a ``type: "session"`` header;
    every later line is one event. Both transcript shapes seen in the wild are
    accepted:

    - ``{"type": "message", "message": {"role": ..., "content": [...]}}``
      (the OpenClaw Gateway format), and
    - a bare ``{"role": ..., "content": [...]}`` message (the Pi session
      manager format).

    Content blocks map like CoPaw: ``text`` → chat events, ``tool_use`` →
    tool/skill/mcp events, ``tool_result`` → status/summary merged onto the
    matching tool event. Timestamps may be Unix epoch milliseconds (the OpenClaw
    default) or ISO-8601 strings.

    Raises ``ValueError`` only when the object contains no JSONL line at all;
    individual malformed lines are skipped so a partial write does not destroy
    the whole session projection.
    """
    events: list[dict] = []
    pending: dict[str, dict] = {}  # tool_use call_id -> event dict
    truncated = False
    line_count = 0

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        line_count += 1
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        if entry.get("type") == "session":
            continue
        if entry.get("type") in _OPENCLAW_SKIP_TYPES:
            continue

        message = entry.get("message")
        if isinstance(message, dict):
            role = message.get("role")
            blocks = message.get("content")
            ts = _parse_ts(entry.get("timestamp") or message.get("timestamp"))
        else:
            role = entry.get("role")
            blocks = entry.get("content")
            ts = _parse_ts(entry.get("timestamp"))
        if role not in ("user", "assistant", "tool") or not isinstance(blocks, list):
            continue

        if role == "assistant":
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    if not _maybe_emit(events, _tool_use_event(block, ts), max_events):
                        truncated = True
                        break
                    call_id = str(block.get("id") or "")
                    if call_id:
                        pending[call_id] = events[-1]
                elif block.get("type") == "text":
                    text = str(block.get("text") or "").strip()
                    if text and not _maybe_emit(
                        events, _chat_event("assistant", text, ts), max_events
                    ):
                        truncated = True
                        break
            if truncated:
                break
        elif role == "user":
            text = "\n".join(_text_blocks(blocks)).strip()
            if not text:
                continue
            if not _maybe_emit(events, _user_event(text, ts), max_events):
                truncated = True
                break
        elif role == "tool":
            for block in blocks:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                # Anthropic-style block: tool_result carries tool_use_id;
                # the Pi session manager writes a plain id.
                call_id = str(block.get("tool_use_id") or block.get("id") or "")
                event = pending.pop(call_id, None)
                if event is not None:
                    _apply_result(event, block)

    if line_count == 0:
        raise ValueError("OpenClaw transcript 为空")
    drafts = tuple(TraceEventDraft(**event) for event in events)
    note = f"事件数超限，已截断至 {max_events} 条" if truncated else None
    return ParsedSession(events=drafts, error=note)


def classify_tool(name: str) -> str:
    """Map a tool_use name to its trace event type."""
    if name.startswith("skill_"):
        return "skill"
    if name.startswith("mcporter.") or name.startswith("repomesh-task-control"):
        return "mcp"
    # 注：memory_search / rag_search 等检索类工具一律归 tool——平台自身没有 RAG
    # 检索链路，保留「rag」事件类型会误导为平台接入了检索增强。
    return "tool"


def _maybe_emit(events: list[dict], event: dict, max_events: int) -> bool:
    if len(events) >= max_events:
        return False
    event["seq"] = len(events) + 1
    events.append(event)
    return True


def _tool_use_event(block: dict, ts: datetime | None) -> dict:
    name = str(block.get("name") or "tool")
    event_type = classify_tool(name)
    payload: dict = {}
    raw = block.get("raw_input")
    if isinstance(raw, str) and raw:
        payload["raw_input"] = _truncate(raw, _PAYLOAD_LIMIT)
    else:
        raw_input = block.get("input")
        if raw_input is not None:
            if isinstance(raw_input, str):
                text = raw_input
            else:
                text = json.dumps(raw_input, ensure_ascii=False)
            payload["input"] = _truncate(text, _PAYLOAD_LIMIT)
    call_id = block.get("id")
    if call_id:
        payload["call_id"] = str(call_id)
    return {
        "ts": ts,
        "event_type": event_type,
        "name": name,
        "role": "tool",
        "summary": None,
        "status": "skipped",
        "payload": payload or None,
    }


def _apply_result(event: dict, block: dict) -> None:
    output_text = _tool_result_text(block)
    if output_text:
        event["status"] = "error" if _looks_like_error(output_text) else "ok"
        event["summary"] = _truncate(output_text, DEFAULT_SUMMARY_LIMIT)
        payload = dict(event["payload"] or {})
        payload["output"] = _truncate(output_text, _PAYLOAD_LIMIT)
        event["payload"] = payload or None


def _chat_event(role: str, text: str, ts: datetime | None) -> dict:
    return {
        "ts": ts,
        "event_type": "chat",
        "name": role,
        "role": role,
        "summary": _truncate(text, DEFAULT_SUMMARY_LIMIT),
        "status": "ok",
        "payload": None,
    }


def _user_event(text: str, ts: datetime | None) -> dict:
    """A user message is a task event when it embeds a collaboration payload."""
    payload = _collaboration_payload(text)
    if payload is not None:
        summary = payload.get("body")
        if not isinstance(summary, str):
            summary = payload.get("subject") or text
        kept = {
            key: payload[key]
            for key in (
                "kind",
                "schema",
                "task_id",
                "project_id",
                "repository_id",
                "correlation_id",
                "subject",
                "message_id",
                "sender_agent_id",
                "recipient_agent_id",
            )
            if key in payload
        }
        return {
            "ts": ts,
            "event_type": "task",
            "name": "task.assignment",
            "role": "user",
            "summary": _truncate(str(summary), DEFAULT_SUMMARY_LIMIT),
            "status": "ok",
            "payload": kept or None,
        }
    return _chat_event("user", text, ts)


def _collaboration_payload(text: str) -> dict | None:
    if "repomesh.collaboration.v1" not in text:
        return None
    start = 0
    while True:
        start = text.find("{", start)
        if start < 0:
            return None
        try:
            payload = json.loads(text[start:])
        except json.JSONDecodeError:
            start += 1
            continue
        if isinstance(payload, dict) and payload.get("schema") == "repomesh.collaboration.v1":
            return payload
        start += 1


def _text_blocks(blocks: list) -> list[str]:
    parts = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return parts


def _tool_result_text(block: dict) -> str:
    # CoPaw system messages carry the result as ``output``; OpenClaw
    # tool_result blocks carry it as ``content``.
    out = block.get("output")
    if out is None:
        out = block.get("content")
    if isinstance(out, str):
        return out
    if isinstance(out, list):
        parts = []
        for item in out:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return ""


def _looks_like_error(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _ERROR_MARKERS)


def _parse_ts(value: object) -> datetime | None:
    # OpenClaw timestamps are Unix epoch milliseconds (numbers); CoPaw uses
    # string timestamps. Accept both, and plain epoch seconds as a fallback.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value <= 0:
            return None
        seconds = value / 1000.0 if value >= 100_000_000_000 else float(value)
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        return None


def _truncate(text: str, limit: int) -> str:
    if limit <= 0 or not text:
        return ""
    if len(text) <= limit:
        return text
    head = limit * 3 // 4
    tail = limit - head - 1
    return text[:head] + "…" + (text[-tail:] if tail > 0 else "")


# ---------------------------------------------------------------------------
# Session key helpers
# ---------------------------------------------------------------------------


def is_session_key(key: str) -> bool:
    """True for a CoPaw or OpenClaw transcript object key.

    - CoPaw:    ``agents/{name}/.copaw/workspaces/{ws}/sessions/*.json``
    - OpenClaw: ``agents/{name}/.openclaw/agents/main/sessions/*.jsonl``

    The OpenClaw ``sessions.json`` index is metadata, not a transcript, and is
    always skipped.
    """
    if not key or key.endswith("sessions.json"):
        return False
    parts = key.split("/")
    if len(parts) < 7 or parts[0] != "agents" or "/sessions/" not in key:
        return False
    return (key.endswith(".json") and "/.copaw/workspaces/" in key) or (
        key.endswith(".jsonl") and "/.openclaw/" in key
    )


def parse_session_key(key: str) -> dict:
    parts = key.split("/")
    if key.endswith(".jsonl"):
        return {
            "agent_name": parts[1],
            "session_id": parts[-1][:-6],
            "runtime": "openclaw",
        }
    return {
        "agent_name": parts[1],
        "session_id": parts[-1][:-5],
        "runtime": "copaw",
    }


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class TraceStore:
    """Projection store for parsed sessions (Postgres, SQLite in tests)."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get_object_state(self, source_key: str) -> dict | None:
        async with self._database.transaction() as session:
            row = (
                await session.execute(
                    select(
                        TraceSessionRecord.object_mtime,
                        TraceSessionRecord.object_size,
                        TraceSessionRecord.parsing_error,
                    ).where(TraceSessionRecord.source_key == source_key)
                )
            ).first()
            if row is None:
                return None
            return {
                "object_mtime": row[0],
                "object_size": row[1],
                "parsing_error": row[2],
            }

    async def upsert_session(
        self,
        *,
        source_key: str,
        session_id: str,
        agent_name: str,
        runtime: str,
        object_mtime: datetime,
        object_size: int,
        parsing_error: str | None,
        parsed_at: datetime | None,
    ) -> UUID:
        async with self._database.transaction() as session:
            existing = await session.scalar(
                select(TraceSessionRecord.id).where(
                    TraceSessionRecord.source_key == source_key
                )
            )
            if existing is not None:
                record = await session.get(TraceSessionRecord, existing)
                record.session_id = session_id
                record.agent_name = agent_name
                record.runtime = runtime
                record.object_mtime = object_mtime
                record.object_size = object_size
                record.parsing_error = parsing_error
                record.parsed_at = parsed_at
                await session.flush()
                return record.id
            record = TraceSessionRecord(
                id=uuid4(),
                session_id=session_id,
                agent_name=agent_name,
                runtime=runtime,
                source_key=source_key,
                object_mtime=object_mtime,
                object_size=object_size,
                parsing_error=parsing_error,
                parsed_at=parsed_at,
                # Explicit value, not server_default: SQLite renders the
                # DEFAULT as CURRENT_TIMESTAMP (no fractional seconds), while
                # bound parameters keep 6-digit microseconds. A keyset cursor
                # bound as ``...HH:MM:SS.ffffff`` then string-compares *less*
                # than every stored row, making ``< first_seen_at`` degenerate.
                # Assigning here keeps the stored and bound formats identical.
                first_seen_at=datetime.now(UTC),
            )
            session.add(record)
            await session.flush()
            return record.id

    async def ingest_events(
        self, session_row_id: UUID, events: list[TraceEventDraft]
    ) -> int:
        """Project events; conflicts on (session_id, seq) are swallowed.

        Also refreshes the session's ``event_count`` in the same transaction.
        """
        if not events:
            return 0
        rows = [
            {
                "id": uuid4(),
                "session_id": session_row_id,
                "seq": event.seq,
                "ts": event.ts or datetime.now(UTC),
                "event_type": event.event_type,
                "name": event.name,
                "role": event.role,
                "summary": event.summary,
                "token_count": event.token_count,
                "latency_ms": event.latency_ms,
                "status": event.status,
                "payload": event.payload,
            }
            for event in events
        ]
        async with self._database.transaction() as session:
            insert_cls = (
                sqlite_insert
                if self._database.engine.dialect.name == "sqlite"
                else pg_insert
            )
            stmt = (
                insert_cls(TraceEventRecord)
                .values(rows)
                .on_conflict_do_nothing(index_elements=["session_id", "seq"])
            )
            result = await session.execute(stmt)
            inserted = int(result.rowcount or 0)
            count = await session.scalar(
                select(func.count(TraceEventRecord.id)).where(
                    TraceEventRecord.session_id == session_row_id
                )
            )
            await session.execute(
                update(TraceSessionRecord)
                .where(TraceSessionRecord.id == session_row_id)
                .values(event_count=int(count or 0))
            )
            return inserted


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


class LocalTraceSource:
    """Reads session objects from a pre-synced local storage root."""

    def __init__(self, root: Path) -> None:
        self._root = root

    async def list_objects(self) -> list[TraceObject]:
        return await asyncio.to_thread(self._list_objects_sync)

    def _list_objects_sync(self) -> list[TraceObject]:
        if not self._root.is_dir():
            return []
        found: list[TraceObject] = []
        for pattern in ("*.json", "*.jsonl"):
            for path in self._root.rglob(pattern):
                if not path.is_file():
                    continue
                key = path.relative_to(self._root).as_posix()
                if not is_session_key(key):
                    continue
                stat = path.stat()
                found.append(
                    TraceObject(
                        key=key,
                        mtime=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                        size=stat.st_size,
                    )
                )
        return sorted(found, key=lambda obj: obj.key)

    async def read_object(self, key: str) -> bytes | None:
        path = self._root / Path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except OSError:
            logger.warning("读取本地会话对象失败: %s", key)
            return None


class MinioTraceSource:
    """Reads session objects directly from MinIO (bucket ``agents/`` prefix)."""

    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
    ) -> None:
        # minio is an optional dependency of the runtime integrations; import
        # lazily so tests and local-only deployments never need it.
        from minio import Minio

        secure = endpoint.startswith("https://")
        host = endpoint.removeprefix("https://").removeprefix("http://").rstrip("/")
        self._client = Minio(
            host, access_key=access_key, secret_key=secret_key, secure=secure
        )
        self._bucket = bucket

    async def list_objects(self) -> list[TraceObject]:
        return await asyncio.to_thread(self._list_objects_sync)

    def _list_objects_sync(self) -> list[TraceObject]:
        try:
            if not self._client.bucket_exists(self._bucket):
                logger.warning("MinIO bucket %s 不存在", self._bucket)
                return []
            objects = self._client.list_objects(
                self._bucket, prefix="agents/", recursive=True
            )
        except Exception:
            logger.exception("列举 MinIO 会话对象失败")
            return []
        found: list[TraceObject] = []
        for item in objects:
            if not is_session_key(item.object_name):
                continue
            mtime = item.last_modified or datetime.now(UTC)
            if mtime.tzinfo is None:
                mtime = mtime.replace(tzinfo=UTC)
            found.append(TraceObject(key=item.object_name, mtime=mtime, size=item.size or 0))
        return sorted(found, key=lambda obj: obj.key)

    async def read_object(self, key: str) -> bytes | None:
        return await asyncio.to_thread(self._read_object_sync, key)

    def _read_object_sync(self, key: str) -> bytes | None:
        try:
            response = self._client.get_object(self._bucket, key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()
        except Exception:
            logger.exception("读取 MinIO 会话对象失败: %s", key)
            return None


# ---------------------------------------------------------------------------
# Background poller
# ---------------------------------------------------------------------------


class TraceIngester:
    """Background loop projecting new/changed session objects into Postgres.

    An object is skipped when its mtime and size are unchanged since the last
    poll (a previously recorded parse failure is left alone until the object
    actually changes, so broken objects do not hot-loop the poller).
    """

    def __init__(
        self,
        store: TraceStore,
        source: TraceSource,
        *,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        self._store = store
        self._source = source
        self._interval = interval_seconds
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="trace-ingester")

    async def close(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def ingest_once(self) -> dict:
        stats = {"seen": 0, "parsed": 0, "inserted": 0, "unchanged": 0, "errors": 0}
        for obj in await self._source.list_objects():
            stats["seen"] += 1
            state = await self._store.get_object_state(obj.key)
            if state is not None:
                # Some drivers (SQLite, some asyncpg configs) return naive
                # datetimes; session files are always UTC, so normalize.
                stored_mtime = state["object_mtime"]
                if stored_mtime.tzinfo is None:
                    stored_mtime = stored_mtime.replace(tzinfo=UTC)
                mtime_delta = abs((stored_mtime - obj.mtime).total_seconds())
                if mtime_delta < _UNCHANGED_TOLERANCE_SECONDS and state["object_size"] == obj.size:
                    stats["unchanged"] += 1
                    continue
            raw = await self._source.read_object(obj.key)
            if raw is None:
                continue
            meta = parse_session_key(obj.key)
            parsed_at = datetime.now(UTC)
            try:
                raw_text = raw.decode("utf-8", errors="replace")
                if meta["runtime"] == "openclaw":
                    parsed = parse_openclaw_session(raw_text)
                else:
                    parsed = parse_copaw_session(raw_text)
            except ValueError as exc:
                await self._store.upsert_session(
                    source_key=obj.key,
                    session_id=meta["session_id"],
                    agent_name=meta["agent_name"],
                    runtime=meta["runtime"],
                    object_mtime=obj.mtime,
                    object_size=obj.size,
                    parsing_error=str(exc),
                    parsed_at=None,
                )
                stats["errors"] += 1
                continue
            row_id = await self._store.upsert_session(
                source_key=obj.key,
                session_id=meta["session_id"],
                agent_name=meta["agent_name"],
                runtime=meta["runtime"],
                object_mtime=obj.mtime,
                object_size=obj.size,
                parsing_error=parsed.error,
                parsed_at=parsed_at,
            )
            inserted = await self._store.ingest_events(row_id, list(parsed.events))
            stats["parsed"] += 1
            stats["inserted"] += inserted
        return stats

    async def _run(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._interval)
                try:
                    await self.ingest_once()
                except Exception:
                    logger.exception("trace ingest 轮询失败")
        except asyncio.CancelledError:
            pass
