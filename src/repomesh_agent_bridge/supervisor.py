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

Six further shapes are deliberate:

**A room message wakes work up and never authorises it.** Some mention shapes go
to RepoMesh instead of to the coding session — ``start task <uuid>``, the
platform's own dispatch, and a Repository Leader's two notices — and what they
carry is an id and this member's identity. Whether the task exists, who may act
on it, what phase it is in and when it is finished are answered by the control
plane and are never re-decided from what somebody typed in a room; the room gets
a receipt or a refusal. Nothing retries any of those actions either, which is
why the refusal lines ask a *person* to try again.

**Which shapes are read at all is the enrollment's decision, not the message's.**
The leader notices are read only by a ``repository_leader`` and the same bytes
stay conversation for a worker, because RepoMesh addresses those messages to the
member it parked a round on. This is the one place in the loop where a member's
role changes what a room message means.

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
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from uuid import UUID

from .contracts import ExternalWorkerEnrollment, LeaderDocumentInvalid, RoomObservation
from .inbox import Inbox, Trigger
from .leader_lane import PLAN_NOTICE, REVIEW_NOTICE, LeaderNotice, parse_leader_notice
from .outbox import NOTE_LANE, RUN_LANE, TURN_LANE, Outbox, observation_id
from .ports import (
    CodingSessionPort,
    GovernedStartReceipt,
    GovernedTaskPort,
    GovernedTaskRefused,
    GovernedTaskUnavailable,
    LeaderActionPort,
    LeaderActionRefused,
    LeaderActionUnavailable,
    LeaderCoordinationPort,
    RoomBatch,
    RoomPort,
    RoomRefused,
    TurnRequest,
)
from .state import TERMINAL_TURN_STATES, BridgeState

__all__ = [
    "BACKOFF_CEILING_SECONDS",
    "FAILURE_NOTE",
    "GOVERNANCE_DISABLED_NOTE",
    "GOVERNANCE_REFUSED_PREFIX",
    "GOVERNANCE_UNAVAILABLE_NOTE",
    "LEADER_DRAFT_REFUSED_NOTE",
    "LEADER_LANE_DISABLED_NOTE",
    "LEADER_PHASE_MOVED_TEMPLATE",
    "LEADER_PLAN_ACCEPTED_TEMPLATE",
    "LEADER_REFUSED_PREFIX",
    "LEADER_REVIEW_ACCEPTED_TEMPLATE",
    "LEADER_REWORK_SUFFIX",
    "LEADER_UNAVAILABLE_NOTE",
    "RUN_ACCEPTED_BODY",
    "SYNC_TIMEOUT_MS",
    "TIMEOUT_NOTE",
    "TURN_TIMEOUT_SECONDS",
    "AssignmentDirective",
    "LeaderRuntime",
    "RoomSupervisor",
    "assignment_directive",
    "governed_task_id",
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

GOVERNANCE_DISABLED_NOTE = (
    "This bridge instance cannot start governed runs; it was launched conversation-only."
)
GOVERNANCE_UNAVAILABLE_NOTE = (
    "I could not reach RepoMesh, so nothing was started. Ask me again and I will retry."
)
GOVERNANCE_REFUSED_PREFIX = "RepoMesh will not start that task: "
RUN_ACCEPTED_BODY = "Task accepted; governed run is queued."
"""What a room hears about a governed wake-up, in the four cases that needed
their own words. A start that hangs is told with :data:`TIMEOUT_NOTE`, because
running out of time means the same thing whoever was being waited for.

The refusal is the one that carries RepoMesh's own words, and it is the one that
has to: "you are not the assignee" and "there is no such task" are different
problems for the person who asked, and a canned line would send them to read a
log they do not have. The other three say nothing a stranger could learn from.

The unavailable line invites a retry *by a person*, which is the only retry
there is — nothing behind this module re-sends a start action, because a start
that RepoMesh may have received is not safe to repeat automatically.
"""

LEADER_LANE_DISABLED_NOTE = (
    "This bridge instance cannot submit leader decisions; it was launched conversation-only."
)
LEADER_UNAVAILABLE_NOTE = (
    "I could not reach RepoMesh, so nothing was submitted. Ask me again and I will retry."
)
LEADER_REFUSED_PREFIX = "RepoMesh will not take that leader action: "
LEADER_DRAFT_REFUSED_NOTE = (
    "I would not submit the decision my own session produced, so RepoMesh has nothing from "
    "me for this round. The reason is in this machine's log; ask me again and I will try "
    "once more."
)
"""What a room hears when the Bridge refuses its own leader's draft.

Canned rather than carrying the complaint, and it is the one place this lane
differs from the governed refusal next door. RepoMesh's refusal is the control
plane's own words about a decision it made and is safe to repeat; this refusal
is *about a document the model wrote*, and the sentence that describes what is
wrong with it quotes the document — a node id, a path, an assignee it invented.
The room gets the fact and the operator gets the reason, which is the same split
:data:`FAILURE_NOTE` makes.

It ends by inviting a retry by a person, because that is the only retry there
is: nothing here re-runs a session or re-posts a submission.
"""

LEADER_PLAN_ACCEPTED_TEMPLATE = (
    "Repository plan accepted: revision {revision}, {tasks} worker task(s) dispatched."
)
LEADER_REVIEW_ACCEPTED_TEMPLATE = (
    "Review round {revision} answered {verdict}; RepoMesh now calls this leader task {status}."
)
LEADER_REWORK_SUFFIX = " {count} rework task(s) created."
LEADER_PHASE_MOVED_TEMPLATE = (
    "RepoMesh says this round is {phase} rather than {expected}, so I submitted nothing."
)
"""The four things a room is told about a leader round that reached the server.

Every one of them is built from the receipt's own counters and status words —
never from the leader's product, which is the room's business only to the extent
that RepoMesh accepted it. The phase line is the one an operator sees after a
notice arrives twice: the round has already moved, and saying so is more useful
than silence and more honest than planning it again.
"""

_EXPECTED_PHASE: dict[str, str] = {PLAN_NOTICE: "planning", REVIEW_NOTICE: "review_due"}
"""Which phase each notice is only meaningful in.

Both names come from the frozen ``phase`` enum. The gate they feed is what makes
a replayed notice safe without a second ledger: RepoMesh's phase is the durable
record of whether this round has already been decided, and it is read before any
session is spawned or any decision posted.
"""

_GOVERNED_COMMAND = re.compile(
    r"\bstart\s+task\s+"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)
"""The whole command grammar, and deliberately the whole of it.

Two words and a strict uuid, matched anywhere in the mention so the ``@worker``
a Matrix client puts in front does not have to be described here. Nothing is
fuzzy, nothing is inferred, and a message that is *nearly* this — a mistyped id,
"start the task", the word "start" on its own — is not a command but an ordinary
sentence, which is exactly what it will be treated as. The cost of guessing wrong
in the other direction is a workspace and a run somebody did not ask for, so the
parser refuses to guess at all.

``\\b`` is there for one real message: "restart task <id>". Without it that ends
in ``start task <id>`` and would silently be read as a plain start — which is not
what the person asked for and not what they would be told happened. A word
boundary is the difference between matching a phrase and matching a substring;
it is not a heuristic.
"""


def governed_task_id(prompt: str) -> UUID | None:
    """The task a mention asks to start, or ``None`` if it asks for anything else.

    First match wins: a message naming two tasks is a person being unclear, and
    starting both would be the reading least likely to be what they meant.
    """

    match = _GOVERNED_COMMAND.search(prompt)
    return None if match is None else UUID(match.group(1))


_UUID_PATTERN = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

_ASSIGNMENT_DIRECTIVE = re.compile(
    r"\{\s*"
    rf'\\?"task_id\\?"\s*:\s*\\?"({_UUID_PATTERN})\\?"\s*,\s*'
    rf'\\?"worker_agent_id\\?"\s*:\s*\\?"({_UUID_PATTERN})\\?"'
    r"\s*\}",
    re.IGNORECASE,
)
"""The object RepoMesh already puts in the message that hands a Worker its task.

Not a new protocol and not a parser for one. ``task_orchestration`` writes this
line into every assignment message it sends
(``{"task_id":"…","worker_agent_id":"…"}``), addressed to a Worker that is
expected to read it and act — a containerised Worker does exactly that, through
an MCP tool. An external Worker had no way to, so it read the same message as
conversation and answered that it could not find the tool. This reads the line
that was always addressed to it.

Two shapes, one expression. The plain text is what the platform composes; the
Matrix event usually carries it inside a collaboration envelope, where JSON
encoding turns every ``"`` into ``\\"``. Both are the same directive and
neither is a different message, so the optional backslash is written into the
pattern rather than handled by unwrapping an envelope this module would then
have to know the shape of.

Strict about everything else: both keys, in the order RepoMesh writes them, each
holding a whole uuid. Anything less exact is not a directive, and a message that
merely talks about task ids stays conversation.
"""


@dataclass(frozen=True, slots=True)
class AssignmentDirective:
    """A dispatch RepoMesh addressed to one worker."""

    task_id: UUID
    worker_agent_id: UUID


def assignment_directive(prompt: str) -> AssignmentDirective | None:
    """The task a platform dispatch hands this room, or ``None``.

    Whether *this* worker may act on it is the caller's question, not this
    function's: the directive names its assignee, and reporting who was named is
    how the caller can tell "not for me" from "not a directive at all".
    """

    match = _ASSIGNMENT_DIRECTIVE.search(prompt)
    if match is None:
        return None
    try:
        return AssignmentDirective(UUID(match.group(1)), UUID(match.group(2)))
    except ValueError:  # pragma: no cover - the pattern already shapes both ids
        return None


@dataclass(frozen=True, slots=True)
class LeaderRuntime:
    """The two halves of the Repository Leader lane, which only arrive together.

    A Bridge that could read an assignment package but not decide on it, or
    decide and not submit, would answer a notice with half a round: the leader
    task stays open, RepoMesh waits for a verdict that is never coming, and
    nothing in the room says why. So the decision surface and the session that
    produces decisions for it are one value, built once by the composition root.

    ``close`` is here because the actions port holds a connection pool for the
    life of the process and the session behind it does not belong to this record
    — it is the conversation lane's, already closed by whoever built it. A
    runtime whose port has nothing to release leaves this ``None``.
    """

    actions: LeaderActionPort
    session: LeaderCoordinationPort
    close: Callable[[], Awaitable[None]] | None = None


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
        governed_task: GovernedTaskPort | None = None,
        leader_runtime: LeaderRuntime | None = None,
        sync_timeout_ms: int = SYNC_TIMEOUT_MS,
        turn_timeout_seconds: float = TURN_TIMEOUT_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._enrollment = enrollment
        self._rooms = tuple(confirmed_room_ids)
        self._room_port = room_port
        self._coding_session = coding_session
        self._governed_task = governed_task
        """The control plane, or ``None`` for an instance launched without one.

        Optional rather than required because the two arrangements are both
        legitimate deployments and neither is a degraded version of the other: a
        Bridge wired only to a room and a CLI is a conversational teammate, and
        one that is also given this can be asked to start work RepoMesh
        governs. What is not legitimate is silence — an instance without it says
        so in the room the first time somebody asks.
        """
        self._leader = leader_runtime
        """The leader lane, or ``None`` for an instance that decides nothing.

        Separate from the governed pair above and never held beside it in
        practice: the two belong to different roles, and the composition root
        builds this one only for a ``repository_leader`` enrollment and that one
        only for a worker given ``--workspace-root``. Optional here for the
        reason the governed port is optional — a leader Bridge brought up with
        the conversation stand-in can hear its room without being able to plan,
        and it says so rather than going quiet.
        """
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

        A governed wake-up is answered before any of that and never reaches the
        session at all. The two are alternatives rather than stages: "start task
        <id>" is a request to the control plane, and handing it to a coding CLI
        as well would run the same work twice, once under RepoMesh's governance
        and once outside it.

        Two wake-ups reach that branch and they are tried in this order: the
        platform's own dispatch first, then the typed command. The order is not a
        preference. A message can hold both — an operator answering a dispatch by
        repeating its id — and a single trigger must start a single run, so one
        branch has to win outright, and the one that should is the one RepoMesh
        wrote. Both then go through the same ``_start_governed`` under the same
        trigger event id, which is the idempotency key the whole round is built
        on: one mention, one start, whichever way it was phrased.

        A dispatch naming a different worker is not refused, it is simply not a
        directive *here*. The Bridge drops the reading and treats the message as
        conversation, exactly as it did before this branch existed — refusing it
        would mean answering for a worker whose room this also is.

        None of this is a permission decision, and the security argument is the
        one PR 5 already made for the typed command: a room message is a wake-up
        and nothing more. Whether the task exists, whether it is assigned to this
        worker, and whether this worker may run it are all re-decided by
        RepoMesh's start action against its own records, so a forged dispatch
        buys its author exactly what a forged ``start task`` does — a refusal
        from the control plane, reported into the room.

        A Repository Leader's two notices are read before any of that, and only
        by a leader. RepoMesh addresses them to the member it parked the round
        on, so reading them anywhere else would be answering for somebody whose
        room this also is — and a worker that tried would be refused by the
        server anyway, one round-trip later and with a leader task left waiting.
        The role gate is therefore the enrollment's, not the message's: the same
        bytes are a wake-up for a leader and ordinary conversation for a worker.
        """

        if self._enrollment.is_repository_leader:
            notice = parse_leader_notice(trigger.prompt)
            if notice is not None:
                _logger.info(
                    "mention %s is a RepoMesh %s notice for leader task %s",
                    trigger.event_id,
                    notice.action,
                    notice.task_id,
                )
                return await self._decide_as_leader(trigger, notice)
        directive = assignment_directive(trigger.prompt)
        if directive is not None:
            if directive.worker_agent_id == self._enrollment.worker_agent_id:
                _logger.info(
                    "mention %s carries a RepoMesh dispatch for task %s",
                    trigger.event_id,
                    directive.task_id,
                )
                return await self._start_governed(trigger, directive.task_id)
            _logger.info(
                "mention %s carries a dispatch for worker %s, not this one; "
                "reading it as conversation",
                trigger.event_id,
                directive.worker_agent_id,
            )
        if (task_id := governed_task_id(trigger.prompt)) is not None:
            return await self._start_governed(trigger, task_id)
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

    async def _start_governed(self, trigger: Trigger, task_id: UUID) -> _Answer:
        """Ask RepoMesh to start a task, and tell the room what it said.

        Everything this branch decides is about *reporting*. It does not check
        that the task exists, that the sender may ask for it, or that this
        worker is its assignee, because a room is not a permission system and
        re-deciding any of that here would create a second, weaker answer to a
        question RepoMesh already answers authoritatively. The mention is a
        wake-up; the control plane is the authority.

        The exception discipline is the session path's, for the same reasons: a
        start that hangs is a timeout and stays answerable, and anything the port
        raises outside its own vocabulary is this machine's problem and gets the
        canned failure line rather than escaping into the round.

        The anchor is written before the answer is returned, which puts it
        before the enqueue and the send. A crash in the other order would leave a
        room holding a receipt for a run that nothing on disk can place back into
        the thread that asked for it.
        """

        if self._governed_task is None:
            _logger.warning(
                "mention %s asked to start task %s, but this instance has no control plane",
                trigger.event_id,
                task_id,
            )
            return _Answer("failed", NOTE_LANE, (self._note(trigger, GOVERNANCE_DISABLED_NOTE),))
        try:
            async with asyncio.timeout(self._turn_timeout):
                receipt = await self._governed_task.start_task(
                    task_id=task_id, worker_agent_id=self._enrollment.worker_agent_id
                )
        except TimeoutError:
            _logger.warning(
                "the start action for task %s ran past %.0fs and was abandoned",
                task_id,
                self._turn_timeout,
            )
            return _Answer("timeout", NOTE_LANE, (self._note(trigger, TIMEOUT_NOTE),))
        except GovernedTaskRefused as refusal:
            _logger.info("RepoMesh refused to start task %s: %s", task_id, refusal)
            return _Answer(
                "failed",
                NOTE_LANE,
                (self._note(trigger, f"{GOVERNANCE_REFUSED_PREFIX}{refusal}"),),
            )
        except GovernedTaskUnavailable as trouble:
            _logger.warning("RepoMesh could not be asked to start task %s: %s", task_id, trouble)
            return _Answer(
                "failed", NOTE_LANE, (self._note(trigger, GOVERNANCE_UNAVAILABLE_NOTE),)
            )
        except Exception:
            _logger.exception("the start action for task %s failed", task_id)
            return _Answer("failed", NOTE_LANE, (self._note(trigger, FAILURE_NOTE),))
        self._state.record_anchor(
            run_id=receipt.run_id,
            task_id=receipt.task_id,
            room_id=trigger.room_id,
            thread_root_id=trigger.thread_root_id,
            trigger_event_id=trigger.event_id,
        )
        _logger.info(
            "RepoMesh accepted task %s as run %s, anchored to %s",
            receipt.task_id,
            receipt.run_id,
            trigger.event_id,
        )
        return _Answer("completed", RUN_LANE, (self._accepted(trigger, receipt),))

    async def _decide_as_leader(self, trigger: Trigger, notice: LeaderNotice) -> _Answer:
        """Carry one leader round out, and tell the room the one line it gets.

        The exception discipline is the governed branch's, extended by one case.
        RepoMesh's own refusal is repeated because it is the control plane's
        words about a decision it made — ``phase_conflict`` and "you are not the
        assignee" are different problems for whoever is watching. A control plane
        that could not be asked gets the canned line and no automatic retry, for
        the reason the start action does not retry either: a submission RepoMesh
        may already have taken is not safe for a machine to repeat, and the
        server's own idempotency is what makes a *person* asking again safe.

        The extra case is a decision this Bridge refused to submit. That is not
        a failure of the round and not RepoMesh's doing: the session produced
        something that is not the frozen document, or is outside the
        assignment's own envelope, and it is caught here rather than posted and
        refused as a 409 (adjudication B2-2). The room is told the round
        produced nothing; the sentence that says *what* was wrong quotes the
        model's document and stays in this machine's log.

        Everything lands in the note lane at position zero, which is what makes
        a replayed turn land on the row it landed on last time — one round, one
        line, whatever happened in it.
        """

        if self._leader is None:
            _logger.warning(
                "mention %s is a %s notice for leader task %s, but this instance has no "
                "leader lane",
                trigger.event_id,
                notice.action,
                notice.task_id,
            )
            return _Answer("failed", NOTE_LANE, (self._note(trigger, LEADER_LANE_DISABLED_NOTE),))
        try:
            async with asyncio.timeout(self._turn_timeout):
                line = await self._leader_round(self._leader, notice)
        except TimeoutError:
            _logger.warning(
                "the %s round for leader task %s ran past %.0fs and was abandoned; it stays "
                "answerable",
                notice.action,
                notice.task_id,
                self._turn_timeout,
            )
            return _Answer("timeout", NOTE_LANE, (self._note(trigger, TIMEOUT_NOTE),))
        except LeaderActionRefused as refusal:
            _logger.info(
                "RepoMesh refused the %s for leader task %s (%s): %s",
                notice.action,
                notice.task_id,
                refusal.code,
                refusal,
            )
            return _Answer(
                "failed",
                NOTE_LANE,
                (self._note(trigger, f"{LEADER_REFUSED_PREFIX}{refusal}"),),
            )
        except LeaderActionUnavailable as trouble:
            _logger.warning(
                "RepoMesh could not be asked about leader task %s: %s", notice.task_id, trouble
            )
            return _Answer("failed", NOTE_LANE, (self._note(trigger, LEADER_UNAVAILABLE_NOTE),))
        except LeaderDocumentInvalid:
            # The complaint names parts of the document the session wrote, so it
            # belongs to the operator and not to the room.
            _logger.exception(
                "the %s session for leader task %s produced nothing submittable",
                notice.action,
                notice.task_id,
            )
            return _Answer("failed", NOTE_LANE, (self._note(trigger, LEADER_DRAFT_REFUSED_NOTE),))
        except Exception:
            _logger.exception(
                "the %s round for leader task %s failed", notice.action, notice.task_id
            )
            return _Answer("failed", NOTE_LANE, (self._note(trigger, FAILURE_NOTE),))
        return _Answer("completed", NOTE_LANE, (self._note(trigger, line),))

    async def _leader_round(self, lane: LeaderRuntime, notice: LeaderNotice) -> str:
        """Read the facts, decide on them, submit the decision, say what came back.

        The read comes first and it is the thing that makes a replayed notice
        safe. A notice can arrive twice — an uncommitted cursor, a second
        instance of the same message, a person forwarding it — and the round it
        names may already have been decided. RepoMesh's ``phase`` is the durable
        answer to "has this been decided", it survives this process restarting
        because it was never this process's to keep, and consulting it before
        anything else means a stale notice costs one GET rather than a second
        coordination session and a submission the server would refuse.

        Nothing is validated here that the session does not already validate:
        the decision is held against the freeze and against the assignment's own
        envelope where both halves are in hand, which is inside the coordination
        session, and a second copy of that check would be a second thing to keep
        true.

        One submission per round and no retry anywhere behind it. The writes are
        idempotent at the server — the leader task keys a plan, the leader task
        and the review revision key a verdict — so the recovery on offer for an
        ambiguous failure is the honest one: tell the room, and let a person ask
        again.
        """

        package = await lane.actions.fetch_assignment(notice.task_id)
        expected = _EXPECTED_PHASE[notice.action]
        if package.phase != expected:
            _logger.info(
                "leader task %s is %s, not %s; the %s notice is stale and nothing is submitted",
                notice.task_id,
                package.phase,
                expected,
                notice.action,
            )
            return LEADER_PHASE_MOVED_TEMPLATE.format(phase=package.phase, expected=expected)
        if notice.action == PLAN_NOTICE:
            plan = await lane.session.plan(package)
            plan_receipt = await lane.actions.submit_plan(notice.task_id, plan)
            _logger.info(
                "RepoMesh accepted plan revision %d for leader task %s",
                plan_receipt.plan_revision,
                notice.task_id,
            )
            return LEADER_PLAN_ACCEPTED_TEMPLATE.format(
                revision=plan_receipt.plan_revision,
                tasks=len(plan_receipt.worker_task_ids),
            )
        verdict = await lane.session.review(package)
        review_receipt = await lane.actions.submit_review(notice.task_id, verdict)
        _logger.info(
            "RepoMesh accepted a %s verdict on round %d of leader task %s",
            review_receipt.verdict,
            review_receipt.review_revision,
            notice.task_id,
        )
        line = LEADER_REVIEW_ACCEPTED_TEMPLATE.format(
            revision=review_receipt.review_revision,
            verdict=review_receipt.verdict,
            status=review_receipt.leader_task_status,
        )
        if review_receipt.rework_task_ids:
            line += LEADER_REWORK_SUFFIX.format(count=len(review_receipt.rework_task_ids))
        return line

    def _accepted(self, trigger: Trigger, receipt: GovernedStartReceipt) -> RoomObservation:
        """The receipt, as the one message a room gets for it.

        Position zero of the run lane, so the lifecycle a later consumer appends
        continues the sequence this message opens rather than starting a second
        one. Both ids travel in the observation's own fields rather than in the
        sentence, because the schema has somewhere to put them and the room only
        needs to read one: the renderer shows the run id, which is the handle
        nobody in the room otherwise has.
        """

        return RoomObservation(
            observation_id=observation_id(
                self._enrollment.worker_agent_id,
                trigger.room_id,
                trigger.event_id,
                RUN_LANE,
                0,
            ),
            emitted_at=self._state.now(),
            worker_name=self._enrollment.worker_name,
            room_id=trigger.room_id,
            kind="run_accepted",
            body=RUN_ACCEPTED_BODY,
            task_id=receipt.task_id,
            run_id=receipt.run_id,
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
