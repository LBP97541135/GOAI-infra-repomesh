"""The one module that knows what order things happen in.

Everything else in this package holds a decision that can be made in isolation:
:mod:`~repomesh_agent_bridge.inbox` decides which events are ours,
:mod:`~repomesh_agent_bridge.outbox` decides what a response is called,
:mod:`~repomesh_agent_bridge.adapters.matrix` decides nothing at all. What is
left over — and what all three of them depend on being true — is *sequence*::

    drain -> sync -> invites -> baseline? -> per trigger:
        claim -> respond -> enqueue -> drain -> settle
    -> commit(cursor + seen)

The cursor is always last, and a round that ends early simply never reaches it,
so the batch arrives again and the turn ledger — written before the commit that
did not happen — is what stops a finished turn from running twice. That is the
whole recovery story, and it is a property of this file.

Three further shapes are deliberate:

**One turn at a time, in arrival order.** An operator has one laptop and one
workspace, so concurrent turns are contamination rather than throughput. PR 4's
real CLI turns that from a preference into a requirement.

**The room's text never originates here.** Every outbound body comes back out of
the outbox, which rendered it from a ``RoomObservation``; this module constructs
``RoomObservation`` and never ``RoomBody``. A source-scan test pins that.

**Failures are told to the room without detail.** A turn that broke gets one
canned line in the room and a full traceback in this machine's log. The room is
a place other people read.

**A refusal is graded by what it costs, not by where it came from.** Everything
the room port raises is a reason to wait and try again — except
:class:`~repomesh_agent_bridge.ports.RoomRefused`, which says a retry changes
nothing. Backing off on one of those produces a warning a minute for as long as
the process lives while nothing recovers, so each of the three call sites
answers it with the smallest true response: a refused ``sync`` means this
identity can no longer read anything and ends the run; a refused ``send`` is one
message the room will never take and becomes a dead letter; a refused ``join``
is one room this worker cannot enter and is skipped. Grading them here is only
possible because the vocabulary lives on the port — a core module may not import
an adapter to learn the type it branches on.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from .contracts import ExternalWorkerEnrollment, RoomObservation
from .inbox import Inbox, Trigger
from .outbox import NOTE_LANE, TURN_LANE, Outbox, observation_id
from .ports import CodingSessionPort, RoomBatch, RoomPort, RoomRefused, TurnRequest
from .state import TERMINAL_TURN_STATES, BridgeState

__all__ = [
    "BACKOFF_CEILING_SECONDS",
    "FAILURE_NOTE",
    "SYNC_TIMEOUT_MS",
    "TIMEOUT_NOTE",
    "TURN_TIMEOUT_SECONDS",
    "RoomSupervisor",
]

_logger = logging.getLogger(__name__)

SYNC_TIMEOUT_MS = 30_000
"""How long one long poll may wait for something to happen.

Spelled here rather than imported from the Matrix adapter: the caller decides
how patient a round is, and an adapter that carried the number would be holding
a policy it has no way to justify. The two values agree today, which is the
uninteresting case.
"""

TURN_TIMEOUT_SECONDS = 900.0
"""How long one turn may take before the Bridge stops waiting for it.

Generous, because a real coding turn legitimately takes minutes, and finite
because the alternative is a room that goes quiet forever with no way for anyone
in it to find out why. The abandoned turn is recorded as retryable rather than
finished — running out of time is not an answer.
"""

BACKOFF_CEILING_SECONDS = 60.0
_BACKOFF_FLOOR_SECONDS = 1.0
_BACKOFF_DOUBLINGS = 6
"""1, 2, 4 … 60 seconds. Bounded doublings rather than an unbounded shift so a
homeserver that has been down all night cannot overflow the arithmetic."""

FAILURE_NOTE = "I could not finish that turn. The details are in this machine's log."
TIMEOUT_NOTE = "I ran out of time on that turn. The details are in this machine's log."
"""The only two things a room is ever told about a turn that went wrong.

No exception text, no path, no command line. Whoever is in the room did not ask
for this worker's stack trace and may not be entitled to it; the operator who
owns the machine has the log.
"""


class _RoomTrouble(Exception):
    """A call into the room port failed in a way that waiting might fix.

    Raised by this module's own wrappers so the loop can back off without
    knowing which adapter is behind the port or what it calls things. The port's
    contract is transport and parsing with no decisions in it, so *any*
    unclassified failure coming out of one is a transport failure and gets the
    same answer: keep the cursor where it is and try again later.

    The one exception is a ``RoomRefused``, which never becomes one of these:
    "wait and retry" is the wrong answer to "this will never work", and each
    call site handles it before this wrapper would swallow it.
    """


@dataclass(frozen=True, slots=True)
class _Answer:
    """What one turn produced, and which lane the outbox should file it under.

    The lane travels with the observations rather than being re-derived by the
    caller, because the two paths that produce a turn's output are exactly the
    two lanes: whatever the session said belongs to it, and whatever this module
    had to say on its behalf belongs to the other. Reconstructing that from the
    status afterwards would be a second encoding of a fact the producing branch
    already knew, and the second encoding is the one that drifts.
    """

    status: str
    lane: str
    observations: tuple[RoomObservation, ...]


class RoomSupervisor:
    """One worker's rooms, served one turn at a time until cancelled.

    Takes the state file rather than an inbox and an outbox because the three
    are facets of a single file — the session references live directly on it —
    and handing over all three would invite the question of whether they are the
    same file. They are, by construction, because this constructor is where the
    other two are made.
    """

    def __init__(
        self,
        *,
        enrollment: ExternalWorkerEnrollment,
        confirmed_room_ids: Sequence[str],
        room_port: RoomPort,
        coding_session: CodingSessionPort,
        state: BridgeState,
        sync_timeout_ms: int = SYNC_TIMEOUT_MS,
        turn_timeout_seconds: float = TURN_TIMEOUT_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._enrollment = enrollment
        self._rooms = tuple(confirmed_room_ids)
        self._room_port = room_port
        self._coding_session = coding_session
        self._state = state
        self._inbox = Inbox(state)
        self._outbox = Outbox(state, worker_agent_id=enrollment.worker_agent_id)
        self._sync_timeout_ms = sync_timeout_ms
        self._turn_timeout = turn_timeout_seconds
        self._sleep = sleep
        self._failures = 0

    async def serve(self) -> None:
        """Run rounds until cancelled.

        The first thing ``serve`` does is drain the outbox, because that is the
        first thing every round does. An intent stranded by a crash has to reach
        its room before new messages are taken on, or the room reads its answers
        out of order — and putting the drain at the top of the loop rather than
        once in front of it means a send that failed mid-round leaves on the
        next round rather than waiting for the next process.

        There is no "stopping" flag, and its absence is the design. The
        implementation this grew from ran the loop on a thread and needed a
        boolean to tell the acknowledgement path that a stop had been requested;
        here cancellation *is* that signal, and it arrives as an exception that
        unwinds past the commit rather than as a state the commit has to
        remember to consult. A flag would be a second way to know the same
        thing, and the second way is the one that gets forgotten.

        Two things end this loop and they leave the same way. Cancellation is an
        operator stopping the process. A ``RoomRefused`` out of the sync is the
        homeserver saying this identity cannot read its rooms at all — a revoked
        or replaced token, most often — and retrying a decision is not a
        recovery strategy: it would log a warning a minute forever while the
        room saw nothing and the operator was told nothing they could act on. So
        it propagates, the caller's exit stack unwinds every seam in reverse,
        and the process exits with the same code any other refusal to serve
        earns. The batch in hand is uncommitted either way, so nothing is lost.
        """

        try:
            while True:
                try:
                    await self._round()
                except _RoomTrouble as trouble:
                    await self._back_off(trouble)
        except RoomRefused:
            _logger.error(
                "the homeserver refuses this worker's sync; ending the run because no "
                "amount of retrying changes a refusal"
            )
            raise
        except asyncio.CancelledError:
            _logger.info(
                "stopping: the batch in hand is left uncommitted so it arrives again"
            )
            raise

    async def _round(self) -> None:
        await self._drain()
        batch = await self._sync()
        await self._accept_invites(batch)
        if self._inbox.is_baseline():
            # A sync without ``since`` answers with history. Adopting it as the
            # starting line is the only correct thing to do with it; invitations
            # are the exception and were handled a line ago, because a worker
            # that skipped them on its first round would be deaf in every room
            # it had not yet joined until somebody invited it a second time.
            self._inbox.record_baseline(batch)
        else:
            for trigger in self._inbox.triggers(
                batch,
                matrix_user_id=self._enrollment.matrix_user_id,
                allowed_rooms=self._rooms,
            ):
                await self._run_turn(trigger)
            self._inbox.commit(batch)
        self._failures = 0

    # -- the room side ------------------------------------------------------

    async def _sync(self) -> RoomBatch:
        """Read one batch, or decide whether the room is worth waiting for.

        A refusal is re-raised untouched rather than wrapped, so ``serve`` sees
        the type and not a backoff: nothing this process can do makes a rejected
        identity acceptable, and a Bridge that cannot read is not serving
        whatever it looks like from outside.
        """

        try:
            return await self._room_port.sync(
                since=self._inbox.since(), timeout_ms=self._sync_timeout_ms
            )
        except RoomRefused:
            raise
        except Exception as trouble:
            raise _RoomTrouble(f"sync failed: {trouble}") from trouble

    async def _accept_invites(self, batch: RoomBatch) -> None:
        """Join every invitation into a confirmed room, and log the rest.

        The trust test is the *room*, not the person who sent the invitation.
        The confirmed list came from RepoMesh's preflight, which this process
        cannot edit; an inviter allowlist would be a local file an operator could
        widen by accident. The inviter is logged and decides nothing.

        Joining is idempotent, and every round offers every pending invitation
        again, so a Bridge that was killed between the invitation and the join
        converges on the next round without any of this being written down.

        A room that refuses the join is damage to one room, not to the worker:
        the other invitations in the batch are still worth accepting and the
        rounds behind them are still worth running, so this one is logged and
        skipped without a backoff. There is nothing to write down either — the
        invitation either reappears in a later sync, in which case the refusal
        repeats and is reported again, or it does not, in which case there was
        never anything to remember.
        """

        for invite in batch.invites:
            who = invite.inviter or "an inviter the homeserver did not name"
            if invite.room_id not in self._rooms:
                _logger.warning(
                    "declining an invitation to %s from %s: RepoMesh has not confirmed "
                    "that room for this worker",
                    invite.room_id,
                    who,
                )
                continue
            try:
                await self._room_port.join(invite.room_id)
            except RoomRefused as refusal:
                _logger.warning(
                    "cannot join %s (invited by %s): the homeserver refused, and this "
                    "worker will not act in that room (%s)",
                    invite.room_id,
                    who,
                    refusal,
                )
                continue
            except Exception as trouble:
                raise _RoomTrouble(f"join {invite.room_id} failed: {trouble}") from trouble
            _logger.info("joined %s, invited by %s", invite.room_id, who)

    async def _drain(self) -> None:
        """Hand every unacknowledged intent to its room, oldest first.

        Nothing is rendered here and nothing is named here. The body is the one
        the outbox already produced and the transaction id is the one it already
        derived, which is exactly why a resend after a crash is a no-op at the
        homeserver instead of a second message in the room.

        A message the room refuses outright is put down rather than retried, and
        the loop moves to the next one. Making that fatal instead would be the
        worst of the three options available: this drain runs at the head of
        every round, so the process would refuse one message, restart, refuse it
        again, and never reach anything behind it. Dropping it in silence would
        be the other bad option. A dead letter plus one ERROR is the only answer
        that keeps the queue moving and still says out loud what the room never
        heard. The log line carries the transaction id and the room and *not*
        the body — a message that could not be delivered is still the room's
        text, and this machine's log is not where it goes.
        """

        for send in self._outbox.pending():
            try:
                event_id = await self._room_port.send(
                    room_id=send.room_id,
                    thread_root_id=send.thread_root_id,
                    txn_id=send.txn_id,
                    body=send.body,
                )
            except RoomRefused as refusal:
                self._outbox.mark_refused(send.txn_id)
                _logger.error(
                    "the room %s permanently refused intent %s; it is now a dead letter "
                    "and no drain will offer it again (%s)",
                    send.room_id,
                    send.txn_id,
                    refusal,
                )
                continue
            except Exception as trouble:
                raise _RoomTrouble(f"send {send.txn_id} failed: {trouble}") from trouble
            self._outbox.mark_sent(send.txn_id, event_id)

    async def _back_off(self, trouble: _RoomTrouble) -> None:
        delay = min(
            BACKOFF_CEILING_SECONDS,
            _BACKOFF_FLOOR_SECONDS * 2 ** min(self._failures, _BACKOFF_DOUBLINGS),
        )
        self._failures += 1
        _logger.warning(
            "matrix round failed (%s); retrying in %.0fs with the cursor unchanged",
            trouble,
            delay,
        )
        await self._sleep(delay)

    # -- one turn -----------------------------------------------------------

    async def _run_turn(self, trigger: Trigger) -> None:
        """Answer one mention, or record why it was not answered.

        ``settle`` is in a ``finally`` and not on the happy path, because the
        alternative fails in the least visible way available: a turn that raised
        between the claim and the settle would stay ``in_flight`` with a live
        claim in this process, and every replay of it would then be refused as
        "already running" by the very instance that abandoned it.
        """

        claim = self._inbox.claim(trigger)
        if not claim.granted:
            _logger.info(
                "not answering %s in %s: %s", trigger.event_id, trigger.room_id, claim.reason
            )
            return
        _logger.info(
            "answering %s in %s (%s)", trigger.event_id, trigger.room_id, claim.reason
        )
        status = "failed"
        try:
            answer = await self._decide(trigger)
            status = answer.status
            self._outbox.enqueue(
                room_id=trigger.room_id,
                thread_root_id=trigger.thread_root_id,
                trigger_event_id=trigger.event_id,
                observations=answer.observations,
                lane=answer.lane,
            )
            await self._drain()
        except asyncio.CancelledError:
            # Not a terminal state: an operator's Ctrl-C is not an answer, and a
            # mention interrupted by one has to stay answerable on the next start
            # rather than be refused forever as a duplicate.
            status = "cancelled"
            raise
        finally:
            self._inbox.settle(trigger, status)

    async def _decide(self, trigger: Trigger) -> _Answer:
        """Put the turn to the coding session and classify what came back.

        No room IO happens here, so a transport failure during the send cannot
        overwrite what the session actually said: the caller keeps the status
        this function returned and settles the ledger with it even if the drain
        afterwards fails.

        Every ``return`` also names a lane, and the two lanes fall out of the
        three exits rather than being chosen: the two failure paths synthesise
        text this module wrote, the success path forwards text the session
        wrote, and those are exactly the two sequences that must stay
        independently idempotent when a turn is replayed.
        """

        request = TurnRequest(
            room_id=trigger.room_id,
            thread_id=trigger.thread_id,
            trigger_event_id=trigger.event_id,
            prompt=trigger.prompt,
            native_session_id=self._state.resume_handle(
                trigger.room_id, trigger.thread_id, profile=self._enrollment.coding_profile
            ),
        )
        try:
            async with asyncio.timeout(self._turn_timeout):
                outcome = await self._coding_session.respond(request)
        except TimeoutError:
            _logger.warning(
                "turn %s ran past %.0fs and was abandoned; it stays answerable",
                trigger.event_id,
                self._turn_timeout,
            )
            return _Answer("timeout", NOTE_LANE, (self._note(trigger, TIMEOUT_NOTE),))
        except Exception:
            # The traceback belongs to this machine. The room gets one line with
            # nothing in it that a reader could act on or a stranger could learn
            # from, which is what makes the room safe to be a room.
            _logger.exception("turn %s failed", trigger.event_id)
            return _Answer("failed", NOTE_LANE, (self._note(trigger, FAILURE_NOTE),))
        # Bound the moment the session announces it, before the response is even
        # written down: the window between "a session exists" and "the turn
        # finished" is exactly where a crash loses a thread's context.
        self._state.bind_session(
            trigger.room_id,
            trigger.thread_id,
            profile=self._enrollment.coding_profile,
            native_session_id=outcome.native_session_id,
        )
        self._state.count_turn(
            trigger.room_id, trigger.thread_id, profile=self._enrollment.coding_profile
        )
        return _Answer(
            self._terminal(outcome.status, trigger), TURN_LANE, outcome.observations
        )

    def _terminal(self, status: str, trigger: Trigger) -> str:
        if status in TERMINAL_TURN_STATES:
            return status
        _logger.warning(
            "the coding session ended turn %s with an unknown status %r; recording it as failed",
            trigger.event_id,
            status,
        )
        return "failed"

    def _note(self, trigger: Trigger, text: str) -> RoomObservation:
        """One canned line about a turn that did not produce its own.

        Always position zero of the note lane, because a turn produces at most
        one of these. Two attempts that both time out therefore derive the same
        identity and the room hears about it once, which is the right answer:
        the second timeout is not news.

        The identity is derived rather than generated. The outbox re-derives the
        same value from the same five parts — the lane among them — when it
        writes the row, so spelling it here costs nothing and keeps the
        supervisor free of any source of randomness, which is the property that
        makes a replayed turn land on the row it landed on last time.
        """

        return RoomObservation(
            observation_id=observation_id(
                self._enrollment.worker_agent_id,
                trigger.room_id,
                trigger.event_id,
                NOTE_LANE,
                0,
            ),
            emitted_at=self._state.now(),
            worker_name=self._enrollment.worker_name,
            room_id=trigger.room_id,
            kind="note",
            body=text,
        )
