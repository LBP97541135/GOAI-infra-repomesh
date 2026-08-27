"""Baseline, replay refusal, turn claims and outbound identity — on a real database.

ADR 0004 decision 4 makes local state an internal seam rather than a port
("SQLite is its own test stand-in"), so every test here opens a real file under
``tmp_path``. There is no in-memory state double that could drift from the code
that runs in production.

The two replay layers are exercised *separately* on purpose. The seen-set is
bounded and the turn ledger is not, so the ledger is what catches an event the
seen-set has already forgotten; a single test that happened to hit the seen-set
would pass while the second layer was broken.
"""

import ast
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import pytest

import repomesh_agent_bridge
from repomesh_agent_bridge.contracts import RoomObservation
from repomesh_agent_bridge.inbox import Inbox, Trigger
from repomesh_agent_bridge.outbox import (
    LANES,
    NOTE_LANE,
    ROOM_OBSERVATION_NAMESPACE,
    TURN_LANE,
    TXN_PREFIX,
    Outbox,
    observation_id,
    observation_txn_id,
    render,
)
from repomesh_agent_bridge.ports import RoomBatch, RoomEvent
from repomesh_agent_bridge.state import (
    ROOM_BODY_LIMIT,
    SEEN_EVENT_LIMIT,
    BridgeState,
    open_state,
    state_path,
)

from .conftest import MATRIX_USER_ID, TEAM_ROOM, WORKER_AGENT_ID, WORKER_NAME, WORKER_ROOM

OTHER_USER = "@teammate:matrix.example.org"
OUTSIDE_ROOM = "!not-confirmed:matrix.example.org"
ALLOWED = (TEAM_ROOM, WORKER_ROOM)
WORKER_UUID = UUID(WORKER_AGENT_ID)


def _event(
    event_id: str,
    *,
    room_id: str = TEAM_ROOM,
    sender: str = OTHER_USER,
    body: str = "please look at the failing test",
    origin_server_ts: int = 1_700_000_000_000,
    thread_root_id: str | None = None,
    mentions_me: bool = True,
) -> RoomEvent:
    return RoomEvent(
        event_id=event_id,
        room_id=room_id,
        sender=sender,
        body=body,
        origin_server_ts=origin_server_ts,
        thread_root_id=thread_root_id,
        mentions_me=mentions_me,
    )


def _batch(*events: RoomEvent, next_batch: str = "s-1") -> RoomBatch:
    return RoomBatch(
        next_batch=next_batch, events=tuple(events), invites=(), limited_rooms=()
    )


def _observation(
    body: str = "I am in the room, but this tier cannot code yet",
    *,
    kind: str = "note",
    room_id: str = TEAM_ROOM,
    **fields: object,
) -> RoomObservation:
    # A random observation id on purpose: the outbox must derive its own, so a
    # turn that reran and minted a fresh one still lands on the same identity.
    return RoomObservation(
        observation_id=uuid4(),
        emitted_at=datetime(2026, 1, 1, tzinfo=UTC),
        worker_name=WORKER_NAME,
        room_id=room_id,
        kind=kind,
        body=body,
        **fields,  # type: ignore[arg-type]
    )


@pytest.fixture
def state(tmp_path: Path) -> Iterator[BridgeState]:
    opened = open_state(state_path(WORKER_UUID, tmp_path), worker_agent_id=WORKER_UUID)
    yield opened
    opened.close()


@pytest.fixture
def inbox(state: BridgeState) -> Inbox:
    return Inbox(state)


@pytest.fixture
def outbox(state: BridgeState) -> Outbox:
    return Outbox(state, worker_agent_id=WORKER_UUID)


def _triggers(inbox: Inbox, batch: RoomBatch) -> tuple[Trigger, ...]:
    return inbox.triggers(batch, matrix_user_id=MATRIX_USER_ID, allowed_rooms=ALLOWED)


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------


def test_the_first_sync_is_a_baseline_that_executes_nothing(
    inbox: Inbox, state: BridgeState
) -> None:
    """History is not a work queue.

    A ``/sync`` with no ``since`` answers with the room's past, so executing it
    would put last week's mentions into a live workspace. The baseline round
    records that past as already seen and runs zero turns.
    """

    batch = _batch(_event("$a"), _event("$b"), _event("$c"), next_batch="s-baseline")

    assert inbox.is_baseline() is True
    assert inbox.since() is None

    recorded = inbox.record_baseline(batch)

    assert recorded == 3
    assert _triggers(inbox, batch) == (), "every baseline event is already seen"
    cursor = state.cursor()
    assert cursor is not None
    assert cursor.since_token == "s-baseline"
    assert cursor.baseline_at is not None
    assert inbox.is_baseline() is False
    assert inbox.since() == "s-baseline"


def test_the_baseline_watermark_records_the_newest_timestamp_it_skipped(
    inbox: Inbox, state: BridgeState
) -> None:
    """Reserved for a later backfill; PR 3 writes it and nothing reads it."""

    inbox.record_baseline(
        _batch(_event("$a", origin_server_ts=10), _event("$b", origin_server_ts=40))
    )

    cursor = state.cursor()
    assert cursor is not None
    assert cursor.watermark_ts == 40


# ---------------------------------------------------------------------------
# Trigger selection
# ---------------------------------------------------------------------------


def test_only_an_explicit_mention_from_another_sender_in_a_confirmed_room_triggers(
    inbox: Inbox,
) -> None:
    inbox.record_baseline(_batch(next_batch="s-0"))
    batch = _batch(
        _event("$outside", room_id=OUTSIDE_ROOM),
        _event("$echo", sender=MATRIX_USER_ID),
        _event("$ambient", mentions_me=False),
        _event("$real"),
        next_batch="s-1",
    )

    triggers = _triggers(inbox, batch)

    assert [trigger.event_id for trigger in triggers] == ["$real"]


def test_an_ignored_event_still_enters_the_seen_set_on_commit(
    inbox: Inbox, state: BridgeState
) -> None:
    """Otherwise every replay would re-decide events already decided against."""

    inbox.record_baseline(_batch(next_batch="s-0"))
    batch = _batch(_event("$ambient", mentions_me=False), next_batch="s-1")

    _triggers(inbox, batch)
    inbox.commit(batch)

    assert state.has_seen("$ambient") is True


def test_a_thread_reply_keys_on_its_root_and_a_top_level_message_on_itself(
    inbox: Inbox,
) -> None:
    """The thread is the conversation; a top-level mention starts one of its own."""

    inbox.record_baseline(_batch(next_batch="s-0"))
    batch = _batch(
        _event("$top"),
        _event("$reply", thread_root_id="$root"),
        next_batch="s-1",
    )

    top, reply = _triggers(inbox, batch)

    assert (top.thread_id, top.thread_root_id) == ("$top", None)
    assert (reply.thread_id, reply.thread_root_id) == ("$root", "$root")


# ---------------------------------------------------------------------------
# Replay — layer one (seen set) and layer two (turn ledger), separately
# ---------------------------------------------------------------------------


def test_a_redelivered_event_is_refused_by_the_seen_set(inbox: Inbox) -> None:
    inbox.record_baseline(_batch(next_batch="s-0"))
    batch = _batch(_event("$once"), next_batch="s-1")

    assert len(_triggers(inbox, batch)) == 1
    inbox.commit(batch)

    assert _triggers(inbox, batch) == (), "layer one absorbed the redelivery"


def test_a_redelivered_event_the_seen_set_forgot_is_refused_by_the_turn_ledger(
    inbox: Inbox, state: BridgeState
) -> None:
    """The second layer, exercised with the first one deliberately exhausted.

    The seen-set is bounded, so a long-lived Bridge eventually forgets an old
    event id. The ledger is unbounded precisely to catch that case, and this
    test is the only place where that claim is checked.
    """

    inbox.record_baseline(_batch(next_batch="s-0"))
    batch = _batch(_event("$old"), next_batch="s-1")
    (trigger,) = _triggers(inbox, batch)
    assert inbox.claim(trigger).granted is True
    inbox.settle(trigger, "completed")
    inbox.commit(batch)

    # Push the event out of the bounded seen-set without touching the ledger.
    filler = _batch(
        *(_event(f"$filler-{index}", mentions_me=False) for index in range(SEEN_EVENT_LIMIT)),
        next_batch="s-2",
    )
    inbox.commit(filler)
    assert state.has_seen("$old") is False, "the first layer has genuinely forgotten it"

    (again,) = _triggers(inbox, batch)
    claim = inbox.claim(again)

    assert claim.granted is False
    assert claim.reason == "settled"


# ---------------------------------------------------------------------------
# Turn ledger — three states
# ---------------------------------------------------------------------------


def test_a_second_claim_from_the_same_instance_is_refused(inbox: Inbox) -> None:
    inbox.record_baseline(_batch(next_batch="s-0"))
    (trigger,) = _triggers(inbox, _batch(_event("$one"), next_batch="s-1"))

    assert inbox.claim(trigger).granted is True
    second = inbox.claim(trigger)

    assert second.granted is False
    assert second.reason == "active"


def test_a_turn_left_in_flight_by_a_dead_instance_is_reauthorized(
    inbox: Inbox, state: BridgeState
) -> None:
    """``in_flight`` on disk with no live claim means the last Bridge died mid-turn.

    Refusing it would strand the mention forever; the in-process active set —
    which is never persisted — is the only thing that tells the two apart.
    """

    inbox.record_baseline(_batch(next_batch="s-0"))
    (trigger,) = _triggers(inbox, _batch(_event("$one"), next_batch="s-1"))
    assert inbox.claim(trigger).granted is True

    restarted = Inbox(state)
    claim = restarted.claim(trigger)

    assert claim.granted is True
    assert claim.reason == "reauthorized"


@pytest.mark.parametrize("status", ["completed", "failed", "blocked"])
def test_a_settled_turn_is_never_run_again(inbox: Inbox, state: BridgeState, status: str) -> None:
    inbox.record_baseline(_batch(next_batch="s-0"))
    (trigger,) = _triggers(inbox, _batch(_event("$one"), next_batch="s-1"))
    inbox.claim(trigger)
    inbox.settle(trigger, status)

    claim = Inbox(state).claim(trigger)

    assert claim.granted is False
    assert claim.reason == "settled"


def test_a_timed_out_turn_is_not_terminal_and_can_be_claimed_again(
    inbox: Inbox, state: BridgeState
) -> None:
    """A deadline says the turn did not finish, not that it must never run."""

    inbox.record_baseline(_batch(next_batch="s-0"))
    (trigger,) = _triggers(inbox, _batch(_event("$one"), next_batch="s-1"))
    inbox.claim(trigger)
    inbox.settle(trigger, "timeout")

    assert state.turn_state(trigger.room_id, trigger.thread_id, trigger.event_id) is None
    assert inbox.claim(trigger).granted is True


def test_a_cancelled_turn_is_recorded_as_interrupted_and_stays_claimable(
    inbox: Inbox, state: BridgeState
) -> None:
    """Ctrl-C must not permanently poison the mention that was in flight."""

    inbox.record_baseline(_batch(next_batch="s-0"))
    (trigger,) = _triggers(inbox, _batch(_event("$one"), next_batch="s-1"))
    inbox.claim(trigger)
    inbox.settle(trigger, "cancelled")

    assert state.turn_state(trigger.room_id, trigger.thread_id, trigger.event_id) == "interrupted"
    claim = Inbox(state).claim(trigger)
    assert claim.granted is True
    assert claim.reason == "retry"


def test_two_rooms_that_share_a_trigger_id_are_different_turns(inbox: Inbox) -> None:
    """The ledger key carries the room, so no cross-room aliasing is possible."""

    inbox.record_baseline(_batch(next_batch="s-0"))
    here = Trigger(
        event_id="$e",
        room_id=TEAM_ROOM,
        thread_id="$e",
        thread_root_id=None,
        sender=OTHER_USER,
        prompt="hi",
        origin_server_ts=1,
    )
    there = replace(here, room_id=WORKER_ROOM)

    assert inbox.claim(here).granted is True
    assert inbox.claim(there).granted is True


# ---------------------------------------------------------------------------
# Bounded seen-set
# ---------------------------------------------------------------------------


def test_the_seen_set_evicts_in_insertion_order_and_leaves_the_rest_alone(
    inbox: Inbox, state: BridgeState
) -> None:
    inbox.record_baseline(_batch(_event("$oldest", mentions_me=False), next_batch="s-0"))
    inbox.commit(
        _batch(
            *(_event(f"$e{index}", mentions_me=False) for index in range(SEEN_EVENT_LIMIT)),
            next_batch="s-1",
        )
    )

    assert state.seen_count() == SEEN_EVENT_LIMIT
    assert state.has_seen("$oldest") is False
    assert state.has_seen(f"$e{SEEN_EVENT_LIMIT - 1}") is True
    cursor = state.cursor()
    assert cursor is not None and cursor.since_token == "s-1", "eviction is not a rollback"


# ---------------------------------------------------------------------------
# Outbound identity
# ---------------------------------------------------------------------------


def test_the_transaction_id_is_derived_from_the_trigger_the_lane_and_the_position() -> None:
    first = observation_txn_id("$trigger", TURN_LANE, 0)

    assert first == observation_txn_id("$trigger", TURN_LANE, 0), "derivation, not generation"
    assert first != observation_txn_id("$trigger", TURN_LANE, 1)
    assert first != observation_txn_id("$other", TURN_LANE, 0)
    assert first != observation_txn_id("$trigger", NOTE_LANE, 0), (
        "a synthesised note and a session's first answer to the same mention are two "
        "messages; deriving one name for both makes the homeserver drop the second"
    )
    assert first.startswith(TXN_PREFIX)
    assert len(first) == len(TXN_PREFIX) + 40
    assert "$" not in first, "a Matrix event id would need escaping in the request path"


def test_the_observation_id_is_a_uuid5_over_the_five_parts_that_name_a_response() -> None:
    derived = observation_id(WORKER_UUID, TEAM_ROOM, "$trigger", TURN_LANE, 0)

    assert derived == uuid5(
        ROOM_OBSERVATION_NAMESPACE, f"{WORKER_UUID}|{TEAM_ROOM}|$trigger|{TURN_LANE}|0"
    )
    assert derived != observation_id(WORKER_UUID, TEAM_ROOM, "$trigger", TURN_LANE, 1)
    assert derived != observation_id(WORKER_UUID, WORKER_ROOM, "$trigger", TURN_LANE, 0)
    assert derived != observation_id(WORKER_UUID, TEAM_ROOM, "$trigger", NOTE_LANE, 0), (
        "the lane is part of a response's identity, not just of its transaction id"
    )
    assert derived.version == 5, "the schema says format: uuid, so a digest string will not do"


def test_two_observations_for_one_trigger_take_ordinals_zero_and_one(
    inbox: Inbox, outbox: Outbox
) -> None:
    trigger = _claimed(inbox)

    sends = outbox.enqueue(
        room_id=trigger.room_id,
        thread_root_id=trigger.thread_root_id,
        trigger_event_id=trigger.event_id,
        observations=(_observation("first"), _observation("second")),
    )

    assert [send.ordinal for send in sends] == [0, 1]
    assert [send.lane for send in sends] == [TURN_LANE, TURN_LANE], "the default lane"
    assert sends[0].txn_id != sends[1].txn_id
    assert sends[0].observation_id != sends[1].observation_id
    assert sends[0].txn_id == observation_txn_id(trigger.event_id, TURN_LANE, 0)


def test_the_outbox_overrides_the_identity_the_session_handed_it(
    inbox: Inbox, outbox: Outbox
) -> None:
    """A rerun turn mints a fresh random id; the durable one must not move."""

    trigger = _claimed(inbox)
    observation = _observation("only")

    (send,) = outbox.enqueue(
        room_id=trigger.room_id,
        thread_root_id=None,
        trigger_event_id=trigger.event_id,
        observations=(observation,),
    )

    assert send.observation_id != observation.observation_id
    assert send.observation_id == observation_id(
        WORKER_UUID, trigger.room_id, "$one", TURN_LANE, 0
    )


def test_enqueueing_the_same_trigger_twice_adds_no_rows(
    inbox: Inbox, outbox: Outbox, state: BridgeState
) -> None:
    """``UNIQUE (trigger_event_id, ordinal)`` is what holds this, not a lookup."""

    trigger = _claimed(inbox)
    observations = (_observation("first"), _observation("second"))
    kwargs = {
        "room_id": trigger.room_id,
        "thread_root_id": None,
        "trigger_event_id": trigger.event_id,
        "observations": observations,
    }

    first = outbox.enqueue(**kwargs)
    second = outbox.enqueue(**kwargs)

    assert len(state.sends_for_trigger(trigger.event_id)) == 2
    assert [send.txn_id for send in first] == [send.txn_id for send in second]
    assert [send.emitted_at for send in first] == [send.emitted_at for send in second]


def test_pending_is_ordered_by_the_row_the_intent_was_written_into(
    inbox: Inbox, outbox: Outbox
) -> None:
    trigger = _claimed(inbox)
    outbox.enqueue(
        room_id=trigger.room_id,
        thread_root_id=None,
        trigger_event_id=trigger.event_id,
        observations=(_observation("first"), _observation("second"), _observation("third")),
    )

    pending = outbox.pending()

    assert [send.ordinal for send in pending] == [0, 1, 2]
    assert [send.outbox_id for send in pending] == sorted(send.outbox_id for send in pending)


def test_acknowledging_a_send_removes_it_from_pending_and_is_idempotent(
    inbox: Inbox, outbox: Outbox
) -> None:
    trigger = _claimed(inbox)
    (send,) = outbox.enqueue(
        room_id=trigger.room_id,
        thread_root_id=None,
        trigger_event_id=trigger.event_id,
        observations=(_observation(),),
    )

    assert outbox.mark_sent(send.txn_id, "$sent") is True
    assert outbox.pending() == ()
    assert outbox.mark_sent(send.txn_id, "$sent") is False, "a second ack changes nothing"
    assert outbox.pending() == ()


# ---------------------------------------------------------------------------
# Lanes: two independent sequences of answers to one mention
# ---------------------------------------------------------------------------


def test_the_two_lanes_share_an_ordinal_space_without_colliding(
    inbox: Inbox, outbox: Outbox, state: BridgeState
) -> None:
    """The account-B fix, at the level where the collision used to happen.

    A turn that timed out put one note at position zero. When the replay
    succeeds and produces two real answers, those also start at zero — and in
    one ordinal space ``INSERT OR IGNORE`` would drop the first of them, leaving
    the room showing "I ran out of time" as the only reply to a mention that was
    in fact answered.
    """

    trigger = _claimed(inbox)
    outbox.enqueue(
        room_id=trigger.room_id,
        thread_root_id=None,
        trigger_event_id=trigger.event_id,
        observations=(_observation("I ran out of time"),),
        lane=NOTE_LANE,
    )

    answers = outbox.enqueue(
        room_id=trigger.room_id,
        thread_root_id=None,
        trigger_event_id=trigger.event_id,
        observations=(_observation("first"), _observation("second")),
    )

    rows = state.sends_for_trigger(trigger.event_id)
    assert [(row.lane, row.ordinal) for row in rows] == [
        (NOTE_LANE, 0),
        (TURN_LANE, 0),
        (TURN_LANE, 1),
    ]
    assert len({row.txn_id for row in rows}) == 3
    assert len({row.observation_id for row in rows}) == 3
    assert [send.ordinal for send in answers] == [0, 0, 1], (
        "the note is still owed too, and enqueue answers with everything unsent"
    )


def test_sends_are_read_back_in_write_order_not_by_a_lane_local_ordinal(
    inbox: Inbox, outbox: Outbox, state: BridgeState
) -> None:
    """Write order is what the room saw; an ordinal only orders within its lane.

    The turn lane's two answers are written first, then the note lane's single
    answer at ordinal zero, so the row-id order (turn 0, turn 1, note 0) and the
    ordinal order (turn 0, note 0, turn 1) genuinely disagree. Reading back by
    ordinal would slip the note between the two answers; reading back by row id
    keeps the sequence the room actually received. Both readers the drain relies
    on promise write order, and this is the case that tells the two apart — the
    sibling test above writes the note first, where the two orders happen to
    coincide.
    """

    trigger = _claimed(inbox)
    outbox.enqueue(
        room_id=trigger.room_id,
        thread_root_id=None,
        trigger_event_id=trigger.event_id,
        observations=(_observation("first"), _observation("second")),
        lane=TURN_LANE,
    )
    outbox.enqueue(
        room_id=trigger.room_id,
        thread_root_id=None,
        trigger_event_id=trigger.event_id,
        observations=(_observation("ran out of time"),),
        lane=NOTE_LANE,
    )

    write_order = [(TURN_LANE, 0), (TURN_LANE, 1), (NOTE_LANE, 0)]
    assert [
        (row.lane, row.ordinal) for row in state.sends_for_trigger(trigger.event_id)
    ] == write_order
    assert [(send.lane, send.ordinal) for send in outbox.pending()] == write_order


def test_re_enqueueing_a_lane_is_a_no_op_and_leaves_the_other_lane_alone(
    inbox: Inbox, outbox: Outbox, state: BridgeState
) -> None:
    trigger = _claimed(inbox)
    note = {
        "room_id": trigger.room_id,
        "thread_root_id": None,
        "trigger_event_id": trigger.event_id,
        "observations": (_observation("I ran out of time"),),
        "lane": NOTE_LANE,
    }
    outbox.enqueue(**note)
    outbox.enqueue(
        room_id=trigger.room_id,
        thread_root_id=None,
        trigger_event_id=trigger.event_id,
        observations=(_observation("the real answer"),),
    )

    outbox.enqueue(**note)

    rows = state.sends_for_trigger(trigger.event_id)
    assert [(row.lane, row.ordinal) for row in rows] == [(NOTE_LANE, 0), (TURN_LANE, 0)]


def test_an_unknown_lane_is_refused_rather_than_minting_a_third_identity_space(
    inbox: Inbox, outbox: Outbox
) -> None:
    """A lane is part of a message's durable name.

    A typo would derive transaction ids that collide with nothing, so every
    replay would post a new message and the deduplication the outbox exists for
    would be silently off for that caller. Refusing is the only way that shows.
    """

    trigger = _claimed(inbox)

    with pytest.raises(ValueError):
        outbox.enqueue(
            room_id=trigger.room_id,
            thread_root_id=None,
            trigger_event_id=trigger.event_id,
            observations=(_observation(),),
            lane="chatter",
        )


def test_the_lane_vocabulary_is_closed() -> None:
    assert LANES == (TURN_LANE, NOTE_LANE)


# ---------------------------------------------------------------------------
# Dead letters
# ---------------------------------------------------------------------------


def test_a_refused_intent_leaves_the_queue_and_never_returns_to_it(
    inbox: Inbox, outbox: Outbox, state: BridgeState
) -> None:
    """Account A's send half: neither delivered nor owed.

    The drain runs at the head of every round and stops at the first failure, so
    an intent the room will never accept has to leave the queue or nothing
    behind it ever moves. It must not leave by being deleted either — the row is
    the record that this was owed and was refused.
    """

    trigger = _claimed(inbox)
    (first, second) = outbox.enqueue(
        room_id=trigger.room_id,
        thread_root_id=None,
        trigger_event_id=trigger.event_id,
        observations=(_observation("rejected"), _observation("still fine")),
    )

    assert outbox.mark_refused(first.txn_id) is True

    assert [send.txn_id for send in outbox.pending()] == [second.txn_id]
    (refused, _) = state.sends_for_trigger(trigger.event_id)
    assert refused.refused_at is not None
    assert refused.sent_event_id is None, "a dead letter is not a delivery"


def test_enqueue_never_hands_back_an_intent_the_room_already_refused(
    inbox: Inbox, outbox: Outbox
) -> None:
    """What ``enqueue`` returns is "what still needs sending", and this does not.

    Worth pinning separately from ``pending``: the two answers are computed from
    different queries over the same rows, and a caller that drained the return
    value rather than the queue would resurrect a dead letter — which is exactly
    the retry-forever loop the dead letter was created to end.
    """

    trigger = _claimed(inbox)
    (dead,) = outbox.enqueue(
        room_id=trigger.room_id,
        thread_root_id=None,
        trigger_event_id=trigger.event_id,
        observations=(_observation("the room refused this"),),
        lane=NOTE_LANE,
    )
    outbox.mark_refused(dead.txn_id)

    still_owed = outbox.enqueue(
        room_id=trigger.room_id,
        thread_root_id=None,
        trigger_event_id=trigger.event_id,
        observations=(_observation("but this is owed"),),
    )

    assert [send.txn_id for send in still_owed] == [
        observation_txn_id(trigger.event_id, TURN_LANE, 0)
    ]


def test_a_refused_intent_can_never_be_recorded_as_delivered(
    inbox: Inbox, outbox: Outbox, state: BridgeState
) -> None:
    """Otherwise the outbox would claim a room saw something it refused."""

    trigger = _claimed(inbox)
    (send,) = outbox.enqueue(
        room_id=trigger.room_id,
        thread_root_id=None,
        trigger_event_id=trigger.event_id,
        observations=(_observation(),),
    )
    outbox.mark_refused(send.txn_id)

    assert outbox.mark_sent(send.txn_id, "$never-happened") is False
    (row,) = state.sends_for_trigger(trigger.event_id)
    assert row.sent_event_id is None


def test_refusing_an_intent_twice_changes_nothing(inbox: Inbox, outbox: Outbox) -> None:
    trigger = _claimed(inbox)
    (send,) = outbox.enqueue(
        room_id=trigger.room_id,
        thread_root_id=None,
        trigger_event_id=trigger.event_id,
        observations=(_observation(),),
    )

    assert outbox.mark_refused(send.txn_id) is True
    assert outbox.mark_refused(send.txn_id) is False


def test_an_acknowledged_intent_cannot_be_turned_into_a_dead_letter(
    inbox: Inbox, outbox: Outbox
) -> None:
    """It reached the room. Nothing that happens later makes that untrue."""

    trigger = _claimed(inbox)
    (send,) = outbox.enqueue(
        room_id=trigger.room_id,
        thread_root_id=None,
        trigger_event_id=trigger.event_id,
        observations=(_observation(),),
    )
    outbox.mark_sent(send.txn_id, "$in-the-room")

    assert outbox.mark_refused(send.txn_id) is False


def _claimed(inbox: Inbox, event_id: str = "$one") -> Trigger:
    inbox.record_baseline(_batch(next_batch="s-0"))
    (trigger,) = _triggers(inbox, _batch(_event(event_id), next_batch="s-1"))
    inbox.claim(trigger)
    return trigger


# ---------------------------------------------------------------------------
# Session references
# ---------------------------------------------------------------------------


def test_a_resume_handle_is_invisible_to_a_profile_that_did_not_issue_it(
    state: BridgeState,
) -> None:
    """A handle only means something to the runtime that minted it.

    Answering a ``codex`` handle to a ``claude-code`` session is how a real
    deployment gets a resume that silently fails; treating it as absent costs
    one cold start and is always safe.
    """

    state.bind_session(TEAM_ROOM, "$thread", profile="codex", native_session_id="sess-7")

    assert state.resume_handle(TEAM_ROOM, "$thread", profile="codex") == "sess-7"
    assert state.resume_handle(TEAM_ROOM, "$thread", profile="claude-code") is None


def test_binding_under_a_new_profile_replaces_the_handle_rather_than_keeping_both(
    state: BridgeState,
) -> None:
    state.bind_session(TEAM_ROOM, "$thread", profile="codex", native_session_id="sess-7")

    state.bind_session(TEAM_ROOM, "$thread", profile="kimi", native_session_id="sess-9")

    reference = state.session_ref(TEAM_ROOM, "$thread")
    assert reference is not None
    assert (reference.profile, reference.native_session_id) == ("kimi", "sess-9")


def test_one_room_holds_a_separate_reference_per_thread(state: BridgeState) -> None:
    """Keyed by thread, not by room: one room runs several conversations."""

    state.bind_session(TEAM_ROOM, "$a", profile="codex", native_session_id="sess-a")
    state.bind_session(TEAM_ROOM, "$b", profile="codex", native_session_id="sess-b")

    assert state.resume_handle(TEAM_ROOM, "$a", profile="codex") == "sess-a"
    assert state.resume_handle(TEAM_ROOM, "$b", profile="codex") == "sess-b"


def test_counting_a_turn_creates_the_reference_before_a_session_announces_one(
    state: BridgeState,
) -> None:
    state.count_turn(TEAM_ROOM, "$thread", profile="codex")
    state.count_turn(TEAM_ROOM, "$thread", profile="codex")

    reference = state.session_ref(TEAM_ROOM, "$thread")
    assert reference is not None
    assert reference.turn_count == 2
    assert reference.native_session_id is None


# ---------------------------------------------------------------------------
# Room text
# ---------------------------------------------------------------------------


def test_render_labels_the_kind_and_keeps_the_observation_body() -> None:
    body = render(_observation("the suite is green", kind="run_completed"))

    assert "the suite is green" in body
    assert body.startswith("[done]")


def test_render_bounds_what_can_enter_a_room() -> None:
    body = render(_observation("x" * (ROOM_BODY_LIMIT * 2)))

    assert len(body) <= ROOM_BODY_LIMIT
    assert body.endswith("…")


def test_render_carries_the_structured_detail_a_kind_brought_with_it() -> None:
    body = render(
        _observation(
            "pytest",
            kind="test_completed",
            test_command="pytest -q",
            test_exit_code=1,
        )
    )

    assert "pytest -q" in body
    assert "exit 1" in body


def test_room_text_is_constructed_in_exactly_one_place() -> None:
    """G-5: the type-level half of "no protocol frame ever enters a room".

    ``RoomBody`` is a ``NewType``, so at runtime it is a plain ``str`` and no
    checker stops a caller from wrapping a raw transcript. What *is* checkable
    is that the wrap happens nowhere else in the package — smuggling a frame
    into a room then requires an author to add a second construction site, which
    a reviewer sees. Parsed rather than grepped so a docstring that merely names
    ``RoomBody(raw)`` is not mistaken for a call.
    """

    package = Path(repomesh_agent_bridge.__file__).parent
    sites: list[str] = []
    for module in sorted(package.rglob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        enclosing = {
            child: node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            for child in ast.walk(node)
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "RoomBody"
            ):
                sites.append(f"{module.name}:{enclosing.get(node, '<module>')}")

    assert sorted(sites) == ["outbox.py:_pending_from_row", "outbox.py:render"], (
        "render is the projection; _pending_from_row rehydrates text an earlier "
        "process already rendered. Any other site is a new way into a room."
    )
