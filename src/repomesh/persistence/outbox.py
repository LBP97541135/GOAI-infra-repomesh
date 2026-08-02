from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update

from repomesh.persistence.models import OutboxEventRecord

from .database import Database


@dataclass(frozen=True, slots=True)
class OutboxMessage:
    event_id: UUID
    event_type: str
    schema_version: int
    occurred_at: datetime
    correlation_id: UUID
    payload: dict[str, Any]


class OutboxStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def pending(self, limit: int = 100) -> list[OutboxMessage]:
        statement = (
            select(OutboxEventRecord)
            .where(OutboxEventRecord.published_at.is_(None))
            .order_by(OutboxEventRecord.recorded_at, OutboxEventRecord.event_id)
            .limit(limit)
        )
        async with self._database.transaction() as session:
            records = (await session.scalars(statement)).all()
        return [
            OutboxMessage(
                event_id=record.event_id,
                event_type=record.event_type,
                schema_version=record.schema_version,
                occurred_at=record.occurred_at,
                correlation_id=record.correlation_id,
                payload=record.payload,
            )
            for record in records
        ]

    async def mark_published(self, event_id: UUID, published_at: datetime) -> bool:
        statement = (
            update(OutboxEventRecord)
            .where(
                OutboxEventRecord.event_id == event_id,
                OutboxEventRecord.published_at.is_(None),
            )
            .values(published_at=published_at)
        )
        async with self._database.transaction() as session:
            result = await session.execute(statement)
        return bool(result.rowcount)

    async def pending_count(self) -> int:
        statement = (
            select(func.count())
            .select_from(OutboxEventRecord)
            .where(OutboxEventRecord.published_at.is_(None))
        )
        async with self._database.transaction() as session:
            return int(await session.scalar(statement) or 0)
