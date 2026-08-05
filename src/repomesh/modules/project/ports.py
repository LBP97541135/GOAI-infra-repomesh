from typing import Protocol
from uuid import UUID

from repomesh.modules.project.domain import ProjectAgentTopology


class ProjectTopologyStore(Protocol):
    async def add(
        self,
        topology: ProjectAgentTopology,
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> None: ...

    async def get(self, project_id: UUID) -> ProjectAgentTopology | None: ...

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> tuple[ProjectAgentTopology, str] | None: ...

    async def save(self, topology: ProjectAgentTopology) -> None: ...
