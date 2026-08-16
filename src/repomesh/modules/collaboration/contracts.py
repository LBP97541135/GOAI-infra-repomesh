from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class CollaborationMessageKind(StrEnum):
    TASK_ASSIGNMENT = "task_assignment"
    TASK_REPORT = "task_report"
    QUESTION = "question"
    ANSWER = "answer"
    PROGRESS = "progress"
    DECISION = "decision"


class CollaborationDeliveryStatus(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SendCollaborationMessageCommand:
    organization_id: UUID
    project_id: UUID
    sender_agent_id: UUID
    recipient_agent_id: UUID
    kind: CollaborationMessageKind
    subject: str
    body: str
    repository_id: UUID | None = None
    task_id: UUID | None = None
    correlation_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class CollaborationMessageView:
    id: UUID
    organization_id: UUID
    project_id: UUID
    repository_id: UUID | None
    task_id: UUID | None
    sender_agent_id: UUID
    recipient_agent_id: UUID
    kind: CollaborationMessageKind
    subject: str
    body: str
    room_id: str
    status: CollaborationDeliveryStatus
    event_id: str | None
    correlation_id: UUID
    created_at: datetime


class CollaborationGateway(Protocol):
    async def send(
        self,
        command: SendCollaborationMessageCommand,
        *,
        idempotency_key: str,
    ) -> CollaborationMessageView: ...


@dataclass(frozen=True, slots=True)
class InboundMatrixMessage:
    event_id: str
    room_id: str
    sender: str
    body: str


@dataclass(frozen=True, slots=True)
class MatrixTaskReport:
    sender_agent_id: UUID
    project_id: UUID
    task_id: UUID
    status: str
    summary: str


class MatrixInboundResult(StrEnum):
    IGNORED = "ignored"
    PROCESSED = "processed"
    DUPLICATE = "duplicate"


class MatrixInboundProcessor(Protocol):
    async def execute(self, message: InboundMatrixMessage) -> MatrixInboundResult: ...


class CollaborationError(Exception):
    """Base of every refusal this module hands back to a caller.

    The hierarchy lives in ``contracts`` rather than ``domain`` because other
    modules' API layers have to map these onto status codes, and an API layer
    reaching into a foreign module's ``domain`` would be importing its
    internals to read its refusals. ``domain`` re-exports them, so nothing
    inside the module has to change where it imports from.
    """


class CollaborationDenied(CollaborationError):
    pass


class CollaborationConflict(CollaborationError):
    pass


class CollaborationRouteUnavailable(CollaborationError):
    """No usable Matrix room for this pair of agents — yet.

    Not a malformed request and not a server fault: the topology names a team
    whose AgentTeams room has not been provisioned, so the message has nowhere
    to go until the execution plane catches up. Callers translate it as
    retryable (503), never as a 500.
    """
