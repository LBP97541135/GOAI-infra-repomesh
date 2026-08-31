"""What survives a crash, table by table and window by window.

Every test here kills the Bridge by *closing the state file and opening it
again*. That is the honest simulation available to a test: the process-local
facts (the active-claim set, anything cached in memory) genuinely go away, and
what remains is exactly what an operator's machine would still have on disk
after a power cut. Design §D-3's grid — ``crash-before-persist``,
``persist-before-send``, ``send-before-ack`` — is walked cell by cell.

The write order those windows rest on is::

    claim -> enqueue -> send -> mark_sent -> settle -> commit(cursor + seen)

The cursor is always last, which is why an uncommitted batch simply arrives
again and is absorbed by the two layers in front of it.
"""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from repomesh_agent_bridge.contracts import BridgeStartupError, RoomObservation
from repomesh_agent_bridge.inbox import Inbox
from repomesh_agent_bridge.outbox import NOTE_LANE, TURN_LANE, Outbox, observation_txn_id
from repomesh_agent_bridge.ports import RoomBatch, RoomEvent
from repomesh_agent_bridge.state import (
    SCHEMA_VERSION,
    BridgeState,
    open_state,
    state_path,
)

from .conftest import MATRIX_USER_ID, ORGANIZATION_ID, TEAM_ROOM, WORKER_AGENT_ID, WORKER_NAME

OTHER_USER = "@teammate:matrix.example.org"
ALLOWED = (TEAM_ROOM,)
WORKER_UUID = UUID(WORKER_AGENT_ID)
OTHER_WORKER = UUID(ORGANIZATION_ID)


class Restartable:
    """One state file, opened and closed as many times as a test likes.

    Holds the path rather than the connection so a test reads as "the process
    died, another one started against the same directory", which is the thing
    under test.
    """

    def __init__(self, directory: Path) -> None:
        self.path = state_path(WORKER_UUID, directory)
        self._open: BridgeState | None = None
        self.clock = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)

    def _now(self) -> datetime:
        return self.clock

    def boot(self) -> BridgeState:
        self.crash()
        self._open = open_state(self.path, worker_agent_id=WORKER_UUID, now=self._now)
        return self._open

    def crash(self) -> None:
        if self._open is not None:
            self._open.close()
            self._open = None


@pytest.fixture
def bridge(tmp_path: Path) -> Iterator[Restartable]:
    restartable = Restartable(tmp_path)
    yield restartable
    restartable.crash()


def _event(event_id: str, *, mentions_me: bool = True) -> RoomEvent:
    return RoomEvent(
        event_id=event_id,
        room_id=TEAM_ROOM,
        sender=OTHER_USER,
        body="please rerun the suite",
        origin_server_ts=1_700_000_000_000,
        thread_root_id=None,
        mentions_me=mentions_me,
    )


def _batch(*events: RoomEvent, next_batch: str) -> RoomBatch:
    return RoomBatch(
        next_batch=next_batch, events=tuple(events), invites=(), limited_rooms=()
    )


def _observation(body: str = "on it") -> RoomObservation:
    return RoomObservation(
        observation_id=uuid4(),
        emitted_at=datetime(2026, 1, 1, tzinfo=UTC),
        worker_name=WORKER_NAME,
        room_id=TEAM_ROOM,
        kind="note",
        body=body,
    )


def _outbox(state: BridgeState) -> Outbox:
    return Outbox(state, worker_agent_id=WORKER_UUID)


def _trigger(inbox: Inbox, batch: RoomBatch):
    (trigger,) = inbox.triggers(batch, matrix_user_id=MATRIX_USER_ID, allowed_rooms=ALLOWED)
    return trigger


# ---------------------------------------------------------------------------
# The file itself
# ---------------------------------------------------------------------------


def test_the_database_is_opened_for_crash_consistency(bridge: Restartable) -> None:
    """WAL plus ``synchronous=FULL``.

    NORMAL would let the last few transactions evaporate on a power cut, and
    surviving exactly those transactions is the entire value of this module. The
    cost is a handful of fsyncs per sync round.
    """

    state = bridge.boot()

    journal = state.pragma("journal_mode")
    synchronous = state.pragma("synchronous")

    assert str(journal).lower() == "wal"
    assert synchronous == 2, "2 is FULL"


def test_a_state_file_belonging_to_another_worker_is_refused(bridge: Restartable) -> None:
    """Two identities in one file would answer each other's mentions."""

    bridge.boot()
    bridge.crash()

    with pytest.raises(BridgeStartupError):
        open_state(bridge.path, worker_agent_id=OTHER_WORKER)


def test_a_state_file_from_another_schema_version_is_refused(bridge: Restartable) -> None:
    """Refused, not silently discarded.

    Dropping the file would take the room's conversation context with it and say
    nothing; an operator who is told can downgrade, migrate, or delete on
    purpose.
    """

    bridge.boot()
    bridge.crash()
    with sqlite3.connect(bridge.path) as raw:
        raw.execute(
            "UPDATE bridge_meta SET value = ? WHERE key = 'schema_version'",
            (f"{SCHEMA_VERSION}-next",),
        )
    raw.close()

    with pytest.raises(BridgeStartupError):
        open_state(bridge.path, worker_agent_id=WORKER_UUID)


def test_a_state_file_written_by_the_previous_schema_is_refused(bridge: Restartable) -> None:
    """The version that actually shipped, spelled out rather than parameterised.

    ``"1"`` is not a hypothetical: it is what every state file on an operator's
    machine says today, and its outbox has one ordinal space and no way to
    record a dead letter. Reading those rows with this build's code would put a
    replayed turn's first real answer under a note's transaction id. The bump is
    only a safeguard if a file that says ``1`` is turned away, so that exact
    string is the assertion.
    """

    bridge.boot()
    bridge.crash()
    with sqlite3.connect(bridge.path) as raw:
        raw.execute("UPDATE bridge_meta SET value = '1' WHERE key = 'schema_version'")
    raw.close()

    assert SCHEMA_VERSION != "1", "this build no longer reads the shape 1 files carry"
    with pytest.raises(BridgeStartupError):
        open_state(bridge.path, worker_agent_id=WORKER_UUID)


def test_every_table_is_readable_after_a_close_and_reopen(bridge: Restartable) -> None:
    """The persistence claim, stated once over all six tables."""

    run_id, task_id = uuid4(), uuid4()
    state = bridge.boot()
    inbox = Inbox(state)
    inbox.record_baseline(_batch(_event("$seen"), next_batch="s-0"))
    trigger = _trigger(inbox, _batch(_event("$live"), next_batch="s-1"))
    inbox.claim(trigger)
    inbox.settle(trigger, "completed")
    (send,) = _outbox(state).enqueue(
        room_id=TEAM_ROOM,
        thread_root_id=None,
        trigger_event_id=trigger.event_id,
        observations=(_observation(),),
    )
    state.bind_session(TEAM_ROOM, trigger.thread_id, profile="codex", native_session_id="s-7")
    state.record_anchor(
        run_id=run_id,
        task_id=task_id,
        room_id=TEAM_ROOM,
        thread_root_id=None,
        trigger_event_id=trigger.event_id,
    )

    state = bridge.boot()

    cursor = state.cursor()
    assert cursor is not None and cursor.since_token == "s-0"
    assert cursor.baseline_at is not None
    assert state.has_seen("$seen") is True
    assert state.turn_state(TEAM_ROOM, trigger.thread_id, trigger.event_id) == "completed"
    assert [row.txn_id for row in state.pending_sends()] == [send.txn_id]
    assert state.resume_handle(TEAM_ROOM, trigger.thread_id, profile="codex") == "s-7"
    anchor = state.anchor_for_run(run_id)
    assert anchor is not None and anchor.trigger_event_id == trigger.event_id


# ---------------------------------------------------------------------------
# Window one: crash before the intent was persisted
# ---------------------------------------------------------------------------


def test_a_turn_that_crashed_before_persisting_replays_to_the_same_transaction_id(
    bridge: Restartable,
) -> None:
    """Nothing was written, so the turn simply runs again — and lands identically.

    This is the cell the deterministic derivation exists for: the replay does
    not "resend", it produces the same names from the same trigger, so the room
    ends up with one message either way.
    """

    state = bridge.boot()
    inbox = Inbox(state)
    inbox.record_baseline(_batch(next_batch="s-0"))
    batch = _batch(_event("$one"), next_batch="s-1")
    trigger = _trigger(inbox, batch)
    assert inbox.claim(trigger).granted is True
    # The turn ran here and the process died before enqueue and before commit.

    state = bridge.boot()
    inbox = Inbox(state)
    assert inbox.since() == "s-0", "the cursor never moved, so the batch arrives again"
    replayed = _trigger(inbox, batch)
    claim = inbox.claim(replayed)
    assert (claim.granted, claim.reason) == (True, "reauthorized")

    (send,) = _outbox(state).enqueue(
        room_id=TEAM_ROOM,
        thread_root_id=None,
        trigger_event_id=replayed.event_id,
        observations=(_observation(),),
    )

    assert send.txn_id == observation_txn_id("$one", TURN_LANE, 0)


def test_a_session_reference_bound_mid_turn_outlives_the_turn_that_bound_it(
    bridge: Restartable,
) -> None:
    """Bound on arrival, not at the end — the window this closes is the crash."""

    state = bridge.boot()
    state.bind_session(TEAM_ROOM, "$thread", profile="codex", native_session_id="sess-mid")

    state = bridge.boot()

    assert state.resume_handle(TEAM_ROOM, "$thread", profile="codex") == "sess-mid"


# ---------------------------------------------------------------------------
# Window two: the intent was persisted but never sent
# ---------------------------------------------------------------------------


def test_an_intent_persisted_but_never_sent_is_still_pending_after_restart(
    bridge: Restartable,
) -> None:
    """The drain at startup is what makes this window recoverable at all."""

    state = bridge.boot()
    inbox = Inbox(state)
    inbox.record_baseline(_batch(next_batch="s-0"))
    trigger = _trigger(inbox, _batch(_event("$one"), next_batch="s-1"))
    inbox.claim(trigger)
    written = _outbox(state).enqueue(
        room_id=TEAM_ROOM,
        thread_root_id="$root",
        trigger_event_id=trigger.event_id,
        observations=(_observation("first"), _observation("second")),
    )

    state = bridge.boot()
    pending = _outbox(state).pending()

    assert [send.txn_id for send in pending] == [send.txn_id for send in written]
    assert [send.ordinal for send in pending] == [0, 1]
    assert [send.body for send in pending] == [send.body for send in written]
    assert pending[0].thread_root_id == "$root"


def test_the_emission_timestamp_is_read_back_rather_than_taken_again(
    bridge: Restartable,
) -> None:
    """One ``observationId`` must never carry two ``emittedAt`` values.

    Matrix deduplicates on the transaction id and never looks at the body, so a
    drifting timestamp would not duplicate the room message — it would produce
    two contradictory records of the same observation for whatever reads them
    downstream.
    """

    state = bridge.boot()
    inbox = Inbox(state)
    inbox.record_baseline(_batch(next_batch="s-0"))
    trigger = _trigger(inbox, _batch(_event("$one"), next_batch="s-1"))
    inbox.claim(trigger)
    (written,) = _outbox(state).enqueue(
        room_id=TEAM_ROOM,
        thread_root_id=None,
        trigger_event_id=trigger.event_id,
        observations=(_observation(),),
    )

    bridge.clock += timedelta(hours=3)
    state = bridge.boot()
    (pending,) = _outbox(state).pending()

    assert pending.emitted_at == written.emitted_at
    assert pending.observation_id == written.observation_id


# ---------------------------------------------------------------------------
# Window three: sent, but the acknowledgement was never written
# ---------------------------------------------------------------------------


def test_a_send_that_was_never_acknowledged_replays_under_the_same_transaction_id(
    bridge: Restartable,
) -> None:
    """The homeserver deduplicates on ``(access token, txnId)``.

    So resending an unacknowledged intent under the identical transaction id
    returns the original event and puts nothing new in the room. That is the
    whole reason the id is derived on disk instead of generated per attempt.
    """

    state = bridge.boot()
    inbox = Inbox(state)
    inbox.record_baseline(_batch(next_batch="s-0"))
    trigger = _trigger(inbox, _batch(_event("$one"), next_batch="s-1"))
    inbox.claim(trigger)
    (written,) = _outbox(state).enqueue(
        room_id=TEAM_ROOM,
        thread_root_id=None,
        trigger_event_id=trigger.event_id,
        observations=(_observation(),),
    )
    # The PUT reached the homeserver here; the process died before mark_sent.

    state = bridge.boot()
    (resend,) = _outbox(state).pending()

    assert resend.txn_id == written.txn_id
    assert resend.txn_id == observation_txn_id("$one", TURN_LANE, 0)


def test_an_acknowledged_send_is_not_offered_again_after_restart(
    bridge: Restartable,
) -> None:
    state = bridge.boot()
    inbox = Inbox(state)
    inbox.record_baseline(_batch(next_batch="s-0"))
    trigger = _trigger(inbox, _batch(_event("$one"), next_batch="s-1"))
    inbox.claim(trigger)
    outbox = _outbox(state)
    (send,) = outbox.enqueue(
        room_id=TEAM_ROOM,
        thread_root_id=None,
        trigger_event_id=trigger.event_id,
        observations=(_observation(),),
    )
    outbox.mark_sent(send.txn_id, "$event-in-room")

    state = bridge.boot()

    assert _outbox(state).pending() == ()
    (row,) = state.sends_for_trigger("$one")
    assert row.sent_event_id == "$event-in-room"
    assert row.sent_at is not None


# ---------------------------------------------------------------------------
# Window four: the room refused it, and will refuse it again
# ---------------------------------------------------------------------------


def test_a_dead_letter_is_not_offered_again_by_the_next_process(
    bridge: Restartable,
) -> None:
    """The refusal has to be *on disk*, not a decision the last process made.

    A restart is exactly when a retry-forever bug comes back: the in-memory
    knowledge that this message was rejected is gone, and if the row still looks
    pending, the new process puts it at the head of its first drain and stops
    there — before every intent written after it.
    """

    state = bridge.boot()
    inbox = Inbox(state)
    inbox.record_baseline(_batch(next_batch="s-0"))
    trigger = _trigger(inbox, _batch(_event("$one"), next_batch="s-1"))
    inbox.claim(trigger)
    outbox = _outbox(state)
    (rejected, following) = outbox.enqueue(
        room_id=TEAM_ROOM,
        thread_root_id=None,
        trigger_event_id=trigger.event_id,
        observations=(_observation("the room will not take this"), _observation("but this")),
    )
    outbox.mark_refused(rejected.txn_id)

    state = bridge.boot()

    assert [send.txn_id for send in _outbox(state).pending()] == [following.txn_id]
    (dead, _) = state.sends_for_trigger("$one")
    assert dead.refused_at is not None
    assert dead.sent_event_id is None, "the record still says the room never got it"


def test_a_timeout_note_and_the_replayed_answers_survive_in_separate_lanes(
    bridge: Restartable,
) -> None:
    """Account B, at the durability layer: what the next process finds on disk.

    The note was written and delivered before the crash, the batch was never
    acknowledged, and the replay produced two real answers. All three rows have
    to be there under three names, or the room's record of the mention is a
    timeout that was in fact answered.
    """

    state = bridge.boot()
    inbox = Inbox(state)
    inbox.record_baseline(_batch(next_batch="s-0"))
    trigger = _trigger(inbox, _batch(_event("$one"), next_batch="s-1"))
    inbox.claim(trigger)
    outbox = _outbox(state)
    (note,) = outbox.enqueue(
        room_id=TEAM_ROOM,
        thread_root_id=None,
        trigger_event_id=trigger.event_id,
        observations=(_observation("I ran out of time"),),
        lane=NOTE_LANE,
    )
    outbox.mark_sent(note.txn_id, "$the-timeout-note")
    # The batch was never committed, so the mention arrives again.

    state = bridge.boot()
    answers = _outbox(state).enqueue(
        room_id=TEAM_ROOM,
        thread_root_id=None,
        trigger_event_id="$one",
        observations=(_observation("first"), _observation("second")),
    )

    assert [send.txn_id for send in answers] == [
        observation_txn_id("$one", TURN_LANE, 0),
        observation_txn_id("$one", TURN_LANE, 1),
    ]
    assert note.txn_id == observation_txn_id("$one", NOTE_LANE, 0)
    rows = state.sends_for_trigger("$one")
    assert [(row.lane, row.ordinal) for row in rows] == [
        (NOTE_LANE, 0),
        (TURN_LANE, 0),
        (TURN_LANE, 1),
    ]
    assert len({row.txn_id for row in rows}) == 3


# ---------------------------------------------------------------------------
# The cursor is always last
# ---------------------------------------------------------------------------


def test_an_uncommitted_batch_arrives_again_and_the_ledger_absorbs_it(
    bridge: Restartable,
) -> None:
    """Shutdown skips the commit on purpose, so the batch is not lost.

    What stops the replay from running the turn twice is not the cursor — it is
    the settled ledger row, written before the commit that never happened.
    """

    state = bridge.boot()
    inbox = Inbox(state)
    inbox.record_baseline(_batch(next_batch="s-0"))
    batch = _batch(_event("$one"), next_batch="s-1")
    trigger = _trigger(inbox, batch)
    inbox.claim(trigger)
    inbox.settle(trigger, "completed")
    # Stopping: the cursor is deliberately not committed.

    state = bridge.boot()
    inbox = Inbox(state)

    assert inbox.since() == "s-0"
    assert state.has_seen("$one") is False, "seen is written with the cursor, not before"
    replayed = _trigger(inbox, batch)
    claim = inbox.claim(replayed)
    assert (claim.granted, claim.reason) == (False, "settled")


def test_a_turn_interrupted_by_shutdown_is_retried_rather_than_refused(
    bridge: Restartable,
) -> None:
    """Ctrl-C during a turn must not look like a duplicate on the next start."""

    state = bridge.boot()
    inbox = Inbox(state)
    inbox.record_baseline(_batch(next_batch="s-0"))
    batch = _batch(_event("$one"), next_batch="s-1")
    trigger = _trigger(inbox, batch)
    inbox.claim(trigger)
    inbox.settle(trigger, "cancelled")

    state = bridge.boot()
    inbox = Inbox(state)
    claim = inbox.claim(_trigger(inbox, batch))

    assert (claim.granted, claim.reason) == (True, "retry")
