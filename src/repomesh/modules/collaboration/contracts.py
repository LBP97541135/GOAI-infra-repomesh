from dataclasses import dataclass
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
