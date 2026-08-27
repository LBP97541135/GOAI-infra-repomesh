"""In-memory and inert sides of the three seams.

They ship with the package rather than living in the test tree for two reasons.
The merge gate asks every new HTTP adapter to have an in-memory counterpart, and
one of these is not a test double at all: ``InertCodingSession`` is what the
``run`` subcommand genuinely assembles in this tier, because a real coding CLI
behind a restricted process factory does not arrive until PR 4. Until then the
honest production stand-in is something that hears the room, says so, and codes
nothing.

None of these keeps durable state. The Bridge's local state — cursor, seen set,
turn ledger, outbox, session references — is SQLite, which is its own test
stand-in and never a port.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from ..contracts import ExternalWorkerEnrollment, RoomObservation, WorkerBinding
from ..ports import RoomBatch, RoomBody, TurnOutcome, TurnRequest

__all__ = [
    "InertCodingSession",
    "InMemoryRoomPort",
    "InMemoryWorkerBindingPort",
    "ScriptedCodingSession",
    "SentMessage",
]

INERT_SESSION_NOTE = (
    "I am in this room and I can hear you, but this build cannot run a coding "
    "session yet."
)
"""What the inert session answers with, every time.

Deliberately a whole sentence rather than a status word: the person who @-ed a
worker deserves to learn from the room itself that the answer is a limitation of
this build and not a failure they should retry.
"""


class InMemoryWorkerBindingPort:
    """A control plane that answers from memory.

    Records what it was asked and how often, so a caller can assert the thing
    that matters most about stage 1: that it did not get here at all.
    """

    def __init__(
        self,
        binding: WorkerBinding | None = None,
        *,
        failure: Exception | None = None,
        requires_credential: bool = False,
    ) -> None:
        if binding is None and failure is None:
            raise ValueError("give the port either a binding to answer or a failure to raise")
        self.requires_credential = requires_credential
        self._binding = binding
        self._failure = failure
        self.calls = 0
        self.credentials: list[str | None] = []

    async def fetch_binding(
        self, enrollment: ExternalWorkerEnrollment, *, credential: str | None
    ) -> WorkerBinding:
        self.calls += 1
        self.credentials.append(credential)
        if self._failure is not None:
            raise self._failure
        assert self._binding is not None
        return self._binding


@dataclass(frozen=True, slots=True)
class SentMessage:
    """One message the Bridge handed to a room, exactly as it handed it over."""

    room_id: str
    thread_root_id: str | None
    txn_id: str
    body: RoomBody


class InMemoryRoomPort:
    """A room port whose homeserver a caller writes in advance.

    Answers ``sync`` from a script and records everything the Bridge did, in
    order. The order is the point: "the outbox drained before the first sync"
    and "the invitation was accepted on the baseline round" are claims about
    sequence, and a double that kept only per-method lists could not settle
    them — hence ``calls`` alongside the detailed records.

    When the script runs out, ``sync`` sets :attr:`idle` and then waits. That is
    not a convenience for tests: it is the shape a real long poll has when a room
    is quiet, and it means a caller is always stopped by cancellation rather than
    by running off the end of a fixture.
    """

    def __init__(self, *answers: RoomBatch | BaseException) -> None:
        self._answers: list[RoomBatch | BaseException] = list(answers)
        self.started_rooms: tuple[str, ...] = ()
        self.user_id: str | None = None
        self.homeserver_url: str | None = None
        self.access_token: str | None = None
        self.closed = False
        self.ready = asyncio.Event()
        """Set once ``start`` has run: the local readiness signal this tier has."""
        self.idle = asyncio.Event()
        """Set when the script is exhausted and the port is merely waiting."""
        self.calls: list[str] = []
        self.syncs: list[str | None] = []
        self.joined: list[str] = []
        self.sent: list[SentMessage] = []

    async def start(
        self,
        *,
        homeserver_url: str,
        user_id: str,
        room_ids: Sequence[str],
        access_token: str,
    ) -> None:
        self.calls.append("start")
        self.homeserver_url = homeserver_url
        self.user_id = user_id
        self.started_rooms = tuple(room_ids)
        self.access_token = access_token
        self.ready.set()

    async def sync(self, *, since: str | None, timeout_ms: int) -> RoomBatch:
        del timeout_ms  # nothing here waits on the wire
        self.calls.append("sync")
        self.syncs.append(since)
        while not self._answers:
            self.idle.set()
            await asyncio.Event().wait()
        answer = self._answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    async def join(self, room_id: str) -> None:
        self.calls.append("join")
        self.joined.append(room_id)

    async def send(
        self, *, room_id: str, thread_root_id: str | None, txn_id: str, body: RoomBody
    ) -> str:
        self.calls.append("send")
        self.sent.append(
            SentMessage(
                room_id=room_id, thread_root_id=thread_root_id, txn_id=txn_id, body=body
            )
        )
        return event_id_for(txn_id)

    async def close(self) -> None:
        self.closed = True
        self.ready.clear()


def event_id_for(txn_id: str) -> str:
    """The event id this double hands back for a transaction id.

    Derived from the transaction id and from nothing else, because that is the
    property a real homeserver has and the property the whole outbox design
    rests on: resending an unacknowledged transaction returns the *original*
    event rather than creating a second one. A double that minted a fresh id per
    call would make a crashed-and-resent turn look like a duplicate that never
    happens in production.
    """

    return f"$sent-{txn_id}"


class InertCodingSession:
    """A coding session that never starts a process.

    This is the production stand-in, not a test double: ``run`` assembles it, so
    what a room gets when it @-mentions this build is one honest note. It spawns
    nothing, so ``close`` is always called on a session that was never opened —
    which is the behaviour every implementation of this port owes the shutdown
    path anyway.
    """

    def __init__(self, *, worker_name: str = "bridge") -> None:
        self.worker_name = worker_name
        self.turns: list[TurnRequest] = []
        self.closed = False

    async def ensure_ready(self) -> None:
        """Nothing to verify, so nothing is verified.

        Not a stub standing in for work this class ought to do: there is no
        binary to resolve, no credential to check and no isolation to prove,
        because this session spawns nothing. The gate exists so that a runtime
        with something to prove has somewhere to prove it, and the honest answer
        from a runtime with nothing to prove is to return.
        """

    async def respond(self, turn: TurnRequest) -> TurnOutcome:
        """Answer with a single note, and never with anything else.

        The observation's own id and timestamp are placeholders: the outbox
        derives both from the trigger when it writes the row, precisely so that
        a replayed turn lands on the same names, so the values put here reach
        neither a room nor a wire.
        """

        self.turns.append(turn)
        return TurnOutcome(
            observations=(
                RoomObservation(
                    observation_id=uuid4(),
                    emitted_at=datetime.now(UTC),
                    worker_name=self.worker_name,
                    room_id=turn.room_id,
                    kind="note",
                    body=INERT_SESSION_NOTE,
                ),
            ),
            native_session_id=None,
            status="completed",
        )

    async def close(self) -> None:
        self.closed = True


class ScriptedCodingSession:
    """A coding session whose answers a caller writes in advance.

    A test double, unlike its inert sibling. Answers are consumed in order and
    an exception in the script is raised instead of returned, so "the CLI blew
    up on turn two" is one entry in a list rather than a subclass. Once the
    script is spent every further turn gets ``default``, which keeps a test that
    cares about the first two turns from having to describe the rest.

    ``turns`` is the record that matters most: it is where the resume handle the
    supervisor offered shows up, and "the second mention in this thread carried
    the handle the first one announced" is only observable there.

    ``not_ready`` scripts the startup gate the same way: a real CLI adapter
    refuses when the binary is missing or the machine cannot isolate it, and
    handing this double the same refusal is what lets "a Bridge that cannot code
    never touches a room" be tested without one.
    """

    def __init__(
        self,
        *answers: TurnOutcome | BaseException,
        default: TurnOutcome | None = None,
        not_ready: BaseException | None = None,
    ) -> None:
        self._answers: list[TurnOutcome | BaseException] = list(answers)
        self._default = default or TurnOutcome(
            observations=(), native_session_id=None, status="completed"
        )
        self._not_ready = not_ready
        self.turns: list[TurnRequest] = []
        self.ready_calls = 0
        """How often the gate was asked. Counted rather than flagged so a caller
        can tell "never reached" from "reached once" from "reached every round",
        which are three different bugs."""
        self.closed = False

    async def ensure_ready(self) -> None:
        self.ready_calls += 1
        if self._not_ready is not None:
            raise self._not_ready

    async def respond(self, turn: TurnRequest) -> TurnOutcome:
        self.turns.append(turn)
        answer = self._answers.pop(0) if self._answers else self._default
        if isinstance(answer, BaseException):
            raise answer
        return answer

    async def close(self) -> None:
        self.closed = True
