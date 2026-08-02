from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from repomesh.modules.agent_runtime.ports.agent_team import (
    AgentTeamControlPlane,
    AgentTeamMessenger,
)
from repomesh.modules.agent_runtime.ports.coding_agent import CodingAgent
from repomesh.modules.repository_intelligence.ports.catalog import RepositoryCatalog
from repomesh.persistence import Database
from repomesh.persistence.outbox import OutboxStore


class ReadinessProbe(Protocol):
    async def health(self) -> bool: ...


class AsyncCloseable(Protocol):
    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ApplicationContainer:
    """Process-level dependencies assembled outside business modules."""

    database: Database
    repository_catalog: RepositoryCatalog
    outbox_store: OutboxStore
    mock_coding_agent_factory: Callable[[str], CodingAgent]
    agent_team_control_plane: AgentTeamControlPlane | None = None
    agent_team_messenger: AgentTeamMessenger | None = None
    agentteams_probe: ReadinessProbe | None = None
    agentteams_required: bool = False
    external_resources: tuple[AsyncCloseable, ...] = ()

    async def is_agentteams_ready(self) -> bool:
        if not self.agentteams_required:
            return True
        return self.agentteams_probe is not None and await self.agentteams_probe.health()

    async def close(self) -> None:
        try:
            for resource in reversed(self.external_resources):
                await resource.close()
        finally:
            await self.database.dispose()
