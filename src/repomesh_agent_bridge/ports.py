"""The Bridge's three seams (ADR 0004 decision 4).

These are seams because each has real variation behind it: a control plane over
HTTP versus an in-memory double, a Matrix client versus an inert stand-in, a
coding CLI versus a scripted session. Local state and credential resolution are
deliberately *not* ports — SQLite is its own test stand-in, and resolution is an
injected ``resolve(ref) -> secret`` callable.

The failure vocabulary lives in :mod:`repomesh_agent_bridge.contracts` next to
the wire models that raise it: the same refusal covers "the transport said no"
and "the body is not a binding", so splitting it across two modules would only
force adapters to import both.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import NewType, Protocol

from .contracts import ExternalWorkerEnrollment, RoomObservation, WorkerBinding

__all__ = [
    "CodingSessionPort",
    "RoomBatch",
    "RoomBody",
    "RoomEvent",
    "RoomInvite",
    "RoomPort",
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
        """
        ...

    async def sync(self, *, since: str | None, timeout_ms: int) -> RoomBatch:
        """Long-poll once. ``since=None`` is the baseline round: return the
        current position without waiting for new messages."""
        ...

    async def join(self, room_id: str) -> None:
        """Accept an invitation. Idempotent on a room already joined."""
        ...

    async def send(
        self, *, room_id: str, thread_root_id: str | None, txn_id: str, body: RoomBody
    ) -> str:
        """Send one message under the caller's transaction id; return the
        event id the homeserver assigned. Retrying with the same ``txn_id``
        must be server-side deduplicated, never re-keyed here."""
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


class CodingSessionPort(Protocol):
    """The local coding CLI, as the Bridge sees it.

    In production this becomes a thin adapter over the Runner's
    ``ProtocolDriver.execute`` (ADR 0004 decision 4, plan decision 5); the Runner
    driver stack is consumed, never copied. The *port* speaks conversation from
    PR 3 on — what PR 4 adds is the real CLI adapter and the restricted
    ``ProcessFactory`` that is allowed to launch one at all; until then every
    implementation answers from memory and spawns nothing.
    """

    async def respond(self, turn: TurnRequest) -> TurnOutcome:
        """Serve one turn. Implementations resume ``native_session_id`` when
        they can and must treat a handle they did not issue as absent."""
        ...

    async def close(self) -> None:
        """Release the session. Must be safe when no session was ever opened."""
        ...
