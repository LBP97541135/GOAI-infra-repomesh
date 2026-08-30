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
    occurred_at: datetime
    """Matrix ``origin_server_ts``, as the homeserver stamped it.

    Required rather than defaulted: the room timeline is ordered by when the
    event happened in the room, and a default would let a caller that never
    read the field record every message as having happened at the moment the
    poller happened to run. A batch that arrives late, or is replayed after a
    crash, would then sort after messages that really came later.
    """


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


@dataclass(frozen=True, slots=True)
class AuthorizedRoom:
    """A room this deployment is allowed to ingest, and what it belongs to.

    The whitelist is the topology: a room id is authorized exactly when some
    repository team names it as its team room or its leader DM. Carrying the
    two owning ids with it means the recorder never has to ask a second
    question to attribute what it stores, and a room nobody's topology claims
    resolves to ``None`` — which is the whole of the "do not mirror the
    homeserver" rule.
    """

    room_id: str
    project_id: UUID
    repository_id: UUID


@dataclass(frozen=True, slots=True)
class RecordRoomTimelineCommand:
    """One ``m.room.message`` as the homeserver handed it over."""

    event_id: str
    room_id: str
    sender_matrix_user_id: str
    body: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class RoomTimelineEntryView:
    """A recorded room message, plus what we could resolve about its sender.

    ``sender_agent_id`` is nullable on purpose (adjudication D-4): a Matrix
    user we cannot map onto a registered principal is stored with its raw
    ``sender_matrix_user_id`` and no agent id. An honest unknown beats a
    guessed identity — AC-06 forbids showing a message under the wrong name,
    not showing one whose sender has no RepoMesh name.
    """

    event_id: str
    room_id: str
    project_id: UUID
    repository_id: UUID
    sender_matrix_user_id: str
    sender_agent_id: UUID | None
    body: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class RoomTimelineCursor:
    """Where a page of the timeline resumes.

    Both halves of the sort key, because ``occurred_at`` alone is not unique:
    a homeserver stamps events at millisecond resolution and two messages can
    share a timestamp. Resuming on the timestamp alone would either repeat the
    tie or skip half of it.
    """

    occurred_at: datetime
    event_id: str


class RoomTimelineIngest(Protocol):
    """The write half: record one room message, keyed by its Matrix event id.

    The event id *is* the idempotency key — it is globally unique, the
    homeserver assigns it, and it is the only key a replayed sync batch can
    present — so it travels inside the command rather than beside it.

    Returns ``None`` when the room is not one this deployment ingests. A
    dropped message and a stored one are different outcomes and the caller
    must be able to tell them apart, so this is not an exception: a message in
    somebody else's room is not an error, it is simply not ours.
    """

    async def record(
        self, command: RecordRoomTimelineCommand
    ) -> RoomTimelineEntryView | None: ...


class RoomTimelineQuery(Protocol):
    """The read half, for the console's room stream.

    Separate from :class:`RoomTimelineIngest` because the two callers are
    different processes' worth of concern: the poller only writes and the read
    model only reads, and neither should be handed the other's verb.
    """

    async def list_room(
        self,
        room_id: str,
        *,
        after: RoomTimelineCursor | None = None,
        limit: int = 100,
    ) -> tuple[RoomTimelineEntryView, ...]: ...


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


class CollaborationDeliveryDeferred(CollaborationRouteUnavailable):
    """The message is persisted as failed and may safely be retried later.

    This is narrower than :class:`CollaborationRouteUnavailable`: that base
    can also be raised before a message has a route or a durable row. This
    subtype is emitted only after ``SendCollaborationMessage`` has stored the
    message and marked its delivery failed, so a caller that already committed
    its own business fact may accept that fact without losing the notification.
    """

    def __init__(self, message_id: UUID, reason: str) -> None:
        super().__init__(reason)
        self.message_id = message_id
