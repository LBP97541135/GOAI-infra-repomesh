"""Projecting a project's topology into the agent runtime, as §8 needs it.

Contract v0.4 §8. ``ensure topology`` writes rows: principals in the directory,
a ``ProjectAgentTopology`` with one team per repository. None of those rows is
a place anyone can talk. The Matrix rooms a round actually dispatches over are
created by the AgentTeams controller, and until something asks the controller
for them ``room_id`` stays NULL and every dispatch raises
``CollaborationRouteUnavailable`` — the whole of defect B-11.

Declared here rather than imported for the same reason
:mod:`.materialization` is: the capability lives outside this module (the
AgentTeams integration composes it out of ``RegisterNativeAgent`` and
``ReconcileProjectAgentTopology``), and naming it directly would drag an
integration into a business module's application layer. The composition root
hands over something that satisfies this protocol structurally.

The step is synchronous and sits *before* the plan is started, not after: a
round whose rooms appear a moment later than its tasks is a round whose first
dispatch fails, and the operator has no button for "try the dispatch again".
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID


class RuntimeProjectionUnavailable(RuntimeError):
    """The topology could not be given a runtime → 503.

    A published refusal in the same family as ``ExecutionPlaneUnavailable``:
    the request is well formed, the server is not broken, the runtime has not
    caught up — press the button again. It is raised *before* the plan is
    started, so a round that hits it is still materialisable once the
    controller answers.
    """


class TopologyRuntimeProjector(Protocol):
    """Make the rooms a project's teams will be dispatched over exist.

    ``project`` is ensure-shaped end to end, and has to be: materialize is
    re-entrant, so this runs again on every retry of a half-executed round. An
    agent already registered with the controller, a team that already has its
    rooms, a topology that was already reconciled — all three are success, not
    conflict.

    Raises :class:`RuntimeProjectionUnavailable` when the controller cannot be
    reached, refuses, or answers without the rooms the round needs.
    """

    async def project(self, project_id: UUID) -> None: ...


__all__ = ["RuntimeProjectionUnavailable", "TopologyRuntimeProjector"]
