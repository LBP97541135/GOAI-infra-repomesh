"""Concrete sides of the Bridge's seams.

``repomesh_binding`` is the production adapter for ``WorkerBindingPort`` and
``matrix`` is the production adapter for ``RoomPort``. ``memory`` holds the
in-memory doubles *and* ``InertCodingSession``, which is not a double at all:
until a real coding CLI arrives behind a restricted process factory in PR 4, it
is what ``run`` genuinely assembles.

The Matrix failure vocabulary is re-exported here because it crosses a boundary
the rest of the adapter does not. A refusal raised out of ``start`` is a startup
refusal and belongs to whoever wires ``run``; the same family raised out of
``sync``, ``join`` or ``send`` is a steady-state event and belongs to the
supervisor's backoff. The composition root has to be able to name it to tell the
two apart.
"""

from .matrix import MatrixRoomAdapter, RoomRefused, RoomTransportError, RoomUnavailable

__all__ = ["MatrixRoomAdapter", "RoomRefused", "RoomTransportError", "RoomUnavailable"]
