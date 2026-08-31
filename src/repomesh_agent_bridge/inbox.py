"""Which room events become turns, and which are refused.

Every decision on the inbound side lives here, and every one of them is made
against :mod:`repomesh_agent_bridge.state`. There is no IO beyond that: no
socket, no clock of its own, no Matrix vocabulary past the already-parsed
:class:`~repomesh_agent_bridge.ports.RoomEvent`. That is what lets the whole
replay story be tested with a directory and a list of events.

Three refusals, in the order they are cheapest:

1. **Not addressed to us.** A room outside the preflight-confirmed allowlist, a
   message this worker sent itself, or one that does not mention it.
2. **Already seen.** The bounded seen-set catches ordinary redelivery — the same
   event arriving in two consecutive syncs because the cursor was not committed.
3. **Already decided.** The unbounded turn ledger catches the case the seen-set
   has forgotten. It is the layer that makes the seen-set safe to bound at all.

The fourth state — a turn recorded ``in_flight`` with no live claim in *this*
process — is not a refusal but a reauthorisation: the previous Bridge died
mid-turn, and retrying is the correct answer. The in-process ``_active`` set is
never persisted, and that is precisely what distinguishes "running right now"
from "was running when the power went out".
"""

import logging
from dataclasses import dataclass

from .ports import RoomBatch, RoomEvent
from .state import IN_FLIGHT, INTERRUPTED, TERMINAL_TURN_STATES, BridgeState

__all__ = ["Inbox", "Trigger", "TurnClaim"]

_logger = logging.getLogger(__name__)

_RETRYABLE_STATUSES = ("timeout", "cancelled", "interrupted")


@dataclass(frozen=True, slots=True)
class Trigger:
    """One mention this Bridge has decided to answer."""

    event_id: str
    room_id: str
    thread_id: str
    """The conversation this turn belongs to: the thread root when the mention
    is a threaded reply, otherwise the mention itself, which starts one. This is
    the ledger key's middle component and the ``session_refs`` key's second."""
    thread_root_id: str | None
    """The Matrix relation to reply under, or ``None`` for a top-level mention.
    Distinct from ``thread_id`` on purpose: whether to *start* a thread is the
    supervisor's call, and it needs to be able to tell the two apart."""
    sender: str
    prompt: str
    origin_server_ts: int


@dataclass(frozen=True, slots=True)
class TurnClaim:
    """Whether this turn may run, and on what grounds.

    The reason is carried because the four answers are operationally different —
    a reauthorisation after a crash and a refusal of a settled turn look the
    same from the outside and mean opposite things — and because a log line that
    says which one happened is the difference between a debuggable Bridge and a
    silent one.
    """

    granted: bool
    reason: str
    """``new`` | ``reauthorized`` | ``retry`` | ``active`` | ``settled``."""


class Inbox:
    """The inbound half of the reliability core."""

    def __init__(self, state: BridgeState) -> None:
        self._state = state
        self._active: set[tuple[str, str, str]] = set()
        """Turns this process is running right now. Never persisted: on disk
        these are indistinguishable from turns a dead process left behind, and
        that distinction is the whole point of keeping the set in memory."""

    # -- baseline ----------------------------------------------------------

    def is_baseline(self) -> bool:
        """True until a position has been committed.

        A ``/sync`` without ``since`` answers with the room's *history*, so the
        first round must establish a starting line rather than execute what it
        finds. Running it would take last week's mentions and put them into a
        live workspace as though they had just arrived.
        """

        cursor = self._state.cursor()
        return cursor is None or cursor.baseline_at is None

    def since(self) -> str | None:
        """The token to resume from, or ``None`` to ask for the baseline."""

        cursor = self._state.cursor()
        return None if cursor is None else cursor.since_token

    def record_baseline(self, batch: RoomBatch) -> int:
        """Adopt this batch as the past, and run none of it.

        Returns how many events were written off, which is worth logging: an
        operator who enrols a Bridge into a busy room deserves to see that it
        skipped forty messages on purpose rather than wonder why it ignored
        them.
        """

        self._note_truncation(batch)
        self._state.commit_batch(
            next_batch=batch.next_batch,
            events=tuple((event.event_id, event.room_id) for event in batch.events),
            watermark_ts=_newest(batch),
            baseline=True,
        )
        _logger.info(
            "baseline established: cursor=set skipped_events=%d", len(batch.events)
        )
        return len(batch.events)

    # -- selection ---------------------------------------------------------

    def triggers(
        self, batch: RoomBatch, *, matrix_user_id: str, allowed_rooms: tuple[str, ...]
    ) -> tuple[Trigger, ...]:
        """The events in this batch that are ours to answer, oldest first.

        The allowlist test is on the *room*, not on who sent the message. The
        rooms are the ones RepoMesh's preflight confirmed, which is an authority
        this process cannot edit; a sender-based rule would be a local file an
        operator could widen by accident.
        """

        self._note_truncation(batch)
        rooms = frozenset(allowed_rooms)
        selected: list[Trigger] = []
        for event in batch.events:
            if not self._addressed_to_us(event, matrix_user_id=matrix_user_id, rooms=rooms):
                continue
            if self._state.has_seen(event.event_id):
                _logger.debug("event %s already seen; not a new turn", event.event_id)
                continue
            selected.append(
                Trigger(
                    event_id=event.event_id,
                    room_id=event.room_id,
                    thread_id=event.thread_root_id or event.event_id,
                    thread_root_id=event.thread_root_id,
                    sender=event.sender,
                    prompt=event.body,
                    origin_server_ts=event.origin_server_ts,
                )
            )
        return tuple(selected)

    def _addressed_to_us(
        self, event: RoomEvent, *, matrix_user_id: str, rooms: frozenset[str]
    ) -> bool:
        if event.room_id not in rooms:
            _logger.warning(
                "ignoring an event from %s, which is not a confirmed room", event.room_id
            )
            return False
        if event.sender == matrix_user_id:
            return False  # our own projection coming back through /sync
        return event.mentions_me

    # -- turn ledger --------------------------------------------------------

    def claim(self, trigger: Trigger) -> TurnClaim:
        """Ask permission to run this turn, and record the attempt if granted."""

        key = _key(trigger)
        recorded = self._state.turn_state(*key)
        if recorded in TERMINAL_TURN_STATES:
            return TurnClaim(granted=False, reason="settled")
        if recorded == IN_FLIGHT and key in self._active:
            return TurnClaim(granted=False, reason="active")
        if recorded == IN_FLIGHT:
            reason = "reauthorized"
            _logger.warning(
                "turn %s was left in flight by an earlier instance; retrying it",
                trigger.event_id,
            )
        elif recorded == INTERRUPTED:
            reason = "retry"
        else:
            reason = "new"
        self._state.record_turn(*key, IN_FLIGHT)
        self._active.add(key)
        return TurnClaim(granted=True, reason=reason)

    def settle(self, trigger: Trigger, status: str) -> None:
        """Record how the turn ended, and release this process's claim.

        Terminal statuses are written down and refuse every later replay.
        ``cancelled`` is written as ``interrupted`` instead, which is *not*
        terminal: a Ctrl-C during a turn must leave the mention answerable on
        the next start rather than permanently rejected as a duplicate.
        ``timeout`` drops the row entirely — a turn that ran out of time has
        nothing worth remembering, and the next attempt should look exactly like
        a first one.
        """

        key = _key(trigger)
        self._active.discard(key)
        if status in TERMINAL_TURN_STATES:
            self._state.record_turn(*key, status)
            return
        if status not in _RETRYABLE_STATUSES:
            raise ValueError(f"unknown turn status {status!r}")
        if status == "timeout":
            self._state.forget_turn(*key)
            return
        self._state.record_turn(*key, INTERRUPTED)

    # -- acknowledgement -----------------------------------------------------

    def commit(self, batch: RoomBatch) -> None:
        """Acknowledge the batch: its events are seen, the cursor moves on.

        Always the last step of a round. A shutdown skips it deliberately, so
        the batch arrives again and the ledger — already written — is what stops
        the finished turns from running twice.
        """

        self._state.commit_batch(
            next_batch=batch.next_batch,
            events=tuple((event.event_id, event.room_id) for event in batch.events),
            watermark_ts=_newest(batch),
        )

    def _note_truncation(self, batch: RoomBatch) -> None:
        """Say so, loudly, and do not backfill.

        Reading past a truncated timeline needs the ``/messages`` pagination API
        and an acknowledgement watermark — a whole second Matrix surface — which
        this tier does not take on. The residual risk is real and worth naming:
        after a long outage, mentions older than the timeline limit are skipped
        in silence unless this line is in the log.
        """

        for room_id in batch.limited_rooms:
            _logger.warning(
                "timeline for %s was truncated; older mentions in it are not backfilled",
                room_id,
            )


def _key(trigger: Trigger) -> tuple[str, str, str]:
    return (trigger.room_id, trigger.thread_id, trigger.event_id)


def _newest(batch: RoomBatch) -> int:
    return max((event.origin_server_ts for event in batch.events), default=0)
