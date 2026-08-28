from typing import Protocol
from uuid import UUID

from repomesh.modules.agent_directory.contracts import AgentPrincipalView
from repomesh.modules.collaboration.contracts import (
    AuthorizedRoom,
    RoomTimelineCursor,
    RoomTimelineEntryView,
)
from repomesh.modules.collaboration.domain import CollaborationMessage
from repomesh.shared.events import EventEnvelope


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


class MatrixIdentityResolver(Protocol):
    """Matrix user id -> the principal behind it, or None if nobody is.

    Deliberately *not* the same port as :class:`MatrixIdentityVerifier`, and
    deliberately not implemented by widening it. The verifier answers "is this
    principal's Matrix identity the one that sent this?" — a yes/no about an
    identity the caller already has, on the path that decides whether to trust
    a report. This answers "who, if anyone, is this Matrix user?" on the path
    that only records what a room said. Overloading one port with both would
    put a resolver's guess where a verifier's proof is required, which is
    exactly the confusion AC-06 is about.
    """

    async def resolve(self, matrix_user_id: str) -> UUID | None: ...


class AuthorizedRoomReader(Protocol):
    """The ingest whitelist, read from the topology, one room at a time."""

    async def authorized_room(self, room_id: str) -> AuthorizedRoom | None: ...


class RoomTimelineStore(Protocol):
    async def get(self, event_id: str) -> RoomTimelineEntryView | None: ...

    async def add(self, entry: RoomTimelineEntryView) -> RoomTimelineEntryView:
        """Store *entry*, or return the one already stored under its event id.

        Returns the winner rather than a bool: two pollers racing on the same
        event must both end up describing the row that exists, and only the
        store can see which write won.
        """
        ...

    async def list_room(
        self,
        room_id: str,
        *,
        after: RoomTimelineCursor | None = None,
        limit: int = 100,
    ) -> tuple[RoomTimelineEntryView, ...]: ...


class ProcessedMatrixEventStore(Protocol):
    async def contains(self, event_id: str) -> bool: ...

    async def add(
        self, event_id: str, *, project_id: UUID, task_id: UUID, sender_agent_id: UUID
    ) -> None: ...


class CollaborationAuditLedger(Protocol):
    """Where this module records a decision an operator may have to review.

    One method, one envelope: the ledger does not interpret what it is handed,
    so a refusal recorded here reads back exactly as it was written.
    """

    async def record(self, event: EventEnvelope) -> None: ...
