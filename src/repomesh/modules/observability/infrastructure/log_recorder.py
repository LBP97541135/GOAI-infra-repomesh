"""Structured log capture: logging handler → bounded queue → PostgreSQL.

Why this shape exists: RepoMesh's planning-side logs are plain ``logging``
text scattered across modules. The unified-log page wants them queryable
(level / source / issue_id / full-text), so a standard-library
``logging.Handler`` attached to the root logger normalises every record into a
``_PendingLog`` row and a background task drains the queue on the event loop —
the same bounded-queue pattern ``QueuedUsageRecorder`` uses, for the same
reason: emitting threads (including ``asyncio.to_thread`` workers) must never
block on a database that may be slow or down.

Two hard rules keep the pipeline from feeding on itself:

- ``emit`` never calls ``logging``. The queue is the only sink; a full queue
  drops the record (counted in ``dropped``) rather than stalling the caller.
- the flush task reports failures to ``sys.stderr`` instead of ``_logger`` —
  with the handler on the root logger, a logging-based error report would be
  re-queued and re-flushed forever on a permanently broken database.

``issue_id`` attribution has two channels, both read in ``emit``:

- explicit ``logging`` ``extra``: ``logger.info(..., extra={"issue_id": "…"})``.
  The value may be a UUID string or an actual ``uuid.UUID``; anything
  unparseable is stored as null rather than guessed at. Explicit extra wins.
- ambient context: when no extra is present, the handler falls back to
  ``current_usage_context`` (the discovery endpoints set it around a step,
  ``asyncio.to_thread`` copies it into worker threads), so every log emitted
  inside a discovery step inherits the step's issue without any call-site
  change. Logs outside any discovery context stay unlabelled.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import sys
import traceback
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from repomesh.modules.observability.contracts import current_usage_context
from repomesh.persistence import Database

from .models import LogEntryRecord

#: Sources stay bounded; messages and tracebacks keep their full text.
_SOURCE_MAX = 256


def _parse_uuid(value: object) -> UUID | None:
    """Best-effort ``extra={"issue_id": …}`` parsing.

    Accepts a ``uuid.UUID`` or its canonical text form; anything else (a
    short label, a malformed string, ``None``) yields ``None`` — the row is
    stored without issue attribution rather than with a guessed one.
    """
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _exc_text(record: logging.LogRecord) -> str | None:
    """Render ``record.exc_info`` to text without triggering ``format``.

    ``traceback.format_exception`` needs the tuple still populated; the
    handler may be called after logging already formatted the record, so a
    bare exception must not take the capture pipeline down.
    """
    if not record.exc_info:
        return None
    try:
        return "".join(traceback.format_exception(*record.exc_info)).rstrip()
    except Exception:  # noqa: BLE001 - capture must never raise
        return None


@dataclass(slots=True)
class _PendingLog:
    """A normalised row, frozen at emit time on the emitting thread."""

    id: UUID
    ts: datetime
    level: str
    source: str
    issue_id: UUID | None
    message: str
    exc_info: str | None


class LogCollectorHandler(logging.Handler):
    """Root-logger handler that enqueues every record above ``level``.

    Attached/detached by ``LogRecorder``; this class itself is a plain
    ``logging.Handler`` so it participates in the standard library's
    ``handleError`` safety net (an unexpected ``emit`` failure is printed to
    stderr by logging itself, not re-queued).
    """

    def __init__(self, pending_queue: queue.Queue[_PendingLog], *, level: int) -> None:
        super().__init__(level=level)
        self._pending_queue = pending_queue
        self.dropped = 0

    def emit(self, record: logging.LogRecord) -> None:
        # Explicit extra wins; otherwise inherit the ambient discovery step's
        # issue (set around the step by the discovery endpoints and copied
        # into worker threads by asyncio.to_thread).
        issue_id = _parse_uuid(record.__dict__.get("issue_id"))
        if issue_id is None:
            context = current_usage_context.get()
            if context is not None:
                issue_id = context.issue_id
        pending = _PendingLog(
            id=uuid4(),
            ts=datetime.fromtimestamp(record.created, tz=UTC),
            level=record.levelname,
            source=record.name[:_SOURCE_MAX],
            issue_id=issue_id,
            message=record.getMessage(),
            exc_info=_exc_text(record),
        )
        try:
            self._pending_queue.put_nowait(pending)
        except queue.Full:
            # The queue is bounded and the caller is some other thread's
            # logging call — it must not wait on us. Count the loss instead of
            # masking it; the recorder folds this into its public counter.
            self.dropped += 1


class LogRecorder:
    """Collects process logs and flushes them to the database.

    Satisfies the composition root's ``BackgroundService`` protocol
    (``start`` / ``close``, driven by the application lifespan) and owns the
    root-logger attachment so capture only runs while the service is started.
    """

    def __init__(
        self,
        database: Database,
        *,
        flush_interval_seconds: float = 2.0,
        max_queue: int = 10_000,
        level: int = logging.INFO,
    ) -> None:
        self._database = database
        self._flush_interval = flush_interval_seconds
        self._queue: queue.Queue[_PendingLog] = queue.Queue(maxsize=max_queue)
        self._handler = LogCollectorHandler(self._queue, level=level)
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._attached = False
        #: How many records were dropped because the queue filled up. A full
        #: queue means the flush task cannot keep up; the loss is observable
        #: rather than silent, matching ``QueuedUsageRecorder.dropped``.
        self.dropped = 0

    # -- BackgroundService protocol (event loop) --------------------------

    async def start(self) -> None:
        self._attach()
        self._stop.clear()
        self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
        self._detach()
        # Final drain: a close() must not lose the last interval's records.
        await self._flush()

    async def _run(self) -> None:
        while not self._stop.is_set():
            await self._flush()
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._flush_interval)

    # -- Attachment --------------------------------------------------------

    def _attach(self) -> None:
        if self._attached:
            return
        logging.getLogger().addHandler(self._handler)
        self._attached = True

    def _detach(self) -> None:
        if not self._attached:
            return
        logging.getLogger().removeHandler(self._handler)
        self._attached = False

    # -- Flush --------------------------------------------------------------

    async def _flush(self) -> None:
        pending: list[_PendingLog] = []
        while True:
            try:
                pending.append(self._queue.get_nowait())
            except queue.Empty:
                break
        if not pending:
            return
        # Fold any drops the handler saw (full queue while we were flushing)
        # into the public counter.
        self.dropped += self._handler.dropped
        self._handler.dropped = 0
        rows = [
            LogEntryRecord(
                id=item.id,
                ts=item.ts,
                level=item.level,
                source=item.source,
                issue_id=item.issue_id,
                message=item.message,
                exc_info=item.exc_info,
            )
            for item in pending
        ]
        try:
            async with self._database.transaction() as session:
                session.add_all(rows)
        except Exception as error:  # noqa: BLE001 - never take the flush task down
            # stderr, not logging: with the handler attached to the root
            # logger, a logging-based report would loop back into the queue.
            sys.stderr.write(
                f"[log_recorder] failed to flush {len(rows)} log_entries: {error!r}\n"
            )


__all__ = ["LogCollectorHandler", "LogRecorder"]
