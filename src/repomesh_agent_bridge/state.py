"""The Bridge's crash-consistent local state: one SQLite file per worker.

Deliberately **not a port** (ADR 0004 decision 4): "SQLite is its own test
stand-in". An in-memory state double would be a second implementation of the
only thing this module exists to guarantee — that what is on disk after a power
cut is what the next process can act on — and a double cannot have that
property. Tests therefore open a real file under ``tmp_path``.

What lives here is storage and the rules that belong to a stored row. What does
*not* live here is any decision about rooms, triggers or turns: those are
:mod:`repomesh_agent_bridge.inbox` and :mod:`repomesh_agent_bridge.outbox`,
which sit on top. This module imports no port and no adapter, which is what lets
it be tested with nothing but a directory.

**Single writer.** :class:`~repomesh_agent_bridge.instance_lock.InstanceLock`
already guarantees one live process per ``workerAgentId``, and the state file
lives in the same ``state_dir`` under the same worker identity, so there is no
second writer to coordinate with. The database still refuses to open under the
wrong identity, because "the operator pointed two enrollments at one directory"
is a configuration mistake, not a concurrency problem, and it should be said out
loud rather than merged.

**Every call is synchronous, and that is on purpose — do not move it into an
executor.** ``sqlite3`` blocks, but each transaction here is a handful of local
row writes, on the order of a millisecond. Wrapping them in ``asyncio.to_thread``
would make a single logical transaction span an ``await`` point, which in a
one-loop process invents a concurrency window that does not otherwise exist: the
supervisor could interleave another turn's writes between this one's. The
blocking call is the safer object.

Transaction boundaries are one per public method. The ordering invariants that
span methods — claim before enqueue, cursor last — belong to the supervisor and
are held there, not by keeping a transaction open across ``await``.
"""

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from .contracts import BridgeStartupError
from .instance_lock import default_state_dir

__all__ = [
    "IN_FLIGHT",
    "INTERRUPTED",
    "ROOM_BODY_LIMIT",
    "SCHEMA_VERSION",
    "SEEN_EVENT_LIMIT",
    "TERMINAL_TURN_STATES",
    "BridgeState",
    "OutboxRow",
    "RunAnchor",
    "SessionRef",
    "StateRefused",
    "SyncCursor",
    "open_state",
    "state_path",
]

SCHEMA_VERSION = "3"
"""Bumped when a released schema changes shape. A file that disagrees is
refused, never migrated silently and never discarded (see :class:`StateRefused`).

``2`` gave the outbox a ``lane`` (so a synthesised note and a session's own
answers stop sharing one ordinal space) and a ``refused_at`` (so an intent the
homeserver will never accept can be put down instead of retried forever). ``3``
adds ``run_anchors``, which is what lets a governed run started from one room
message be narrated back into the thread that asked for it. Each changes what
the file *holds*, not just what columns it has, so an older file is refused
rather than opened with the new code reading it — the same policy as ``1``,
because a migration is a thing to write when there is a shape worth migrating
and an operator who is told can downgrade, migrate or delete on purpose.
"""

SEEN_EVENT_LIMIT = 4096
"""How many inbound event ids the first replay layer remembers.

Bounded because it grows with *events*, and unbounded growth on an operator's
laptop is a slow leak nobody will notice. The turn ledger behind it is
deliberately unbounded — it grows with *turns*, which is orders of magnitude
smaller — so an event evicted from here is still refused one layer down.
"""

ROOM_BODY_LIMIT = 4000
"""The ``room-observation.v1`` schema's ceiling on ``body``, spelled here
because the outbox stores the rendered projection and the column has to hold it."""

IN_FLIGHT = "in_flight"
INTERRUPTED = "interrupted"
TERMINAL_TURN_STATES: tuple[str, ...] = ("completed", "failed", "blocked")
"""States a turn never leaves.

``timeout`` and ``cancelled`` are pointedly absent: a turn that ran out of time
or was interrupted by Ctrl-C did not *happen*, and refusing to ever run it again
would strand the mention permanently.
"""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bridge_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_cursor (
  id           INTEGER PRIMARY KEY CHECK (id = 1),
  since_token  TEXT    NOT NULL,
  baseline_at  TEXT,
  watermark_ts INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS seen_events (
  event_id TEXT PRIMARY KEY,
  room_id  TEXT NOT NULL,
  seq      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS seen_events_seq ON seen_events(seq);

CREATE TABLE IF NOT EXISTS turn_ledger (
  room_id          TEXT NOT NULL,
  thread_id        TEXT NOT NULL,
  trigger_event_id TEXT NOT NULL,
  state            TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  PRIMARY KEY (room_id, thread_id, trigger_event_id)
);

CREATE TABLE IF NOT EXISTS outbox (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  room_id          TEXT NOT NULL,
  thread_root_id   TEXT,
  trigger_event_id TEXT NOT NULL,
  lane             TEXT NOT NULL,
  ordinal          INTEGER NOT NULL,
  txn_id           TEXT NOT NULL UNIQUE,
  observation_id   TEXT NOT NULL,
  emitted_at       TEXT NOT NULL,
  kind             TEXT NOT NULL,
  body             TEXT NOT NULL,
  sent_event_id    TEXT,
  sent_at          TEXT,
  refused_at       TEXT,
  UNIQUE (trigger_event_id, lane, ordinal)
);
CREATE INDEX IF NOT EXISTS outbox_pending
  ON outbox(id) WHERE sent_event_id IS NULL AND refused_at IS NULL;

CREATE TABLE IF NOT EXISTS run_anchors (
  run_id           TEXT PRIMARY KEY,
  task_id          TEXT NOT NULL,
  room_id          TEXT NOT NULL,
  thread_root_id   TEXT,
  trigger_event_id TEXT NOT NULL,
  created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_refs (
  room_id           TEXT NOT NULL,
  thread_id         TEXT NOT NULL,
  profile           TEXT NOT NULL,
  native_session_id TEXT,
  turn_count        INTEGER NOT NULL DEFAULT 0,
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL,
  PRIMARY KEY (room_id, thread_id)
);
"""


class StateRefused(BridgeStartupError):
    """This state file cannot be used as asked, and will not be repaired.

    Two cases at open time, one answer. A file whose ``worker_agent_id`` is
    somebody else's would let one Bridge answer another worker's mentions. A
    file whose ``schema_version`` is not this build's cannot be read correctly,
    and the tempting alternative — drop it and start clean — would take the
    rooms' conversation context with it and say nothing to anyone. Refusing is
    reversible by the operator; a silent discard is not.

    A third case arrives later, from a write rather than an open: a row whose
    key is *derived* being rewritten with different values. Every deterministic
    name in this package exists so that a replay lands on the row it landed on
    last time, so a second set of values under one name is not a conflict to
    merge — it says the derivation is not deterministic after all, which is a
    bug in the caller and not a state an operator can be asked to resolve.
    """


def state_path(worker_agent_id: UUID, state_dir: Path | None = None) -> Path:
    """Derive the state path from the identity the file belongs to.

    Sits beside the instance lock under the same ``state_dir`` and the same
    worker dimension, so the lock that makes single-writer true is guarding
    exactly this file.
    """

    return (state_dir or default_state_dir()) / "state" / f"{worker_agent_id}.sqlite3"


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class SyncCursor:
    """Where the last committed ``/sync`` left off."""

    since_token: str
    baseline_at: datetime | None
    """When the baseline batch was acknowledged. ``None`` means the Bridge has
    committed a position but has not yet established a baseline, which cannot
    happen through :mod:`repomesh_agent_bridge.inbox` and is treated as
    "baseline still owed" if it ever does."""
    watermark_ts: int
    """Highest ``origin_server_ts`` acknowledged. Written, never read: reserved
    for a timeline backfill this PR deliberately does not implement."""


@dataclass(frozen=True, slots=True)
class OutboxRow:
    """One outbound intent, exactly as it sits on disk.

    ``body`` is a plain ``str`` here rather than a ``RoomBody``: this module is
    storage, and the one place text is blessed for a room is
    :func:`repomesh_agent_bridge.outbox.render`.
    """

    room_id: str
    thread_root_id: str | None
    trigger_event_id: str
    lane: str
    """Which stream of answers this row belongs to within its trigger.

    A plain column here, deliberately without a CHECK constraint: the set of
    lanes is a decision of :mod:`repomesh_agent_bridge.outbox`, and spelling it
    twice would let the two copies drift. Storage's job is to hold the lane as
    part of the row's identity, which the unique key does.
    """
    ordinal: int
    txn_id: str
    observation_id: UUID
    emitted_at: datetime
    kind: str
    body: str
    sent_event_id: str | None = None
    sent_at: datetime | None = None
    refused_at: datetime | None = None
    """When the room said this intent will never be accepted.

    A dead letter, and a third state rather than a flavour of "sent": a row with
    a ``sent_event_id`` reached its room, a row with neither is still owed, and
    a row with this is neither and never will be. No drain offers it again.
    """
    outbox_id: int | None = None


@dataclass(frozen=True, slots=True)
class RunAnchor:
    """Where a governed run came from, so its progress can go back there.

    The room message that started a run is the only thing that ties that run to
    a conversation: RepoMesh knows the task, the worker and the run, and knows
    nothing about Matrix. Without this row a Bridge that restarts mid-run has no
    way to tell which thread asked for it, and the narration would either go to
    the wrong place or nowhere.

    ``trigger_event_id`` is here for a second reason: it is the outbox's ordinal
    space, so the lifecycle messages a later consumer appends derive their names
    from the mention that started the run rather than from anything the runner
    generates.
    """

    run_id: UUID
    task_id: UUID
    room_id: str
    thread_root_id: str | None
    trigger_event_id: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SessionRef:
    """The coding session serving one thread, and who issued its handle."""

    room_id: str
    thread_id: str
    profile: str
    native_session_id: str | None
    turn_count: int
    created_at: datetime
    updated_at: datetime


def open_state(
    path: Path,
    *,
    worker_agent_id: UUID,
    now: Callable[[], datetime] | None = None,
) -> "BridgeState":
    """Open (creating if needed) the state file for one worker.

    Creating and validating are one step because there is no useful state
    between them: a caller that got a :class:`BridgeState` back holds a file
    that exists, has this build's schema, and belongs to this worker.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.executescript(_SCHEMA)
        _claim_identity(connection, worker_agent_id)
    except BaseException:
        connection.close()
        raise
    return BridgeState(connection, worker_agent_id=worker_agent_id, now=now or _utcnow)


def _claim_identity(connection: sqlite3.Connection, worker_agent_id: UUID) -> None:
    """Write the identity if the file is new, then read back what is actually there.

    Insert-or-ignore followed by a comparison covers both cases in one shape: a
    fresh file adopts this worker and this schema, an existing one keeps what it
    had and the comparison is what refuses.
    """

    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.executemany(
            "INSERT OR IGNORE INTO bridge_meta(key, value) VALUES (?, ?)",
            (("schema_version", SCHEMA_VERSION), ("worker_agent_id", str(worker_agent_id))),
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    stored = {
        row["key"]: row["value"]
        for row in connection.execute("SELECT key, value FROM bridge_meta")
    }
    if stored.get("schema_version") != SCHEMA_VERSION:
        raise StateRefused(
            f"state file schema is {stored.get('schema_version')!r}, this build reads "
            f"{SCHEMA_VERSION!r}; migrate or remove the file rather than losing its sessions"
        )
    if stored.get("worker_agent_id") != str(worker_agent_id):
        raise StateRefused(
            f"state file belongs to worker {stored.get('worker_agent_id')!r}, "
            f"not {str(worker_agent_id)!r}"
        )


class BridgeState:
    """Every durable fact the Bridge keeps, grouped by the table that holds it."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        worker_agent_id: UUID,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._connection = connection
        self._now = now
        self.worker_agent_id = worker_agent_id

    def close(self) -> None:
        """Release the file. Safe to call more than once."""

        self._connection.close()

    def now(self) -> datetime:
        """The clock the whole state layer shares, injectable for tests."""

        return self._now()

    def pragma(self, name: str) -> object:
        """Read one PRAGMA back, so the durability settings are assertable."""

        row = self._connection.execute(f"PRAGMA {name}").fetchone()
        return None if row is None else row[0]

    # -- sync cursor and the seen set ------------------------------------

    def cursor(self) -> SyncCursor | None:
        """The committed position, or ``None`` when nothing has been acked yet."""

        row = self._connection.execute(
            "SELECT since_token, baseline_at, watermark_ts FROM sync_cursor WHERE id = 1"
        ).fetchone()
        if row is None:
            return None
        return SyncCursor(
            since_token=row["since_token"],
            baseline_at=_read_time(row["baseline_at"]),
            watermark_ts=row["watermark_ts"],
        )

    def commit_batch(
        self,
        *,
        next_batch: str,
        events: Sequence[tuple[str, str]],
        watermark_ts: int = 0,
        baseline: bool = False,
    ) -> None:
        """Acknowledge one ``/sync`` answer: its events, then its position.

        One transaction, so the ordering inside is presentation rather than
        mechanism — but it is written seen-then-cursor because that is the
        invariant the recovery story names, and code that reads in the order of
        its invariant is easier to keep honest. What matters is that this is the
        *last* thing a round does: a crash anywhere earlier leaves the cursor
        where it was and the whole batch arrives again, which the seen set and
        the turn ledger are there to absorb.
        """

        with self._write() as connection:
            self._remember(connection, events)
            connection.execute(
                """
                INSERT INTO sync_cursor(id, since_token, baseline_at, watermark_ts)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  since_token  = excluded.since_token,
                  baseline_at  = COALESCE(sync_cursor.baseline_at, excluded.baseline_at),
                  watermark_ts = MAX(sync_cursor.watermark_ts, excluded.watermark_ts)
                """,
                (
                    next_batch,
                    _write_time(self._now()) if baseline else None,
                    watermark_ts,
                ),
            )

    def _remember(
        self, connection: sqlite3.Connection, events: Sequence[tuple[str, str]]
    ) -> None:
        if not events:
            return
        start = connection.execute("SELECT COALESCE(MAX(seq), 0) FROM seen_events").fetchone()[0]
        connection.executemany(
            "INSERT OR IGNORE INTO seen_events(event_id, room_id, seq) VALUES (?, ?, ?)",
            tuple(
                (event_id, room_id, start + offset)
                for offset, (event_id, room_id) in enumerate(events, start=1)
            ),
        )
        # Eviction by insertion order, which is the disk equivalent of the LRU
        # tail drop this replaces: keep the newest SEEN_EVENT_LIMIT rows.
        connection.execute(
            """
            DELETE FROM seen_events WHERE event_id IN (
              SELECT event_id FROM seen_events ORDER BY seq DESC LIMIT -1 OFFSET ?
            )
            """,
            (SEEN_EVENT_LIMIT,),
        )

    def has_seen(self, event_id: str) -> bool:
        return (
            self._connection.execute(
                "SELECT 1 FROM seen_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            is not None
        )

    def seen_count(self) -> int:
        return int(self._connection.execute("SELECT COUNT(*) FROM seen_events").fetchone()[0])

    # -- turn ledger ------------------------------------------------------

    def turn_state(self, room_id: str, thread_id: str, trigger_event_id: str) -> str | None:
        """The recorded state of one turn, or ``None`` if it has none.

        The key is ``(room, thread, trigger event)``. It is the stable ancestor
        of the contract's "worker + native session id + trigger event id"
        (``contracts/agent-bridge/v1/README.md``, the idempotency table): a
        native session id does not exist until a driver announces one, and a
        cold start therefore has no value to key on. The thread stands in for it
        and ``session_refs`` holds the mapping from ``(room, thread)`` to
        whatever id the session later announces. The worker dimension is absent
        because the instance lock already makes one process serve one worker, so
        putting it in the key would be spelling a constant.
        """

        row = self._connection.execute(
            """
            SELECT state FROM turn_ledger
             WHERE room_id = ? AND thread_id = ? AND trigger_event_id = ?
            """,
            (room_id, thread_id, trigger_event_id),
        ).fetchone()
        return None if row is None else row["state"]

    def record_turn(
        self, room_id: str, thread_id: str, trigger_event_id: str, state: str
    ) -> None:
        with self._write() as connection:
            connection.execute(
                """
                INSERT INTO turn_ledger(room_id, thread_id, trigger_event_id, state, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(room_id, thread_id, trigger_event_id) DO UPDATE SET
                  state = excluded.state, updated_at = excluded.updated_at
                """,
                (room_id, thread_id, trigger_event_id, state, _write_time(self._now())),
            )

    def forget_turn(self, room_id: str, thread_id: str, trigger_event_id: str) -> None:
        """Drop the row entirely, leaving the turn claimable as if never tried."""

        with self._write() as connection:
            connection.execute(
                """
                DELETE FROM turn_ledger
                 WHERE room_id = ? AND thread_id = ? AND trigger_event_id = ?
                """,
                (room_id, thread_id, trigger_event_id),
            )

    # -- outbox -----------------------------------------------------------

    def enqueue_sends(self, rows: Sequence[OutboxRow]) -> int:
        """Persist a turn's outbound intents in one transaction.

        ``INSERT OR IGNORE`` against ``UNIQUE (trigger_event_id, lane, ordinal)``
        is what makes re-enqueueing a replayed turn a no-op: the ordinal is the
        position of the response inside that trigger's answer *within its lane*,
        so the second attempt collides row for row rather than appending a second
        copy. The lane is in the key because one trigger can produce two
        independent sequences — what the session said, and what the supervisor
        had to say on its behalf — and without it the second sequence's first
        row would be silently swallowed by the first sequence's.
        """

        if not rows:
            return 0
        with self._write() as connection:
            before = connection.total_changes
            connection.executemany(
                """
                INSERT OR IGNORE INTO outbox(
                  room_id, thread_root_id, trigger_event_id, lane, ordinal, txn_id,
                  observation_id, emitted_at, kind, body
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(
                    (
                        row.room_id,
                        row.thread_root_id,
                        row.trigger_event_id,
                        row.lane,
                        row.ordinal,
                        row.txn_id,
                        str(row.observation_id),
                        _write_time(row.emitted_at),
                        row.kind,
                        row.body,
                    )
                    for row in rows
                ),
            )
            return connection.total_changes - before

    def enqueue_send_at(self, row: OutboxRow) -> bool:
        """Persist one intent at the key the caller names. ``False`` if it was there.

        The counterpart of :meth:`enqueue_sends` for a sequence whose positions
        are *known* rather than counted: a run's lifecycle arrives one message at
        a time, over minutes, possibly across a restart, so "the ordinal is where
        this response sat in the list" — which is what makes the batch form
        idempotent — has no list to be a position in. The caller supplies the
        ordinal because the caller is the one holding the run's shape.

        That hands the invariant back to the caller, so this method checks it:
        the same key with the same payload is the no-op a replay must be, and the
        same key with a *different* payload is a bug being caught at the moment
        it would otherwise become a silently dropped message. ``INSERT OR
        IGNORE`` alone would swallow the second case, which is exactly the
        failure the deterministic naming exists to prevent.

        ``emitted_at`` is not part of the comparison and the stored value wins: a
        replay legitimately re-derives the row under a later clock, and the whole
        point of the row already existing is that the room saw *that* message.
        Delivery state is not compared either — whether the intent has since been
        sent or dead-lettered says nothing about whether it is the same intent.
        """

        with self._write() as connection:
            before = connection.total_changes
            connection.execute(
                """
                INSERT OR IGNORE INTO outbox(
                  room_id, thread_root_id, trigger_event_id, lane, ordinal, txn_id,
                  observation_id, emitted_at, kind, body
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.room_id,
                    row.thread_root_id,
                    row.trigger_event_id,
                    row.lane,
                    row.ordinal,
                    row.txn_id,
                    str(row.observation_id),
                    _write_time(row.emitted_at),
                    row.kind,
                    row.body,
                ),
            )
            written = connection.total_changes > before
            stored = _read_outbox(
                connection.execute(
                    f"SELECT {_OUTBOX_COLUMNS} FROM outbox"
                    " WHERE trigger_event_id = ? AND lane = ? AND ordinal = ?",
                    (row.trigger_event_id, row.lane, row.ordinal),
                ).fetchone()
            )
        if _outbox_payload(stored) != _outbox_payload(row):
            raise StateRefused(
                f"outbox {row.lane} position {row.ordinal} of {row.trigger_event_id} already "
                "holds a different message; a derived name must derive the same row twice"
            )
        return written

    def pending_sends(self) -> tuple[OutboxRow, ...]:
        """Every intent still owed to a room, oldest row first.

        Dead letters are excluded here rather than filtered by the caller: a row
        the homeserver has permanently refused is not "pending", and leaving the
        distinction to whoever iterates would make every drain a place the
        retry-forever bug could come back.
        """

        return tuple(
            _read_outbox(row)
            for row in self._connection.execute(
                f"SELECT {_OUTBOX_COLUMNS} FROM outbox"
                " WHERE sent_event_id IS NULL AND refused_at IS NULL ORDER BY id"
            )
        )

    def sends_for_trigger(self, trigger_event_id: str) -> tuple[OutboxRow, ...]:
        """Every intent ever written for one trigger, in the order it was written.

        Ordered by row id rather than by ordinal because an ordinal is only
        unique within a lane: two lanes both start at zero, and write order is
        the one sequence that describes what the room actually saw.
        """

        return tuple(
            _read_outbox(row)
            for row in self._connection.execute(
                f"SELECT {_OUTBOX_COLUMNS} FROM outbox WHERE trigger_event_id = ? ORDER BY id",
                (trigger_event_id,),
            )
        )

    def mark_sent(self, txn_id: str, event_id: str) -> bool:
        """Record the homeserver's answer. ``False`` when it was already recorded.

        A dead-lettered row is not eligible: it was put down precisely because
        the room will never take it, and recording a delivery against it would
        make the outbox claim something that did not happen.
        """

        with self._write() as connection:
            changed = connection.execute(
                """
                UPDATE outbox SET sent_event_id = ?, sent_at = ?
                 WHERE txn_id = ? AND sent_event_id IS NULL AND refused_at IS NULL
                """,
                (event_id, _write_time(self._now()), txn_id),
            ).rowcount
        return changed > 0

    def mark_refused(self, txn_id: str) -> bool:
        """Put one intent down for good. ``False`` when it was already settled.

        The alternative to having this at all is a drain that offers a message
        the room will never accept at the head of every round, forever, in front
        of every intent behind it — which is a stuck Bridge that looks busy. A
        dead letter is the only answer that neither lies about delivery nor
        blocks the queue; the ERROR the supervisor logs is what makes it visible.
        """

        with self._write() as connection:
            changed = connection.execute(
                """
                UPDATE outbox SET refused_at = ?
                 WHERE txn_id = ? AND sent_event_id IS NULL AND refused_at IS NULL
                """,
                (_write_time(self._now()), txn_id),
            ).rowcount
        return changed > 0

    # -- run anchors --------------------------------------------------------

    def record_anchor(
        self,
        *,
        run_id: UUID,
        task_id: UUID,
        room_id: str,
        thread_root_id: str | None,
        trigger_event_id: str,
    ) -> None:
        """Tie one governed run to the room message that woke it.

        Written before the room is told the run was accepted, for the reason
        every other write in this package comes before its send: a crash in
        between leaves a run that can still be narrated into the right thread,
        while the other order leaves a room holding a receipt for a run nothing
        on disk can place.

        Recording the same run twice with the same values is the no-op a replay
        must be — RepoMesh answers a repeated start for a live run with that
        run's own receipt, so a re-mention after a crash arrives here with
        exactly what the first attempt wrote. Different values under one run id
        are refused rather than merged: a run belongs to one conversation, and
        two answers to "which one" is a bug rather than a change of mind.
        """

        anchor = self.anchor_for_run(run_id)
        if anchor is not None:
            if (anchor.task_id, anchor.room_id, anchor.thread_root_id, anchor.trigger_event_id) != (
                task_id,
                room_id,
                thread_root_id,
                trigger_event_id,
            ):
                raise StateRefused(
                    f"run {run_id} is already anchored to a different room message; "
                    "one run belongs to one conversation"
                )
            return
        with self._write() as connection:
            connection.execute(
                """
                INSERT INTO run_anchors(
                  run_id, task_id, room_id, thread_root_id, trigger_event_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(run_id),
                    str(task_id),
                    room_id,
                    thread_root_id,
                    trigger_event_id,
                    _write_time(self._now()),
                ),
            )

    def anchor_for_run(self, run_id: UUID) -> RunAnchor | None:
        """Where this run's narration belongs, or ``None`` if nothing started it here."""

        row = self._connection.execute(
            """
            SELECT run_id, task_id, room_id, thread_root_id, trigger_event_id, created_at
              FROM run_anchors WHERE run_id = ?
            """,
            (str(run_id),),
        ).fetchone()
        if row is None:
            return None
        return RunAnchor(
            run_id=UUID(row["run_id"]),
            task_id=UUID(row["task_id"]),
            room_id=row["room_id"],
            thread_root_id=row["thread_root_id"],
            trigger_event_id=row["trigger_event_id"],
            created_at=_require_time(row["created_at"]),
        )

    # -- session references -----------------------------------------------

    def session_ref(self, room_id: str, thread_id: str) -> SessionRef | None:
        row = self._connection.execute(
            """
            SELECT room_id, thread_id, profile, native_session_id, turn_count,
                   created_at, updated_at
              FROM session_refs WHERE room_id = ? AND thread_id = ?
            """,
            (room_id, thread_id),
        ).fetchone()
        if row is None:
            return None
        return SessionRef(
            room_id=row["room_id"],
            thread_id=row["thread_id"],
            profile=row["profile"],
            native_session_id=row["native_session_id"],
            turn_count=row["turn_count"],
            created_at=_require_time(row["created_at"]),
            updated_at=_require_time(row["updated_at"]),
        )

    def resume_handle(self, room_id: str, thread_id: str, *, profile: str) -> str | None:
        """The handle this thread may be resumed with, or ``None``.

        A handle is only meaningful to the runtime that issued it, so one minted
        by a different ``codingProfile`` is treated as absent rather than
        offered and rejected downstream. The cost of being wrong this way is a
        single cold start; the cost of the other way is a resume that fails
        somewhere far from here.
        """

        reference = self.session_ref(room_id, thread_id)
        if reference is None or reference.profile != profile:
            return None
        return reference.native_session_id

    def bind_session(
        self,
        room_id: str,
        thread_id: str,
        *,
        profile: str,
        native_session_id: str | None,
    ) -> None:
        """Record the session serving this thread, the moment it announces itself.

        Written on arrival rather than when the turn ends: the window between
        "the session exists" and "the turn finished" is exactly where a crash
        loses the room's conversation context, and binding late is the mistake
        this ordering exists to avoid. A different profile replaces the handle
        instead of layering on it — a new runtime means a new session.
        """

        stamp = _write_time(self._now())
        with self._write() as connection:
            connection.execute(
                """
                INSERT INTO session_refs(
                  room_id, thread_id, profile, native_session_id, turn_count,
                  created_at, updated_at
                ) VALUES (?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(room_id, thread_id) DO UPDATE SET
                  profile = excluded.profile,
                  native_session_id = CASE
                    WHEN session_refs.profile = excluded.profile
                      THEN COALESCE(excluded.native_session_id, session_refs.native_session_id)
                    ELSE excluded.native_session_id
                  END,
                  updated_at = excluded.updated_at
                """,
                (room_id, thread_id, profile, native_session_id, stamp, stamp),
            )

    def count_turn(self, room_id: str, thread_id: str, *, profile: str) -> None:
        """Note that this thread served another turn, creating the row if needed."""

        stamp = _write_time(self._now())
        with self._write() as connection:
            connection.execute(
                """
                INSERT INTO session_refs(
                  room_id, thread_id, profile, native_session_id, turn_count,
                  created_at, updated_at
                ) VALUES (?, ?, ?, NULL, 1, ?, ?)
                ON CONFLICT(room_id, thread_id) DO UPDATE SET
                  turn_count = session_refs.turn_count + 1,
                  updated_at = excluded.updated_at
                """,
                (room_id, thread_id, profile, stamp, stamp),
            )

    # -- transactions -------------------------------------------------------

    def _write(self) -> "_Transaction":
        """One ``BEGIN IMMEDIATE`` per public method.

        Immediate rather than deferred so the write lock is taken up front: the
        single-writer guarantee comes from the instance lock, and a transaction
        that discovers contention halfway through would be reporting a bug in
        that guarantee rather than handling a legitimate race.
        """

        return _Transaction(self._connection)


class _Transaction:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def __enter__(self) -> sqlite3.Connection:
        self._connection.execute("BEGIN IMMEDIATE")
        return self._connection

    def __exit__(self, kind: object, value: object, traceback: object) -> bool:
        if kind is None:
            self._connection.commit()
        else:
            self._connection.rollback()
        return False


_OUTBOX_COLUMNS = (
    "id, room_id, thread_root_id, trigger_event_id, lane, ordinal, txn_id, "
    "observation_id, emitted_at, kind, body, sent_event_id, sent_at, refused_at"
)


def _read_outbox(row: sqlite3.Row) -> OutboxRow:
    return OutboxRow(
        room_id=row["room_id"],
        thread_root_id=row["thread_root_id"],
        trigger_event_id=row["trigger_event_id"],
        lane=row["lane"],
        ordinal=row["ordinal"],
        txn_id=row["txn_id"],
        observation_id=UUID(row["observation_id"]),
        emitted_at=_require_time(row["emitted_at"]),
        kind=row["kind"],
        body=row["body"],
        sent_event_id=row["sent_event_id"],
        sent_at=_read_time(row["sent_at"]),
        refused_at=_read_time(row["refused_at"]),
        outbox_id=row["id"],
    )


def _outbox_payload(row: OutboxRow) -> tuple[object, ...]:
    """What makes two intents at one key the same intent.

    The destination, the two derived names and the text — everything a room
    would see or the homeserver would deduplicate on. Position is absent because
    it is the key being compared *at*, and the emission time is absent because
    the stored one is authoritative; see :meth:`BridgeState.enqueue_send_at`.
    """

    return (
        row.room_id,
        row.thread_root_id,
        row.txn_id,
        row.observation_id,
        row.kind,
        row.body,
    )


def _write_time(moment: datetime) -> str:
    return moment.isoformat()


def _read_time(stored: str | None) -> datetime | None:
    return None if stored is None else datetime.fromisoformat(stored)


def _require_time(stored: str) -> datetime:
    return datetime.fromisoformat(stored)
