#!/usr/bin/env python3
"""At-least-once inbound delivery, turned into at-most-once execution.

Three concerns that are easy to conflate and expensive to merge later:

``Cursor``
    The Matrix ``since`` token. Advances only after the batch has been durably
    recorded -- never on read. A cursor that advances on read turns a crash
    into silently dropped work.

``SeenEvents``
    Bounded, persisted set of already-handled event ids. Because the cursor is
    ack-driven, a crash replays the tail of a batch; this is what makes the
    replay harmless.

``TurnLedger``
    Idempotency for the expensive side effect. A crash between "spawned the
    agent" and "recorded the result" must not spawn a second agent against the
    same workspace. Keyed by ``(task_id, trigger_event_id)``.

Everything is one JSON file written atomically, so a torn write cannot leave
the cursor ahead of the seen-set.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any, Iterable

SCHEMA_VERSION = 1
DEFAULT_SEEN_CAPACITY = 4096

# Terminal states a turn can reach. Anything else means "still in flight", and
# a restart is allowed to retry it.
TERMINAL_STATES = frozenset({"completed", "failed", "cancelled", "blocked"})


@dataclass(frozen=True)
class TurnClaim:
    """Outcome of trying to claim a trigger for execution."""

    granted: bool
    reason: str = ""  # "duplicate" | "in_flight" | "active" | ""


class InboxState:
    """Cursor + seen-set + turn ledger, persisted together."""

    def __init__(self, path: Path, seen_capacity: int = DEFAULT_SEEN_CAPACITY) -> None:
        self._path = Path(path)
        self._capacity = max(1, int(seen_capacity))
        self._lock = threading.Lock()
        self._cursor = ""
        self._seen: "OrderedDict[str, bool]" = OrderedDict()
        self._turns: dict[str, str] = {}  # claim key -> state
        # Claims granted by THIS process, never persisted. This is what lets
        # claim_turn distinguish "the previous bridge died mid-turn" (retry)
        # from "another thread of this bridge is running it right now" (deny).
        self._active: set[str] = set()
        # Highest origin_server_ts ever acked. The seen-set only holds mention
        # events (the inbox filters server-side), so "have I handled this?" is
        # unanswerable for anything older than the current window -- a gap
        # backfill that pages past the last seen mention would otherwise
        # resurrect pre-baseline history. The watermark is the time floor
        # below which backfilled events are known territory by definition.
        self._watermark_ts = 0
        self._loaded = False

    # ---- cursor ------------------------------------------------------

    @property
    def cursor(self) -> str:
        self._ensure_loaded()
        with self._lock:
            return self._cursor

    @property
    def first_run(self) -> bool:
        """True until the first ``ack`` establishes a baseline.

        On a first run the sync starts with no ``since`` token, so Matrix
        returns recent *history* — including mentions that were already
        handled or predate this member. The supervisor MUST treat the first
        poll as baseline-only: ack the batch, execute nothing. Skipping this
        check replays old task assignments against the live workspace.
        """
        self._ensure_loaded()
        with self._lock:
            return not self._cursor

    @property
    def watermark_ts(self) -> int:
        """Time floor for backfill: events at or below it are already handled."""
        self._ensure_loaded()
        with self._lock:
            return self._watermark_ts

    def ack(
        self,
        next_cursor: str,
        handled_event_ids: Iterable[str] = (),
        watermark_ts: int = 0,
    ) -> None:
        """Commit a batch: record its events, then advance the cursor.

        Order matters. Events are added to the seen-set in the same atomic
        write that moves the cursor, so the two can never disagree on disk.
        ``watermark_ts`` is the batch's newest event timestamp; it only ever
        moves forward, and the first-run baseline ack is what plants it.
        """
        self._ensure_loaded()
        with self._lock:
            for event_id in handled_event_ids:
                self._remember_locked(str(event_id))
            if next_cursor:
                self._cursor = str(next_cursor)
            if int(watermark_ts) > self._watermark_ts:
                self._watermark_ts = int(watermark_ts)
            self._flush_locked()

    # ---- seen events -------------------------------------------------

    def is_seen(self, event_id: str) -> bool:
        self._ensure_loaded()
        with self._lock:
            return str(event_id) in self._seen

    def filter_unseen(self, events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        """Drop events already handled, preserving order."""
        self._ensure_loaded()
        fresh: list[dict[str, Any]] = []
        with self._lock:
            for event in events:
                event_id = str(event.get("eventId") or event.get("event_id") or "")
                if not event_id or event_id in self._seen:
                    continue
                fresh.append(event)
        return fresh

    def _remember_locked(self, event_id: str) -> None:
        if not event_id:
            return
        self._seen.pop(event_id, None)
        self._seen[event_id] = True
        while len(self._seen) > self._capacity:
            self._seen.popitem(last=False)

    # ---- turn ledger -------------------------------------------------

    @staticmethod
    def claim_key(task_id: str, trigger_event_id: str) -> str:
        # "#" is safe as a separator and stays readable on disk: taskflow task
        # ids are constrained to safe path segments, and Matrix event ids are
        # "$" + base64url. Neither can contain it.
        return f"{task_id}#{trigger_event_id}"

    def claim_turn(self, task_id: str, trigger_event_id: str) -> TurnClaim:
        """Reserve the right to run this turn exactly once.

        Returns ``granted=False`` for a trigger that already reached a terminal
        state (``duplicate``) or that this process is executing right now
        (``active``). A persisted in-flight entry with no live claim in this
        process is regranted: the previous bridge died mid-turn, and retrying
        is the desired behaviour.
        """
        self._ensure_loaded()
        key = self.claim_key(task_id, trigger_event_id)
        with self._lock:
            if key in self._active:
                return TurnClaim(granted=False, reason="active")
            state = self._turns.get(key)
            if state in TERMINAL_STATES:
                return TurnClaim(granted=False, reason="duplicate")
            self._turns[key] = "in_flight"
            self._active.add(key)
            self._flush_locked()
            return TurnClaim(granted=True, reason="" if state is None else "in_flight")

    def settle_turn(self, task_id: str, trigger_event_id: str, status: str) -> None:
        """Mark a claimed turn terminal. Non-terminal statuses stay retryable."""
        self._ensure_loaded()
        key = self.claim_key(task_id, trigger_event_id)
        with self._lock:
            self._active.discard(key)
            if status in TERMINAL_STATES:
                self._turns[key] = status
            else:
                # e.g. "timeout" -- deliberately left retryable so a longer
                # deadline or a manual nudge can pick it back up.
                self._turns.pop(key, None)
            self._flush_locked()

    # ---- persistence -------------------------------------------------

    def load(self) -> None:
        with self._lock:
            self._cursor = ""
            self._seen = OrderedDict()
            self._turns = {}
            self._active = set()
            self._watermark_ts = 0
            self._loaded = True
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8") or "{}")
            except (OSError, ValueError):
                return
            if not isinstance(raw, dict) or int(raw.get("schemaVersion") or 0) != SCHEMA_VERSION:
                return
            self._cursor = str(raw.get("cursor") or "")
            try:
                self._watermark_ts = int(raw.get("watermarkTs") or 0)
            except (TypeError, ValueError):
                self._watermark_ts = 0
            for event_id in raw.get("seen") or []:
                self._remember_locked(str(event_id))
            turns = raw.get("turns")
            if isinstance(turns, dict):
                self._turns = {str(k): str(v) for k, v in turns.items()}

    def _flush_locked(self) -> None:
        payload = {
            "schemaVersion": SCHEMA_VERSION,
            "cursor": self._cursor,
            "seen": list(self._seen.keys()),
            "turns": dict(self._turns),
            "watermarkTs": self._watermark_ts,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle, tmp = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()
