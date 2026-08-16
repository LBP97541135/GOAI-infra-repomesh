"""Read-side queries over ``observability.trace_sessions`` / ``trace_events``.

The trace page needs three shapes:

- ``list_sessions``: the session list, newest first. Keyset-paginated on
  ``(first_seen_at, id)`` — the cursor is the previous page's last session id,
  and the filter re-derives that row's sort values, so the page never skips or
  duplicates rows when a new session lands mid-pagination.
- ``session_events``: one session's timeline in trace order (``seq`` asc).
  ``seq`` is the file position, so pagination is a plain ``seq > after`` bound.
- ``list_events``: a cross-session event stream, newest first, keyset-paginated
  on ``(ts, id)`` with ``event_type`` / ``status`` / ``agent_name`` filters.

All ordering is deterministic: the id tiebreak is a random UUID, which only
needs to be stable *within* a page sequence, not meaningful across deployments.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, case, func, or_, select

from repomesh.persistence import Database

from .models import (
    LLMUsageRecord,
    LogEntryRecord,
    TraceEventRecord,
    TraceSessionRecord,
)

#: Event classification vocabulary emitted by the ingester; the API rejects
#: anything else so the frontend can render filters without unknown buckets.
VALID_EVENT_TYPES = ("chat", "tool", "skill", "mcp", "task")
#: Tool events stay "skipped" until their result block arrives; a result
#: containing an error marker flips the status to "error".
VALID_STATUSES = ("ok", "error", "skipped")

#: Padding applied around an issue's inferred activity window when matching
#: trace sessions. Trace sessions are keyed by task, not issue, so the only
#: honest link is temporal overlap — this slack absorbs clock/edge skew.
_ISSUE_SLACK = timedelta(minutes=15)


class TraceQueryStore:
    """Read-only projections over ingested trace rows."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def list_sessions(
        self,
        *,
        agent_name: str | None = None,
        limit: int = 50,
        cursor: UUID | None = None,
    ) -> dict:
        """Session projections, newest first, keyset-paginated.

        Returns ``{"sessions": [...], "next_cursor": UUID | None}``.
        ``next_cursor`` is the last returned row's id and is set only when a
        further page exists (one extra row is fetched to detect it).
        """
        limit = max(1, min(limit, 200))
        conds = []
        if agent_name:
            conds.append(TraceSessionRecord.agent_name == agent_name)
        if cursor is not None:
            last = await self._session_row(cursor)
            if last is None:
                return {"sessions": [], "next_cursor": None}
            conds.append(
                or_(
                    TraceSessionRecord.first_seen_at < last.first_seen_at,
                    and_(
                        TraceSessionRecord.first_seen_at == last.first_seen_at,
                        TraceSessionRecord.id < last.id,
                    ),
                )
            )
        stmt = (
            select(TraceSessionRecord)
            .where(*conds)
            .order_by(
                TraceSessionRecord.first_seen_at.desc(),
                TraceSessionRecord.id.desc(),
            )
            .limit(limit + 1)
        )
        async with self._database.transaction() as session:
            rows = (await session.execute(stmt)).scalars().all()
        has_more = len(rows) > limit
        page = list(rows[:limit])
        return {
            "sessions": [_session_out(row) for row in page],
            "next_cursor": page[-1].id if has_more and page else None,
        }

    async def issue_groups(self, *, limit: int = 100) -> list[dict]:
        """Issues with an inferred activity window, each annotated with the
        number of trace sessions whose first-seen time falls inside the
        window padded by ``_ISSUE_SLACK``.

        This is an *approximate* attribution on purpose: trace sessions are
        keyed by task, not issue, so temporal overlap is the only honest
        signal available without a cross-module schema change. The trace
        page's "by issue" view consumes this.
        """
        limit = max(1, min(limit, 200))
        async with self._database.transaction() as session:
            windows = await self._issue_windows(session)
            if not windows:
                return []
            lo = min(w["start"] for w in windows.values()) - _ISSUE_SLACK
            hi = max(w["end"] for w in windows.values()) + _ISSUE_SLACK
            sessions = (
                await session.execute(
                    select(
                        TraceSessionRecord.id, TraceSessionRecord.first_seen_at
                    ).where(TraceSessionRecord.first_seen_at.between(lo, hi))
                )
            ).all()
        groups: list[dict] = []
        for key, win in windows.items():
            start = win["start"] - _ISSUE_SLACK
            end = win["end"] + _ISSUE_SLACK
            hits = [(sid, at) for sid, at in sessions if start <= at <= end]
            if not hits:
                continue
            groups.append(
                {
                    "issue_id": UUID(key),
                    "activity_start": win["start"],
                    "activity_end": win["end"],
                    "suspected_sessions": len(hits),
                    "last_session_at": max(at for _, at in hits),
                }
            )
        groups.sort(key=lambda g: g["last_session_at"], reverse=True)
        return groups[:limit]

    async def sessions_for_issue(
        self,
        *,
        issue_id: UUID,
        limit: int = 50,
        cursor: UUID | None = None,
    ) -> dict:
        """Sessions whose first-seen time falls inside the issue's inferred
        activity window (padded by ``_ISSUE_SLACK``), newest first and
        keyset-paginated exactly like ``list_sessions``.

        Returns ``{"sessions": [...], "next_cursor": UUID | None}``. An
        issue with no activity window yields an empty page.
        """
        limit = max(1, min(limit, 200))
        async with self._database.transaction() as session:
            windows = await self._issue_windows(session)
            win = windows.get(str(issue_id))
            if win is None:
                return {"sessions": [], "next_cursor": None}
            conds = [
                TraceSessionRecord.first_seen_at.between(
                    win["start"] - _ISSUE_SLACK, win["end"] + _ISSUE_SLACK
                )
            ]
            if cursor is not None:
                last = await self._session_row(cursor)
                if last is None:
                    return {"sessions": [], "next_cursor": None}
                conds.append(
                    or_(
                        TraceSessionRecord.first_seen_at < last.first_seen_at,
                        and_(
                            TraceSessionRecord.first_seen_at == last.first_seen_at,
                            TraceSessionRecord.id < last.id,
                        ),
                    )
                )
            stmt = (
                select(TraceSessionRecord)
                .where(*conds)
                .order_by(
                    TraceSessionRecord.first_seen_at.desc(),
                    TraceSessionRecord.id.desc(),
                )
                .limit(limit + 1)
            )
            rows = (await session.execute(stmt)).scalars().all()
        has_more = len(rows) > limit
        page = list(rows[:limit])
        return {
            "sessions": [_session_out(row) for row in page],
            "next_cursor": page[-1].id if has_more and page else None,
        }

    async def _issue_windows(self, session) -> dict[str, dict]:
        """Per-issue activity windows as the union of usage and log timestamps
        (rows without an issue are excluded). Shared by the two approximate
        issue views above.
        """
        usage = (
            await session.execute(
                select(
                    LLMUsageRecord.issue_id,
                    func.min(LLMUsageRecord.created_at),
                    func.max(LLMUsageRecord.created_at),
                )
                .where(LLMUsageRecord.issue_id.is_not(None))
                .group_by(LLMUsageRecord.issue_id)
            )
        ).all()
        logs = (
            await session.execute(
                select(
                    LogEntryRecord.issue_id,
                    func.min(LogEntryRecord.ts),
                    func.max(LogEntryRecord.ts),
                )
                .where(LogEntryRecord.issue_id.is_not(None))
                .group_by(LogEntryRecord.issue_id)
            )
        ).all()
        windows: dict[str, dict] = {}
        for issue_id, lo, hi in usage:
            if lo is None or hi is None:
                continue
            windows[str(issue_id)] = {"start": lo, "end": hi}
        for issue_id, lo, hi in logs:
            if lo is None or hi is None:
                continue
            key = str(issue_id)
            win = windows.get(key)
            if win is None:
                windows[key] = {"start": lo, "end": hi}
            else:
                win["start"] = min(win["start"], lo)
                win["end"] = max(win["end"], hi)
        return windows

    async def session_events(
        self,
        *,
        session_id: UUID,
        limit: int = 100,
        after_seq: int = 0,
    ) -> dict | None:
        """One session's events in trace order, ``seq``-keyset paginated.

        Returns ``None`` when the session id does not exist (the router maps
        that to 404); otherwise ``{"events": [...], "next_seq": int | None}``.
        """
        limit = max(1, min(limit, 500))
        async with self._database.transaction() as session:
            exists = await session.scalar(
                select(TraceSessionRecord.id).where(
                    TraceSessionRecord.id == session_id
                )
            )
            if exists is None:
                return None
            rows = (
                await session.execute(
                    select(TraceEventRecord)
                    .where(
                        TraceEventRecord.session_id == session_id,
                        TraceEventRecord.seq > after_seq,
                    )
                    .order_by(TraceEventRecord.seq.asc())
                    .limit(limit + 1)
                )
            ).scalars().all()
        has_more = len(rows) > limit
        page = list(rows[:limit])
        return {
            "events": [_event_out(row) for row in page],
            "next_seq": page[-1].seq if has_more and page else None,
        }

    async def list_events(
        self,
        *,
        event_type: str | None = None,
        status: str | None = None,
        agent_name: str | None = None,
        limit: int = 50,
        cursor: UUID | None = None,
    ) -> dict:
        """Cross-session event stream, newest first, keyset-paginated.

        Joins the owning session so each row carries ``agent_name`` and the
        external ``session_id`` for display in a global timeline. Returns
        ``{"events": [...], "next_cursor": UUID | None}``.
        """
        limit = max(1, min(limit, 500))
        conds = []
        if event_type:
            conds.append(TraceEventRecord.event_type == event_type)
        if status:
            conds.append(TraceEventRecord.status == status)
        if agent_name:
            conds.append(TraceSessionRecord.agent_name == agent_name)
        if cursor is not None:
            last = await self._event_row(cursor)
            if last is None:
                return {"events": [], "next_cursor": None}
            conds.append(
                or_(
                    TraceEventRecord.ts < last.ts,
                    and_(
                        TraceEventRecord.ts == last.ts,
                        TraceEventRecord.id < last.id,
                    ),
                )
            )
        stmt = (
            select(
                TraceEventRecord,
                TraceSessionRecord.agent_name,
                TraceSessionRecord.session_id,
            )
            .join(
                TraceSessionRecord,
                TraceEventRecord.session_id == TraceSessionRecord.id,
            )
            .where(*conds)
            .order_by(TraceEventRecord.ts.desc(), TraceEventRecord.id.desc())
            .limit(limit + 1)
        )
        async with self._database.transaction() as session:
            rows = (await session.execute(stmt)).all()
        has_more = len(rows) > limit
        page = list(rows[:limit])
        events = []
        for event, agent_name, external_session_id in page:
            item = _event_out(event)
            item["agent_name"] = agent_name
            item["session_external_id"] = external_session_id
            events.append(item)
        return {
            "events": events,
            "next_cursor": page[-1][0].id if has_more and page else None,
        }

    async def window_metrics(self, *, minutes: int) -> dict:
        """Compact trace-event snapshot over a trailing window (for alert rules).

        Only events that reached a terminal state (``ok``/``error``) count as
        calls; ``skipped`` tool events (result never arrived) are excluded from
        both the numerator and denominator. ``None`` means "no trace data in
        the window", which the evaluator treats as "not firing".
        """
        since = datetime.now(UTC) - timedelta(minutes=max(minutes, 1))
        async with self._database.transaction() as session:
            counted = case(
                (TraceEventRecord.status.in_(("ok", "error")), 1), else_=0
            )
            ok_flag = case((TraceEventRecord.status == "ok", 1), else_=0)
            totals = await session.execute(
                select(
                    func.coalesce(func.sum(counted), 0),
                    func.coalesce(func.sum(ok_flag), 0),
                ).where(TraceEventRecord.ts >= since)
            )
            calls, success_calls = totals.one()
            calls = int(calls)
            success_calls = int(success_calls)
        return {
            "trace_calls": calls,
            "trace_success_rate": round(success_calls / calls, 4) if calls else None,
            "trace_error_count": calls - success_calls,
        }

    async def _session_row(self, row_id: UUID) -> TraceSessionRecord | None:
        async with self._database.transaction() as session:
            return await session.get(TraceSessionRecord, row_id)

    async def _event_row(self, row_id: UUID) -> TraceEventRecord | None:
        async with self._database.transaction() as session:
            return await session.get(TraceEventRecord, row_id)


def _session_out(row: TraceSessionRecord) -> dict:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "agent_name": row.agent_name,
        "runtime": row.runtime,
        "source_key": row.source_key,
        "event_count": row.event_count,
        "first_seen_at": row.first_seen_at,
        "parsed_at": row.parsed_at,
        "parsing_error": row.parsing_error,
        "object_mtime": row.object_mtime,
        "object_size": row.object_size,
    }


def _event_out(row: TraceEventRecord) -> dict:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "seq": row.seq,
        "ts": row.ts,
        "event_type": row.event_type,
        "name": row.name,
        "role": row.role,
        "summary": row.summary,
        "token_count": row.token_count,
        "latency_ms": row.latency_ms,
        "status": row.status,
        "payload": row.payload,
    }
