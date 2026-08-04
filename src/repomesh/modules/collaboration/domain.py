import json
import re
from dataclasses import dataclass, field, replace
from uuid import UUID

from repomesh.modules.collaboration.contracts import (
    CollaborationDeliveryStatus,
    CollaborationMessageKind,
    CollaborationMessageView,
    MatrixTaskReport,
)
from repomesh.shared.domain import new_id


class CollaborationError(Exception):
    pass


class CollaborationDenied(CollaborationError):
    pass


class CollaborationConflict(CollaborationError):
    pass


class CollaborationRouteUnavailable(CollaborationError):
    pass


_JSON_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


def parse_matrix_task_report(body: str) -> MatrixTaskReport | None:
    raw = body.strip()
    if match := _JSON_FENCE.match(raw):
        raw = match.group(1).strip()
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != "repomesh.agent-report.v1":
        return None
    try:
        sender_agent_id = UUID(str(payload["sender_agent_id"]))
        project_id = UUID(str(payload["project_id"]))
        task_id = UUID(str(payload["task_id"]))
    except (KeyError, TypeError, ValueError):
        raise CollaborationDenied("invalid task report identity") from None
    status = payload.get("status")
    summary = payload.get("summary")
    if status not in {"blocked", "succeeded", "failed"}:
        raise CollaborationDenied("invalid task report status")
    if not isinstance(summary, str) or not summary.strip():
        raise CollaborationDenied("invalid task report summary")
    return MatrixTaskReport(
        sender_agent_id, project_id, task_id, status, summary.strip()
    )


@dataclass(frozen=True, slots=True)
class CollaborationMessage:
    organization_id: UUID
    project_id: UUID
    sender_agent_id: UUID
    recipient_agent_id: UUID
    kind: CollaborationMessageKind
    subject: str
    body: str
    room_id: str
    repository_id: UUID | None = None
    task_id: UUID | None = None
    correlation_id: UUID = field(default_factory=new_id)
    id: UUID = field(default_factory=new_id)
    status: CollaborationDeliveryStatus = CollaborationDeliveryStatus.PENDING
    event_id: str | None = None

    def __post_init__(self) -> None:
        if not self.subject.strip() or not self.body.strip() or not self.room_id.strip():
            raise ValueError("subject, body and room_id are required")

    def delivered(self, event_id: str) -> "CollaborationMessage":
        return replace(
            self,
            status=CollaborationDeliveryStatus.DELIVERED,
            event_id=event_id,
        )

    def failed(self) -> "CollaborationMessage":
        return replace(self, status=CollaborationDeliveryStatus.FAILED)

    def to_view(self) -> CollaborationMessageView:
        return CollaborationMessageView(
            id=self.id,
            organization_id=self.organization_id,
            project_id=self.project_id,
            repository_id=self.repository_id,
            task_id=self.task_id,
            sender_agent_id=self.sender_agent_id,
            recipient_agent_id=self.recipient_agent_id,
            kind=self.kind,
            subject=self.subject,
            body=self.body,
            room_id=self.room_id,
            status=self.status,
            event_id=self.event_id,
            correlation_id=self.correlation_id,
        )
