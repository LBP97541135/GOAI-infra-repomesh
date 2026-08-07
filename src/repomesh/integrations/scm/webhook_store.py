from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import DateTime, String, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from repomesh.persistence import Database
from repomesh.persistence.base import Base

from .contracts import SCMConflict


class SCMWebhookEventStore(Protocol):
    async def begin(self, delivery_id: str, payload_hash: str) -> bool: ...

    async def complete(self, delivery_id: str) -> None: ...

    async def release(self, delivery_id: str) -> None: ...


class SCMWebhookEventRecord(Base):
    __tablename__ = "scm_webhook_events"
    __table_args__ = {"schema": "delivery"}

    delivery_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InMemorySCMWebhookEventStore:
    def __init__(self) -> None:
        self._items: dict[str, tuple[str, str]] = {}

    async def begin(self, delivery_id: str, payload_hash: str) -> bool:
        existing = self._items.get(delivery_id)
        if existing:
            if existing[0] != payload_hash:
                raise SCMConflict("webhook delivery ID was reused with another payload")
            return False
        self._items[delivery_id] = (payload_hash, "processing")
        return True

    async def complete(self, delivery_id: str) -> None:
        payload_hash, _ = self._items[delivery_id]
        self._items[delivery_id] = (payload_hash, "completed")

    async def release(self, delivery_id: str) -> None:
        self._items.pop(delivery_id, None)


class PostgresSCMWebhookEventStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def begin(self, delivery_id: str, payload_hash: str) -> bool:
        now = datetime.now(UTC)
        try:
            async with self._database.transaction() as session:
                session.add(
                    SCMWebhookEventRecord(
                        delivery_id=delivery_id,
                        payload_hash=payload_hash,
                        status="processing",
                        received_at=now,
                        completed_at=None,
                    )
                )
            return True
        except IntegrityError as error:
            async with self._database.transaction() as session:
                existing = await session.scalar(
                    select(SCMWebhookEventRecord).where(
                        SCMWebhookEventRecord.delivery_id == delivery_id
                    )
                )
            if existing is None or existing.payload_hash != payload_hash:
                raise SCMConflict(
                    "webhook delivery ID was reused with another payload"
                ) from error
            return False

    async def complete(self, delivery_id: str) -> None:
        async with self._database.transaction() as session:
            record = await session.get(SCMWebhookEventRecord, delivery_id)
            if record is None:
                raise SCMConflict("webhook event was not claimed")
            record.status = "completed"
            record.completed_at = datetime.now(UTC)

    async def release(self, delivery_id: str) -> None:
        async with self._database.transaction() as session:
            await session.execute(
                delete(SCMWebhookEventRecord).where(
                    SCMWebhookEventRecord.delivery_id == delivery_id,
                    SCMWebhookEventRecord.status == "processing",
                )
            )
