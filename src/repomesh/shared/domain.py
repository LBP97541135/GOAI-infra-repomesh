from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4


def new_id() -> UUID:
    return uuid4()


@dataclass(frozen=True, slots=True)
class DomainEvent:
    name: str
    aggregate_id: UUID
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: UUID = field(default_factory=new_id)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class DomainError(Exception):
    """Expected domain failure that can be presented to an API consumer."""

