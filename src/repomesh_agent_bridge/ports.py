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
from typing import Protocol

from .contracts import ExternalWorkerEnrollment, WorkerBinding

__all__ = ["CodingSessionPort", "RoomPort", "WorkerBindingPort"]


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
    """The Matrix side of the Bridge.

    PR 2 only starts and stops it: preflight must be strictly ahead of Matrix
    sync, so "was this started, and when" is exactly the fact PR 2 needs to be
    able to assert. Mention detection, cursors and sending land in PR 3, which
    is also where the Matrix credential is resolved — PR 2 resolves no secret it
    cannot use.
    """

    async def start(
        self, *, homeserver_url: str, user_id: str, room_ids: Sequence[str]
    ) -> None:
        """Begin syncing as ``user_id``, scoped to the confirmed rooms."""
        ...

    async def close(self) -> None:
        """Stop syncing. Must be safe on a port that was never started."""
        ...


class CodingSessionPort(Protocol):
    """The local coding CLI, as the Bridge sees it.

    In production this becomes a thin adapter over the Runner's
    ``ProtocolDriver.execute`` (ADR 0004 decision 4, plan decision 5); the Runner
    driver stack is consumed, never copied. PR 2 spawns nothing, so the only
    member is the one shutdown has to be able to call — the conversation surface
    arrives with PR 4, together with the restricted ``ProcessFactory`` that is
    allowed to launch a real CLI at all.
    """

    async def close(self) -> None:
        """Release the session. Must be safe when no session was ever opened."""
        ...
