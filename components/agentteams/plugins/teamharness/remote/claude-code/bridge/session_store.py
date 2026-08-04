#!/usr/bin/env python3
"""Durable ``task_id -> runtime session handle`` mapping for remote members.

An AgentTeams task outlives any CLI process: the leader may delegate on Monday
and follow up on Wednesday, and the operator's laptop reboots in between. The
store is the only thing that lets a turn resume instead of restarting cold.

Keyed by task, never by room. A room can carry several concurrent tasks; keying
by room is how one task's context leaks into another (upstream issue #603).

Writes are atomic (temp file + ``os.replace``) and guarded by an in-process
lock, matching the ``_write_json_atomic`` idiom already used by the TeamHarness
MCP server. Cross-process safety is deliberately out of scope: exactly one
bridge owns a given member.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any, Iterator

SCHEMA_VERSION = 1


@dataclass
class SessionRecord:
    """One task's live binding to a runtime session."""

    task_id: str
    room_id: str
    driver: str  # "claude-code" | "codex-cli"
    workspace: str
    session_ref: str = ""  # empty until the driver reveals it
    turn_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    last_trigger_event_id: str = ""
    last_status: str = ""  # mirrors protocol.TurnStatus

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionRecord":
        known = {f: data.get(f) for f in cls.__dataclass_fields__ if f in data}
        return cls(**known)  # type: ignore[arg-type]


class SessionStore:
    """JSON-backed session map.

    ``now`` is injected rather than read from the clock so tests stay
    deterministic, following the ``*Deps`` injection style used across the
    TeamHarness MCP helpers.
    """

    def __init__(self, path: Path, now: Any = None) -> None:
        self._path = Path(path)
        self._now = now or _utc_now
        self._lock = threading.Lock()
        self._records: dict[str, SessionRecord] = {}
        self._loaded = False

    # ---- persistence -------------------------------------------------

    def load(self) -> None:
        """Read from disk. Tolerates a missing or corrupt file."""
        with self._lock:
            self._records = {}
            self._loaded = True
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8") or "{}")
            except (OSError, ValueError):
                return
            if not isinstance(raw, dict):
                return
            if int(raw.get("schemaVersion") or 0) != SCHEMA_VERSION:
                # Forward/backward incompatible snapshots are dropped, not
                # migrated. Losing resume handles degrades to a cold turn;
                # honouring a misread handle corrupts a live task.
                return
            for task_id, entry in (raw.get("sessions") or {}).items():
                if isinstance(entry, dict):
                    self._records[str(task_id)] = SessionRecord.from_dict(entry)

    def _flush_locked(self) -> None:
        payload = {
            "schemaVersion": SCHEMA_VERSION,
            "sessions": {k: asdict(v) for k, v in self._records.items()},
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle, tmp = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except BaseException:
            _unlink_quiet(tmp)
            raise

    # ---- accessors ---------------------------------------------------

    def get(self, task_id: str) -> SessionRecord | None:
        self._ensure_loaded()
        with self._lock:
            return self._records.get(task_id)

    def resume_ref(self, task_id: str) -> str | None:
        """The handle to pass as ``TurnRequest.session_ref``, or ``None``."""
        record = self.get(task_id)
        if record is None or not record.session_ref:
            return None
        return record.session_ref

    def ensure(self, task_id: str, room_id: str, driver: str, workspace: str) -> SessionRecord:
        """Create the record if absent. Does not overwrite an existing one."""
        self._ensure_loaded()
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                stamp = self._now()
                record = SessionRecord(
                    task_id=task_id,
                    room_id=room_id,
                    driver=driver,
                    workspace=workspace,
                    created_at=stamp,
                    updated_at=stamp,
                )
                self._records[task_id] = record
                self._flush_locked()
            return record

    def bind_session_ref(self, task_id: str, session_ref: str) -> None:
        """Persist the runtime resume handle the moment the driver reveals it.

        Called from inside the event loop on the first ``session_ref`` event,
        not after the turn completes -- a crash mid-turn must still leave a
        resumable handle behind.
        """
        self._update(task_id, session_ref=session_ref)

    def record_turn(self, task_id: str, status: str, trigger_event_id: str = "") -> None:
        self._ensure_loaded()
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return
            record.turn_count += 1
            record.last_status = status
            record.updated_at = self._now()
            if trigger_event_id:
                record.last_trigger_event_id = trigger_event_id
            self._flush_locked()

    def drop(self, task_id: str) -> bool:
        """Forget a task, e.g. after ``cancel_task`` or a terminal submit."""
        self._ensure_loaded()
        with self._lock:
            if task_id not in self._records:
                return False
            del self._records[task_id]
            self._flush_locked()
            return True

    def __iter__(self) -> Iterator[SessionRecord]:
        self._ensure_loaded()
        with self._lock:
            return iter(list(self._records.values()))

    # ---- internals ---------------------------------------------------

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def _update(self, task_id: str, **changes: Any) -> None:
        self._ensure_loaded()
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return
            for key, value in changes.items():
                setattr(record, key, value)
            record.updated_at = self._now()
            self._flush_locked()


def _utc_now() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _unlink_quiet(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass
