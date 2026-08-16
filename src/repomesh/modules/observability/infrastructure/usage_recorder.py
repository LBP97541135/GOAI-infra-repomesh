"""Thread-safe usage collection + background flush to PostgreSQL.

Why this shape exists: planning-phase LLM calls run synchronously on
``asyncio.to_thread`` worker threads (the discovery chain's four steps), and
the project's only database driver is async (``asyncpg`` + async SQLAlchemy).
A session cannot be created or used from a worker thread, so the sink lands in
a bounded thread-safe queue and a background task drains it on the event loop,
where ``Database.transaction()`` is legal.

The queue also decouples LLM latency from write latency: a stalled database
never makes the model call wait, and a burst of classification calls (N
candidates, one call each) flushes as a batch instead of N sequential writes.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from repomesh.modules.observability.contracts import current_usage_context
from repomesh.persistence import Database

from .models import LLMUsageRecord

_logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _PendingUsage:
    """A normalised row, frozen at record time on the worker thread."""

    id: UUID
    created_epoch: float
    provider: str
    model: str
    operation: str
    issue_id: UUID | None
    discovery_step: int | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    finish_reason: str | None
    latency_ms: int | None
    status: str


def _int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


class QueuedUsageRecorder:
    """Collects LLM usage from worker threads and flushes it to the database.

    Satisfies both the ``UsageRecorder`` port (``record``, called from worker
    threads) and the composition root's ``BackgroundService`` protocol
    (``start`` / ``close``, driven by the application lifespan).
    """

    def __init__(
        self,
        database: Database,
        *,
        flush_interval_seconds: float = 2.0,
        max_queue: int = 10_000,
    ) -> None:
        self._database = database
        self._flush_interval = flush_interval_seconds
        self._queue: queue.Queue[_PendingUsage] = queue.Queue(maxsize=max_queue)
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        #: How many records were dropped because the queue filled up. Exposed
        #: so the loss is observable rather than silent; a full queue means
        #: the flush task cannot keep up and should be told apart from a quiet
        #: DB by whoever is watching.
        self.dropped = 0

    # -- UsageRecorder port (worker threads) ------------------------------

    def record(self, usage: dict[str, object]) -> None:
        """Normalise one observation and enqueue it.

        Called from ``asyncio.to_thread`` workers. Never blocks the caller:
        a full queue drops the record (counted in ``self.dropped``) rather
        than stalling an in-flight model call.
        """
        context = current_usage_context.get()
        latency = usage.get("latency_ms")
        pending = _PendingUsage(
            id=uuid4(),
            created_epoch=time.time(),
            provider=str(usage.get("provider") or "unknown")[:32],
            model=str(usage.get("model") or "unknown")[:64],
            operation=str(usage.get("operation") or "chat")[:32],
            issue_id=context.issue_id if context is not None else None,
            discovery_step=context.step if context is not None else None,
            prompt_tokens=_int(usage.get("prompt_tokens")),
            completion_tokens=_int(usage.get("completion_tokens")),
            total_tokens=_int(usage.get("total_tokens")),
            finish_reason=(
                str(usage["finish_reason"])[:32] if usage.get("finish_reason") else None
            ),
            latency_ms=_int(latency) if latency is not None else None,
            status=str(usage.get("status") or "ok")[:16],
        )
        try:
            self._queue.put_nowait(pending)
        except queue.Full:
            self.dropped += 1

    # -- BackgroundService protocol (event loop) --------------------------

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
        # Final drain: a close() must not lose the last interval's records.
        await self._flush()

    async def _run(self) -> None:
        while not self._stop.is_set():
            await self._flush()
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._flush_interval)

    async def _flush(self) -> None:
        pending: list[_PendingUsage] = []
        while True:
            try:
                pending.append(self._queue.get_nowait())
            except queue.Empty:
                break
        if not pending:
            return
        rows = [
            LLMUsageRecord(
                id=item.id,
                created_at=datetime.fromtimestamp(item.created_epoch, tz=UTC),
                provider=item.provider,
                model=item.model,
                operation=item.operation,
                issue_id=item.issue_id,
                discovery_step=item.discovery_step,
                prompt_tokens=item.prompt_tokens,
                completion_tokens=item.completion_tokens,
                total_tokens=item.total_tokens,
                finish_reason=item.finish_reason,
                latency_ms=item.latency_ms,
                status=item.status,
            )
            for item in pending
        ]
        try:
            async with self._database.transaction() as session:
                session.add_all(rows)
        except Exception:  # noqa: BLE001 - never take the flush task down
            _logger.exception("failed to flush %d llm_usage rows", len(rows))
