from collections.abc import Sequence
from datetime import UTC
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from repomesh.modules.repository_intelligence.domain import AutoCard, RepositoryProfile
from repomesh.persistence import Database
from repomesh.persistence.models import AuditEventRecord, OutboxEventRecord, StateEventRecord
from repomesh.shared.domain import DomainError
from repomesh.shared.events import EventEnvelope

from .models import RepositoryRecord

#: Key under which the AutoCard is serialised inside ``metadata_payload``.
_METADATA_AUTO_CARD_KEY = "auto_card"


class RepositoryAlreadyExists(DomainError):
    pass


class PostgresRepositoryCatalog:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def add(
        self,
        profile: RepositoryProfile,
        *,
        events: Sequence[EventEnvelope] = (),
    ) -> None:
        try:
            async with self._database.transaction() as session:
                session.add(
                    RepositoryRecord(
                        id=profile.id,
                        name=profile.name,
                        url=profile.url,
                        description=profile.description,
                        topics=list(profile.topics),
                        languages=list(profile.languages),
                        profiled_at=profile.profiled_at,
                        metadata=_serialize_metadata(profile),
                    )
                )
                for event in events:
                    session.add(StateEventRecord.from_envelope(event))
                    session.add(AuditEventRecord.from_envelope(event))
                    session.add(OutboxEventRecord.from_envelope(event))
        except IntegrityError as exc:
            raise RepositoryAlreadyExists(f"Repository already registered: {profile.url}") from exc

    async def list(self) -> list[RepositoryProfile]:
        async with self._database.transaction() as session:
            records = (await session.scalars(select(RepositoryRecord))).all()
        return [self._to_domain(record) for record in records]

    async def get(self, repository_id: UUID) -> RepositoryProfile | None:
        async with self._database.transaction() as session:
            record = await session.get(RepositoryRecord, repository_id)
        return self._to_domain(record) if record else None

    @staticmethod
    def _to_domain(record: RepositoryRecord) -> RepositoryProfile:
        return RepositoryProfile(
            id=record.id,
            name=record.name,
            url=record.url,
            description=record.description,
            topics=tuple(record.topics),
            languages=tuple(record.languages),
            auto_card=_deserialize_auto_card(record.meta