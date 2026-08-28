"""Concrete sides of the Bridge's seams.

``repomesh_binding`` is the production adapter for ``WorkerBindingPort`` and
``matrix`` is the production adapter for ``RoomPort``. ``memory`` holds the
in-memory doubles *and* ``InertCodingSession``, which is not a double at all:
until a real coding CLI arrives behind a restricted process factory in PR 4, it
is what ``run`` genuinely assembles.

The room transport vocabulary is re-exported here, and it is imported from
``ports`` rather than from ``matrix`` because that is where it now lives: the
supervisor grades a refusal by which call produced it, and a core module cannot
import an adapter to get the type it branches on. What the re-export preserves
is the composition root's view — a refusal raised out of ``start`` is a startup
refusal and belongs to whoever wires ``run``; the same family raised out of
``sync``, ``join`` or ``send`` is a steady-state event the supervisor owns — and
that root reaches for these names next to the adapter it is wiring.

``DriverCodingSession`` is the production ``CodingSessionPort`` for a codex
enrollment (PR 4), and it is launched only through ``RestrictedProcessFactory``.
Both are exported here, alongside ``prepare_session_dirs`` and the
``IsolationReport`` its probe returns, so the composition root can build the one
containment boundary the driver and the session share.

The leader lane adds three names and no fourth stack. ``RepoMeshLeaderActionAdapter``
and ``InMemoryLeaderActionPort`` are the two implementations of ``LeaderActionPort``
— an HTTP one and one that really runs the phase machine, which is what makes it
a seam. ``LeaderCoordinationSession`` is not a second ``CodingSessionPort``: it
holds a ``DriverCodingSession`` and reads a structured decision out of the same
turn the room lane reads a note out of, so both lanes share one containment
boundary rather than each keeping its own.
"""

from ..ports import RoomRefused, RoomTransportError, RoomUnavailable
from .coding_session import DriverCodingSession
from .leader_actions import RepoMeshLeaderActionAdapter
from .leader_session import LeaderCoordinationSession
from .matrix import MatrixRoomAdapter
from .memory import InMemoryLeaderActionPort
from .restricted_process import (
    IsolationReport,
    RestrictedProcessFactory,
    prepare_session_dirs,
)

__all__ = [
    "DriverCodingSession",
    "InMemoryLeaderActionPort",
    "IsolationReport",
    "LeaderCoordinationSession",
    "MatrixRoomAdapter",
    "RepoMeshLeaderActionAdapter",
    "RestrictedProcessFactory",
    "RoomRefused",
    "RoomTransportError",
    "RoomUnavailable",
    "prepare_session_dirs",
]
