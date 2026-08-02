from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from repomesh.shared.domain import new_id


class ActorType(StrEnum):
    HUMAN = "human"
    AGENT = "agent"
    SERVICE = "service"


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    event_type: str
    actor_type: ActorType
    actor_id: str
    aggregate_type: str
    aggregate_id: UUID
    aggregate_version: int
    payload: dict[str, Any]
    correlation_id: UUID
    schema_version: int = 1
    event_id: UUID = field(default_factory=new_id)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    organization_id: UUID | None = None
    project_id: UUID | None = None
    workstream_id: UUID | None = None
    task_id: UUID | None = None
    run_id: UUID | None = None
    causation_id: UUID | None = None
