"""Are this plan's external members running, as §8 has to ask before it starts.

ADR 0004 / AC-03. A member provisioned ``containerManaged: false`` has no body
inside the cluster — its CLI runs on an operator's own machine — so every
durable fact RepoMesh holds about it (it exists, it is bound, it owns these
rooms) is silent on the only question a round about to hand out work has: *is
it running right now*. Nothing in this module can answer that, and a round that
assumes it can is the false green this port exists to prevent: the tasks are
written, the dispatch lands in a room nobody is reading, and the console has no
button for "deliver that again".

Declared here rather than imported, for :mod:`.materialization`'s reason. The
capability belongs to ``agent_runtime`` — it joins the AgentTeams control
plane's ``containerManaged`` flag to a readiness lease — and naming it would
point a business module at another module's application layer. The composition
root hands over something that satisfies these protocols structurally, the
*fact* included, so the module that produces one does not have to import the
module that reads it.

Two shapes of honesty in the answer.

*A fact per member, not a boolean.* "Not ready" gives an operator nothing to
start. Which member, in which role, and why RepoMesh believes it is absent are
the whole of the next action, and they survive into the refusal verbatim.

*Managed members are absent from the answer, not reported ready.* Their bodies
are containers the AgentTeams controller owns and restarts, so their liveness
is the controller's to know and this port never claims it (AC-04). An
all-managed deployment therefore gets an empty tuple and passes without a
special case anywhere.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID


class MemberReadinessFact(Protocol):
    """One external member, as the gate reports it.

    ``role`` and ``status`` are plain strings on purpose. Their vocabularies
    (``worker``/``repository_leader``, ``ready``/``stale``/``offline``) are
    ``agent_runtime``'s enums, and this module has no business importing them
    to read four fields it passes straight through to a refusal body.
    """

    @property
    def agent_id(self) -> UUID: ...

    @property
    def role(self) -> str: ...

    @property
    def status(self) -> str: ...

    @property
    def reason(self) -> str: ...


class ExternalMemberReadinessGate(Protocol):
    """Ask about a set of members; hear back about the external ones.

    Not one fact per argument: members the controller runs are omitted, so the
    caller's rule is the same either way — every fact that comes back and does
    not say ``ready`` is a member that cannot be given work.

    Raises ``WorkerControlPlaneUnavailable`` when the AgentTeams control plane
    is not configured and the member set is not empty. Without it there is no
    way to tell an external member from a managed one, and a gate that cannot
    see is a gate that must not open.
    """

    async def check(self, member_ids: Sequence[UUID]) -> tuple[MemberReadinessFact, ...]: ...


__all__ = ["ExternalMemberReadinessGate", "MemberReadinessFact"]
