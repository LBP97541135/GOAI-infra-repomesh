"""In-memory and inert sides of the three seams.

They ship with the package rather than living in the test tree for two reasons.
The merge gate asks every new HTTP adapter to have an in-memory counterpart, and
PR 2's ``run`` genuinely runs on these: Matrix arrives in PR 3 and the coding CLI
in PR 4, so until then the honest production stand-in for both is something that
does nothing and says so.

None of these keeps durable state. The Bridge's local state (cursor, inbox,
session refs) is PR 3's SQLite, which is its own test stand-in and never a port.
"""

import asyncio
from collections.abc import Sequence

from ..contracts import ExternalWorkerEnrollment, WorkerBinding

__all__ = ["InertCodingSession", "InertRoomPort", "InMemoryWorkerBindingPort"]


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


class InertRoomPort:
    """A room port that joins nothing.

    ``ready`` is the local readiness signal this tier has: the contract's
    liveness section promises a *local* health probe and nothing remote, and
    "the room side has been started" is exactly what that probe would report.
    """

    def __init__(self) -> None:
        self.started_rooms: tuple[str, ...] = ()
        self.user_id: str | None = None
        self.homeserver_url: str | None = None
        self.closed = False
        self.ready = asyncio.Event()

    async def start(
        self, *, homeserver_url: str, user_id: str, room_ids: Sequence[str]
    ) -> None:
        self.homeserver_url = homeserver_url
        self.user_id = user_id
        self.started_rooms = tuple(room_ids)
        self.ready.set()

    async def close(self) -> None:
        self.closed = True
        self.ready.clear()


class InertCodingSession:
    """A coding session that never starts a process.

    PR 2 spawns nothing at all, so ``close`` is called on a session that was
    never opened — which is the behaviour every implementation of this port owes
    the shutdown path anyway.
    """

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True
