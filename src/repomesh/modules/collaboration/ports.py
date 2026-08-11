from typing import Protocol
from uuid import UUID

from repomesh.modules.agent_directory.contracts import AgentPrincipalView
from repomesh.modules.collaboration.domain import CollaborationMessage


class CollaborationMessageStore(Protocol):
    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> tuple[CollaborationMessage, str] | None: ...

    async def add(
        self,
        message: CollaborationMessage,
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> None: ...

    async def update(self, message: CollaborationMessage) -> None: ...

    async def list_failed(
        self, limit: int = 100
    ) -> tuple[tuple[CollaborationMessage, str], ...]: ...


class CollaborationMessenger(Protocol):
    async def send_task(
        self,
        room_id: str,
        body: str,
        *,
        transaction_id: str,
        recipient_resource_name: str | None = None,
    ) -> str: ...


class MatrixIdentityVerifier(Protocol):
    async def verify(self, profile: AgentPrincipalView, matrix_user_id: str) -> bool: ...


class ProcessedMatrixEventStore(Protocol):
    async def contains(self, event_id: str) -> bool: ...

    async def add(
        self, event_id: str, *, project_id: UUID, task_id: UUID, sender_agent_id: UUID
    ) -> None: ...
