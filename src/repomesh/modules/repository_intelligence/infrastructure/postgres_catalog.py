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
                        test_commands=list(profile.test_commands),
                        test_paths=list(profile.test_paths),
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

    async def update_verification(
        self,
        repository_id: UUID,
        *,
        test_commands: tuple[str, ...],
        test_paths: tuple[str, ...],
    ) -> RepositoryProfile | None:
        """Replace an operator-owned verification profile idempotently.

        The endpoint using this method is safe to retry: it assigns the complete
        command/path pair rather than appending to either collection.
        """

        async with self._database.transaction() as session:
            record = await session.get(RepositoryRecord, repository_id)
            if record is None:
                return None
            record.test_commands = list(test_commands)
            record.test_paths = list(test_paths)
            await session.flush()
            return self._to_domain(record)

    @staticmethod
    def _to_domain(record: RepositoryRecord) -> RepositoryProfile:
        return RepositoryProfile(
            id=record.id,
            name=record.name,
            url=record.url,
            description=record.description,
            topics=tuple(record.topics),
            languages=tuple(record.languages),
            # Rows written before defect A-19 have no verification commands;
            # NULL and [] both mean "nobody has said how to test this".
            test_commands=tuple(record.test_commands or ()),
            # Rows written before defect A-21 say nothing about where tests live.
            test_paths=tuple(record.test_paths or ()),
            auto_card=_deserialize_auto_card(record.metadata_payload),
            profiled_at=_as_utc(record.profiled_at),
        )


def _serialize_metadata(profile: RepositoryProfile) -> dict[str, Any]:
    if profile.auto_card is None:
        return {}
    card = profile.auto_card
    return {
        _METADATA_AUTO_CARD_KEY: {
            "top_dirs": list(card.top_dirs),
            "deps": list(card.deps),
            "recent_commits": list(card.recent_commits),
            "exposed_apis": list(card.exposed_apis),
            "low_signal": card.low_signal,
        }
    }


def _deserialize_auto_card(metadata: dict[str, Any] | None) -> AutoCard | None:
    payload = (metadata or {}).get(_METADATA_AUTO_CARD_KEY)
    if not isinstance(payload, dict):
        return None
    return AutoCard(
        top_dirs=tuple(payload.get("top_dirs") or ()),
        deps=tuple(payload.get("deps") or ()),
        recent_commits=tuple(payload.get("recent_commits") or ()),
        exposed_apis=tuple(payload.get("exposed_apis") or ()),
        low_signal=bool(payload.get("low_signal", False)),
    )


def _as_utc(value):  # noqa: ANN001, ANN202
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value
