from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from repomesh.modules.agent_directory.domain import AgentPrincipal
from repomesh.shared.events import EventEnvelope


class AgentDirectory(Protocol):
    async def add(
        self,
        principal: AgentPrincipal,
        *,
        idempotency_key: str,
        request_fingerprint: str,
        events: Sequence[EventEnvelope] = (),
    ) -> None: ...

    async def get(self, agent_id: UUID) -> AgentPrincipal | None: ...

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> tuple[AgentPrincipal, str] | None: ...
