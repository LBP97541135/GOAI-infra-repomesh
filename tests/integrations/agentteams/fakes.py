"""Doubles shared by the external-worker tests.

One home for the directory double, because the two suites that need it are
testing the *same* pair of use cases from two sides — the endpoint tests
mount it behind an HTTP router, the projection tests drive it directly — and
two spellings of "a directory holding these principals" drift into two
different meanings of "unknown agent" the moment one of them grows a case.

Only the directory lives here. The control-plane doubles are deliberately
per-file: each answers a different question (a whole project's registration,
one external worker, a controller that already holds everything) and folding
them together would produce one double with a flag per test.
"""

from __future__ import annotations

from uuid import UUID

from repomesh.modules.agent_directory.contracts import AgentPrincipalView


class StubDirectory:
    """An ``AgentPrincipalReader`` holding exactly the principals it is given.

    Constructed from views rather than through ``CreateAgent`` on purpose:
    ``InMemoryAgentDirectory`` only builds *active* principals through the
    production path and has no way to retire one, so the disabled and unknown
    cases need a directory whose rows were never created.
    """

    def __init__(self, *principals: AgentPrincipalView) -> None:
        self._principals = {principal.id: principal for principal in principals}

    async def get_view(self, agent_id: UUID) -> AgentPrincipalView | None:
        return self._principals.get(agent_id)

    async def list_views(self) -> tuple[AgentPrincipalView, ...]:
        return tuple(self._principals.values())
