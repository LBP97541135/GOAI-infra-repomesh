from collections.abc import Sequence
from uuid import UUID

from repomesh.modules.agent_directory.domain import AgentAlreadyExists, AgentPrincipal
from repomesh.shared.events import EventEnvelope


class InMemoryAgentDirectory:
    def __init__(self) -> None:
        self._principals: dict[UUID, AgentPrincipal] = {}
        self._idempotency: dict[str, tuple[UUID, str]] = {}
        self._singletons: set[str] = set()
        self._resource_bindings: set[tuple[str, str]] = set()
        self.events: list[EventEnvelope] = []

    async def add(
        self,
        principal: AgentPrincipal,
        *,
        idempotency_key: str,
        request_fingerprint: str,
        events: Sequence[EventEnvelope] = (),
    ) -> None:
        if principal.id in self._principals or idempotency_key in self._idempotency:
            raise AgentAlreadyExists("agent principal already exists")
        if principal.singleton_key is not None and principal.singleton_key in self._singletons:
            raise AgentAlreadyExists("agent leader already exists for this scope")
        resource_kind = (
            "manager" if principal.role.value == "organization_leader" else "worker"
        )
        resource_binding = (resource_kind, principal.agentteams_resource_name)
        if resource_binding in self._resource_bindings:
            raise AgentAlreadyExists("agent principal already exists for AgentTeams resource")
        self._principals[principal.id] = principal
        if principal.singleton_key is not None:
            self._singletons.add(principal.singleton_key)
        self._resource_bindings.add(resource_binding)
        self._idempotency[idempotency_key] = (principal.id, request_fingerprint)
        self.events.extend(events)

    async def get(self, agent_id: UUID) -> AgentPrincipal | None:
        return self._principals.get(agent_id)

    async def get_view(self, agent_id: UUID):
        principal = await self.get(agent_id)
        return principal.to_view() if principal is not None else None

    async def list(self) -> tuple[AgentPrincipal, ...]:
        return tuple(sorted(self._principals.values(), key=lambda item: str(item.id)))

    async def list_views(self):
        return tuple(item.to_view() for item in await self.list())

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> tuple[AgentPrincipal, str] | None:
        binding = self._idempotency.get(idempotency_key)
        if binding is None:
            return None
        agent_id, fingerprint = binding
        return self._principals[agent_id], fingerprint
