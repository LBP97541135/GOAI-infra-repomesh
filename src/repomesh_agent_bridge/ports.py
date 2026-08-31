"""The Bridge's three seams (ADR 0004 decision 4).

These are seams because each has real variation behind it: a control plane over
HTTP versus an in-memory double, a Matrix client versus an inert stand-in, a
coding CLI versus a scripted session. Local state and credential resolution are
deliberately *not* ports — SQLite is its own test stand-in, and resolution is an
injected ``resolve(ref) -> secret`` callable.

A fourth arrived with governed execution: :class:`GovernedTaskPort`, the one
action the Bridge is allowed to take on RepoMesh's behalf. It is a separate
seam from :class:`WorkerBindingPort` even though both speak to the same control
plane, because they are separated by everything except their host — one is read
once at startup and decides whether the process runs at all, the other is a
write performed mid-session on behalf of somebody in a room, and a double for
one is never a usable double for the other.

A fifth arrives with the leader lane: :class:`LeaderActionPort`, the decision
surface a Repository Leader plans and reviews through (adjudication D-1 — over
HTTP on the existing ``agent-actions`` face, because six external members and
no containers means an MCP channel would have zero consumers). It is its own
seam for the reason the governed one is: the two are used by different roles in
the same process family, a worker Bridge never touches this one, and the phase
machine behind it — planning, executing, review_due — is state a start-task
double has no way to express. Its two implementations are the HTTP adapter and
the in-memory one next to it, which is what makes it a seam rather than an
interface with a single caller.

:class:`LeaderCoordinationPort` beside it is a narrower thing and is written
down for a narrower reason: the module that sequences a leader's round has to
call the leader's own session, and a core module may not import an adapter to
learn the type it calls. One stack sits behind it and behind
:class:`CodingSessionPort` alike; what this names is the other *reading* of that
stack's answer.

Two failure vocabularies meet in this package and they live in different
modules for one reason each. The *preflight* one lives in
:mod:`repomesh_agent_bridge.contracts`, next to the wire models that raise it:
the same refusal covers "the transport said no" and "the body is not a
binding", so splitting it across two modules would only force adapters to
import both. The *room transport* one — :class:`RoomTransportError` and its two
halves — lives here, because the supervisor has to tell a retryable failure
from a permanent refusal and may not import an adapter to do it; a failure type
the core branches on is part of the port's contract, not of whoever implements
it. :class:`GovernedTaskError` is here for that second reason: the supervisor
tells a room "RepoMesh said no, and here is what it said" apart from "RepoMesh
could not be asked", and it may not import an adapter to do it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import NewType, Protocol
from uuid import UUID

from .contracts import (
    ExternalWorkerEnrollment,
    PlanReceipt,
    RepositoryAssignmentPackage,
    RepositoryPlanDecision,
    RepositoryReviewDecision,
    ReviewReceipt,
    RoomObservation,
    WorkerBinding,
)

__all__ = [
    "CodingSessionPort",
    "GovernedStartReceipt",
    "GovernedTaskError",
    "GovernedTaskPort",
    "GovernedTaskRefused",
    "GovernedTaskUnavailable",
    "LeaderActionError",
    "LeaderActionPort",
    "LeaderActionRefused",
    "LeaderActionUnavailable",
    "LeaderCoordinationPort",
    "RoomBatch",
    "RoomBody",
    "RoomEvent",
    "RoomInvite",
    "RoomPort",
    "RoomRefused",
    "RoomTransportError",
    "RoomUnavailable",
    "TurnOutcome",
    "TurnRequest",
    "WorkerBindingPort",
]

RoomBody = NewType("RoomBody", str)
"""Text that is allowed to enter a Matrix room.

The one legitimate constructor is ``outbox.render``: everything a room sees is
the display projection of a ``RoomObservation``, never a raw transcript, a
THINKING block or a protocol frame. ``NewType`` makes smuggling anything else
into ``RoomPort.send`` a deliberate act — an author has to write ``RoomBody(raw)``
by hand — and a source-scan test pins that the spelling appears nowhere else.
"""


@dataclass(frozen=True, slots=True)
class RoomEvent:
    """One timeline message, already reduced to what the inbox decides on."""

    event_id: str
    room_id: str
    sender: str
    body: str
    origin_server_ts: int
    thread_root_id: str | None
    mentions_me: bool


@dataclass(frozen=True, slots=True)
class RoomInvite:
    """A pending invitation. Whether to join is the supervisor's call, not the
    adapter's: the trust test is room membership in the preflight-confirmed
    list, and only the caller holds that list."""

    room_id: str
    inviter: str


@dataclass(frozen=True, slots=True)
class RoomBatch:
    """One ``/sync`` answer. Events are oldest first; ``limited_rooms`` names
    rooms whose timeline was truncated (logged, deliberately not backfilled)."""

    next_batch: str
    events: tuple[RoomEvent, ...]
    invites: tuple[RoomInvite, ...]
    limited_rooms: tuple[str, ...]


class WorkerBindingPort(Protocol):
    """RepoMesh preflight: the one control plane the Bridge asks about binding.

    The Bridge holds no AgentTeams management credential and never calls the Go
    controller, so this port is the only way it can learn that its worker is
    really external and which rooms it may act in.
    """

    requires_credential: bool
    """Whether :meth:`fetch_binding` needs the ``credentialRefs.repomesh`` value.

    Stage 1 reads this *before* opening a socket: a port that authenticates
    turns a missing ``repomesh`` reference into a local refusal, so the process
    never makes a call it already knows will be rejected. A double that answers
    from memory sets it False and stage 1 stops demanding a secret nothing will
    use.
    """

    async def fetch_binding(
        self, enrollment: ExternalWorkerEnrollment, *, credential: str | None
    ) -> WorkerBinding:
        """Return RepoMesh's binding for ``enrollment``'s worker.

        Raises ``BindingUnavailable`` when a retry might succeed and
        ``BindingRefused`` when it will not. The credential is passed per call
        rather than held by the adapter so the resolved secret's lifetime is the
        call's, not the process's.
        """
        ...


class RoomTransportError(RuntimeError):
    """A room port could not do what it was asked.

    Its own family rather than a reuse of ``contracts.BridgeStartupError``: that
    one is documented as "anything that stops a Bridge instance from *starting*"
    and the CLI maps the whole family onto one exit code, but a homeserver that
    502s three hours into a session has not stopped anything from starting. The
    composition root is where the two vocabularies meet — a failure raised out
    of :meth:`RoomPort.start` is a startup refusal and belongs to whoever wires
    ``run``; a failure raised out of ``sync``, ``join`` or ``send`` is a
    steady-state event and belongs to the supervisor.
    """


class RoomUnavailable(RoomTransportError):
    """A retry may well get a different answer.

    Split from :class:`RoomRefused` by "can a retry fix it", not by any
    transport's taxonomy, and split at exactly the line the preflight adapter
    already draws between ``BindingUnavailable`` and ``BindingRefused``:
    connection failures, timeouts, and a server that is overloaded or down land
    here. Everything the supervisor's backoff exists for is this class.
    """


class RoomRefused(RoomTransportError):
    """A retry will not change the answer.

    A revoked token, a room this identity may not enter, a message the server
    will never accept, a reply body that is not what the protocol says it is.
    Backing off on one of these produces a warning a minute, forever, while
    nothing recovers — so the supervisor grades them by what the refusal
    actually costs: a refused ``sync`` means the whole identity is unusable and
    ends the run, a refused ``send`` dead-letters that one intent, and a refused
    ``join`` skips that one room.
    """


class RoomPort(Protocol):
    """The Matrix side of the Bridge: transport and parsing, zero decisions.

    Which rooms are answerable, which events are triggers, and what transaction
    id a message carries all arrive from the caller — an adapter that cannot
    invent an outbound identity is what makes "no duplicate room message after a
    crash" checkable in one place (the outbox), not scattered across two.
    """

    async def start(
        self,
        *,
        homeserver_url: str,
        user_id: str,
        room_ids: Sequence[str],
        access_token: str,
    ) -> None:
        """Open the connection as ``user_id``, scoped to the confirmed rooms.

        The token is passed per call rather than held by the adapter for the
        same reason ``fetch_binding`` takes its credential: the resolved
        secret's lifetime is the call's, not the process's.

        Raises :class:`RoomUnavailable` when the server could not be reached and
        :class:`RoomRefused` when it answered and said no — including a token
        that belongs to somebody other than ``user_id``. Either one out of this
        method means the instance never started.
        """
        ...

    async def sync(self, *, since: str | None, timeout_ms: int) -> RoomBatch:
        """Long-poll once. ``since=None`` is the baseline round: return the
        current position without waiting for new messages.

        Raises :class:`RoomUnavailable` for anything a retry might survive.
        :class:`RoomRefused` here means this identity can no longer read its
        rooms at all — a revoked token is the usual cause — and the supervisor
        ends the run on it rather than retrying a decision.
        """
        ...

    async def join(self, room_id: str) -> None:
        """Accept an invitation. Idempotent on a room already joined.

        Raises :class:`RoomUnavailable` when a retry might get in and
        :class:`RoomRefused` when it will not; the latter is damage to one room,
        not to the worker, and the supervisor skips that room and carries on.
        """
        ...

    async def send(
        self, *, room_id: str, thread_root_id: str | None, txn_id: str, body: RoomBody
    ) -> str:
        """Send one message under the caller's transaction id; return the
        event id the homeserver assigned. Retrying with the same ``txn_id``
        must be server-side deduplicated, never re-keyed here.

        Raises :class:`RoomUnavailable` when the message may still get through
        later and :class:`RoomRefused` when it never will, which the supervisor
        turns into a dead letter — an intent no drain will offer again.
        """
        ...

    async def close(self) -> None:
        """Stop syncing. Must be safe on a port that was never started."""
        ...


@dataclass(frozen=True, slots=True)
class TurnRequest:
    """One conversational turn, addressed to whatever session serves it."""

    room_id: str
    thread_id: str
    trigger_event_id: str
    prompt: str
    native_session_id: str | None
    """Resume handle from an earlier turn in this thread, or None for a cold
    start. Bound the moment a session announces one, not when a turn ends."""


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    """What a turn produced. Deliberately has no raw-transcript field: a
    session that wanted to hand the supervisor its protocol frames has no slot
    to put them in, and the room only ever sees rendered observations."""

    observations: tuple[RoomObservation, ...]
    native_session_id: str | None
    status: str
    """``completed`` | ``failed`` | ``blocked``. Cancellation is synthesised by
    the supervisor, which is the only party that knows why the loop stopped."""

    denied_tool_requests: int = 0
    """How many tool/permission requests the session's policy denied this turn.
    The *fact* of denial travels so a room can be told the answer ran nothing;
    the frames that carried it — tool names, arguments, output — never do."""


class CodingSessionPort(Protocol):
    """The local coding CLI, as the Bridge sees it.

    In production this becomes a thin adapter over the Runner's
    ``ProtocolDriver.execute`` (ADR 0004 decision 4, plan decision 5); the Runner
    driver stack is consumed, never copied. The *port* speaks conversation from
    PR 3 on — what PR 4 adds is the real CLI adapter and the restricted
    ``ProcessFactory`` that is allowed to launch one at all; until then every
    implementation answers from memory and spawns nothing.
    """

    async def ensure_ready(self) -> None:
        """Prove this session can serve, before the process takes anything on.

        The startup gate. A Bridge that starts syncing before its CLI is
        installed, logged in, or able to run under this machine's restrictions
        does the most damaging thing available: the baseline round writes off
        the room's backlog as already read, every later mention gets a canned
        failure note, and nothing anywhere says the operator has one command
        left to run. Refusing to start says it instead, by raising
        :class:`~repomesh_agent_bridge.contracts.SessionNotReady` with a message
        an operator can act on.

        Called once, after RepoMesh preflight and before the state file exists,
        so a runtime that is turned away leaves no database behind, is never
        seen by a room, and hands the worker's claim straight back.

        A refusal must leave nothing to close: this is a gate, not an open, and
        :meth:`close` is only owed a session that got past it. An implementation
        that spawns a probe reaps it before it raises. An implementation with
        nothing to verify — the inert stand-in, a scripted double — does nothing
        here, which is the honest answer rather than a stub.
        """
        ...

    async def respond(self, turn: TurnRequest) -> TurnOutcome:
        """Serve one turn. Implementations resume ``native_session_id`` when
        they can and must treat a handle they did not issue as absent."""
        ...

    async def close(self) -> None:
        """Release the session. Must be safe when no session was ever opened."""
        ...


@dataclass(frozen=True, slots=True)
class GovernedStartReceipt:
    """What RepoMesh answered when it took a task on: two ids, and nothing else.

    The start action's answer also names the workspace it prepared — an id, an
    absolute path on the machine that holds it, and the sha it was cut from.
    None of those is a fact the Bridge acts on and one of them is a path, so
    this record has no slot to put them in and they stop at the adapter.
    """

    run_id: UUID
    task_id: UUID


class GovernedTaskError(RuntimeError):
    """A governed run was not started.

    Its own family rather than a reuse of the preflight one, for the reason the
    room transport got its own: those refusals mean "this instance never
    started" and the CLI maps them onto an exit code, while one of these is a
    single mention that could not be honoured by a process that goes on serving
    its rooms. Which of the two halves below arrived decides what the room is
    told, and that decision belongs to the supervisor, so the vocabulary lives
    on the port rather than in whichever adapter raised it.
    """


class GovernedTaskRefused(GovernedTaskError):
    """RepoMesh was asked and said no.

    A task that does not exist, a worker that is not its assignee, a credential
    this installation does not accept. The message is RepoMesh's own words about
    a decision it made, so it is display-safe: the supervisor puts it in the
    room behind a canned prefix, because the person who asked for the run is the
    person who needs to hear which of those it was. Nothing else about the
    exchange — no path, no status code, no credential — is ever in it.
    """


class GovernedTaskUnavailable(GovernedTaskError):
    """RepoMesh could not be asked, or could not answer.

    A connection that failed, a request that timed out, a control plane that is
    down. Split from :class:`GovernedTaskRefused` by "did RepoMesh decide
    anything", which is the only distinction the room cares about: a refusal has
    a reason worth repeating and this does not. Nothing retries it — see
    :meth:`GovernedTaskPort.start_task`.
    """


class GovernedTaskPort(Protocol):
    """The one action the Bridge may take on RepoMesh's behalf.

    A room message is a *wake-up*, never an authorisation. Whether the task
    exists, whether this worker is its assignee, whether the run is permitted
    and when it is over are all RepoMesh's to answer, and none of them is
    re-decided here from what somebody typed in a room: this port carries an id
    and an identity to the control plane and brings back either a receipt or a
    refusal. That is what keeps a Matrix room from becoming a second, weaker
    permission system.
    """

    async def start_task(
        self, *, task_id: UUID, worker_agent_id: UUID
    ) -> GovernedStartReceipt:
        """Ask RepoMesh to start ``task_id`` for ``worker_agent_id``.

        One attempt, and deliberately no retry anywhere behind this call. The
        action is not a read: a retry that RepoMesh did answer — slowly, or after
        the connection dropped — would be a second request for work that is
        already under way. RepoMesh's own in-flight reuse makes a *human*
        re-mention safe, because a start for a task whose run has not finished
        returns that run's receipt rather than dispatching a second one; that is
        an answer only the control plane can give, so the recovery the Bridge
        offers is to tell the room it failed and let a person ask again.

        Raises :class:`GovernedTaskRefused` when RepoMesh decided against it and
        :class:`GovernedTaskUnavailable` when it never decided at all.
        """
        ...


class LeaderActionError(RuntimeError):
    """A leader action did not complete.

    Its own family for the reason the governed one has its own: none of these
    stops a process from starting, and the two halves below are told apart by
    the only distinction the caller acts on — did RepoMesh decide something.
    """


class LeaderActionRefused(LeaderActionError):
    """RepoMesh was asked and said no, in the frozen contract's own vocabulary.

    Carries the machine-readable ``code`` beside the sentence, because the two
    are used by different readers: a leader lane branches on the code — a
    ``phase_conflict`` on a replayed submission is not the same event as a
    ``plan_invalid_dag_cycle`` — while the message is what a room or an operator
    is shown. Both come from the server's own structured error body; nothing
    about the exchange, no status code and no credential, is added to either.

    An unreadable 2xx body lands here too, on the same reasoning the governed
    adapter uses for its receipts: an answer this process cannot parse is not an
    answer, and a retry will not make it one. Such a refusal carries no code from
    the frozen enum, because the server never named one.
    """

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        """The frozen ``structured-error`` code, or ``None`` when the refusal was
        not one the server put a code on. Never assumed to be in the frozen
        enum: an unrecognised code is carried through as itself rather than
        turned into a crash at the moment the two sides have drifted."""


class LeaderActionUnavailable(LeaderActionError):
    """RepoMesh could not be asked, or could not answer.

    A connection that failed, a request that timed out, a 429, a 5xx. Split from
    :class:`LeaderActionRefused` by "did RepoMesh decide anything", which is what
    decides whether there is a reason worth repeating to the leader.
    """


class LeaderActionPort(Protocol):
    """The Repository Leader's decision surface (``contracts/leader-actions/v1``).

    Three calls and a phase machine behind them: read the facts, submit the
    plan, submit the verdict. What the leader may do is bounded by what this
    port can express, which is the point — nothing here can start, edit or
    execute a coding task, so a leader that wanted to do a worker's job has no
    method to do it with (AC-02). The reverse boundary is the server's and is
    unchanged: a leader token calling ``start-worker-task`` is still refused.

    **Nothing here retries.** The reads are safe to repeat and the writes are
    idempotent *at the server* — the leader task id keys a plan, the leader task
    id and review revision key a verdict — so a replay is safe because RepoMesh
    makes it safe, and re-issuing a request the control plane may already have
    answered is a judgement only it can make. An implementation that retried
    would be inventing that judgement, which is the same reasoning
    :meth:`GovernedTaskPort.start_task` is built on.

    Every method raises :class:`LeaderActionRefused` when RepoMesh decided
    against the call and :class:`LeaderActionUnavailable` when it never decided.
    """

    async def fetch_assignment(self, task_id: UUID) -> RepositoryAssignmentPackage:
        """Read the fact package for ``task_id`` in whatever phase it is in.

        Valid in every phase, and the only way the leader learns anything about
        the work: the roster it may assign to, the envelope its plan is clamped
        against, and — from ``review_due`` on — the evidence its verdict has to
        be based on. Facts and non-authoritative hints; never a workspace, and
        never a plan the server wrote on the leader's behalf.
        """
        ...

    async def submit_plan(
        self, task_id: UUID, decision: RepositoryPlanDecision
    ) -> PlanReceipt:
        """Submit the leader's Spec, DAG and worker tasks; return the receipt.

        Valid only in ``planning``; anywhere else RepoMesh answers
        ``phase_conflict``. An identical resubmission returns the original
        receipt, which is what makes a replay after an ambiguous failure safe;
        a *different* plan under the same key is refused rather than silently
        replacing the first.
        """
        ...

    async def submit_review(
        self, task_id: UUID, decision: RepositoryReviewDecision
    ) -> ReviewReceipt:
        """Submit the leader's verdict over the current round; return the receipt.

        Valid only in ``review_due``. This is the only way a leader task reaches
        a terminal status in leader mode — there is no automatic roll-up — so a
        round that is never reviewed stays open rather than resolving itself.
        """
        ...


class LeaderCoordinationPort(Protocol):
    """The leader's own session, read as decisions instead of as room text.

    Not a second :class:`CodingSessionPort` and not a second session stack: one
    ``DriverCodingSession`` serves both lanes, and this is the *reading* the
    leader lane takes off it (adjudication B2-1). The two ends are what differ —
    the prompt is a rendered fact package and the answer is a structured
    decision — which is why the port is separate even though the process behind
    it is the same one the conversation lane spawns.

    It is a port at all so that the module which sequences a leader's round can
    depend on the reading without importing the adapter that performs it: a core
    module may not import an adapter to learn a type it calls, and the supervisor
    calls both of these methods in a fixed order.

    Both raise
    :class:`~repomesh_agent_bridge.contracts.LeaderDocumentInvalid` when the
    session did not produce a decision, when what it produced is not the
    document the freeze describes, or when that document is outside the
    assignment's own safety envelope. A malformed answer is refused and never
    repaired (adjudication B2-2), so an implementation that "fixed" one would be
    submitting a plan its leader did not write.
    """

    async def plan(self, package: RepositoryAssignmentPackage) -> RepositoryPlanDecision:
        """Turn a ``planning`` package into this leader's Spec, DAG and tasks."""
        ...

    async def review(self, package: RepositoryAssignmentPackage) -> RepositoryReviewDecision:
        """Turn a ``review_due`` package into this leader's evidence-based verdict."""
        ...
