"""Read side for captured log entries (``observability.log_entries``).

The unified-log page queries through ``GET /observe/logs`` which delegates
here. Filtering is deliberately cheap: exact level match, ``ILIKE`` prefix /
substring matches for source and full-text, and exact ``issue_id``. Pages use
the same keyset scheme as sessions/events — ``(ts DESC, id DESC)`` — so a
stable page boundary exists even when new lines arrive between requests.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from repomesh.persistence import Database

from .models import LogEntryRecord

#: Accepted ``level`` filter values (mirrors ``logging``'s level names).
VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class LogQueryStore:
    """Read model for ``observability.log_entries``."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def list_logs(
        self,
        *,
        level: str | None = None,
        source: str | None = None,
        issue_id: UUID | None = None,
        query: str | None = None,
        limit: int = 50,
        cursor: UUID | None = None,
    ) -> dict[str, Any]:
        """Return ``{"logs": [...], "next_cursor": ...}``.

        ``query`` matches against ``message`` and ``exc_info`` substrings
        (case-insensitive); ``source`` is a case-insensitive substring too so
        ``"trace_ingest"`` finds ``…infrastructure.trace_ingest``. A cursor
        pointing at a row that no longer exists returns an empty page, like
        the trace query stores.
        """
        filters = []
        if level is not None:
            filters.append(LogEntryRecord.level == level)
        if source:
            filters.append(LogEntryRecord.source.ilike(f"%{source}%"))
        if issue_id is not None:
            filters.append(LogEntryRecord.issue_id == issue_id)
        if query:
            needle = f"%{query}%"
            filters.append(
                LogEntryRecord.message.ilike(needle) | LogEntryRecord.exc_info.ilike(needle)
            )

        async with self._database.transaction() as session:
            if cursor is not None:
                anchor = await session.get(LogEntryRecord, cursor)
                if anchor is None:
                    return {"logs": [], "next_cursor": None}
                filters.append(
                    (LogEntryRecord.ts < anchor.ts)
                    | ((LogEntryRecord.ts == anchor.ts) & (LogEntryRecord.id < cursor))
                )

            rows = (
                await session.execute(
                    select(LogEntryRecord)
                    .where(*filters)
                    .order_by(LogEntryRecord.ts.desc(), LogEntryRecord.id.desc())
                    .limit(limit + 1)
                )
            ).scalars().all()

        has_more = len(rows) > limit
        page = rows[:limit]
        return {
            "logs": [_row(entry) for entry in page],
            "next_cursor": page[-1].id if has_more else None,
        }

    async def issue_groups(self, *, limit: int = 100) -> list[dict]:
        """Per-issue log counts, most recently active first.

        Rows without an issue (ambient/system lines) are excluded — they have
        no issue to group under. Used by the issue page and the logs page's
        "by issue" view.
        """
        async with self._database.transaction() as session:
            rows = (
                await session.execute(
                    select(
                        LogEntryRecord.issue_id,
                        func.count(LogEntryRecord.id),
                        func.max(LogEntryRecord.ts),
                    )
                    .where(LogEntryRecord.issue_id.is_not(None))
                    .group_by(LogEntryRecord.issue_id)
                    .order_by(func.max(LogEntryRecord.ts).desc())
                    .limit(max(1, min(limit, 500)))
                )
            ).all()
        return [
            {"issue_id": issue_id, "count": int(count), "last_at": last_at}
            for issue_id, count, last_at in rows
        ]


def _row(entry: LogEntryRecord) -> dict[str, Any]:
    ts: datetime = entry.ts
    return {
        "id": entry.id,
        "ts": ts,
        "level": entry.level,
        "source": entry.source,
        "issue_id": entry.issue_id,
        "message": entry.message,
        "exc_info": entry.exc_info,
    }


__all__ = ["LogQueryStore"]
