from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.persistence import Database
from repomesh.persistence.base import Base
from repomesh.persistence.models import AuditEventRecord
from repomesh.shared.events import EventEnvelope

from .contracts import (
    ChangeSetStatus,
    DeliveryArchiveView,
    GovernanceDecisionKind,
    RecoveryActionKind,
    RecoveryActionStatus,
    RecoveryTrigger,
    RepositoryDeliveryStatus,
    ReviewState,
    SCMCommandKind,
    SCMCommandStatus,
    SCMObservationSource,
    SCMObservationStatus,
)
from .domain import (
    CandidateRevision,
    ChangeSet,
    CICheckObservation,
    DeliveryConflict,
    GovernanceDecision,
    RecoveryAction,
    RecoveryPlan,
    RepositoryDelivery,
    ReviewObservation,
    SCMCommand,
    SCMObservation,
    SCMPollCursor,
)

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class ChangeSetRecord(Base):
    __tablename__ = "change_sets"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_change_sets_idempotency"),
        {"schema": "delivery"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    version: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    fingerprint: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, object]] = mapped_column(JSON_DOCUMENT)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ChangeSetRepositoryRecord(Base):
    __tablename__ = "change_set_repositories"
    __table_args__ = (
        UniqueConstraint(
            "change_set_id",
            "repository_id",
            name="uq_change_set_repositories_candidate",
        ),
        {"schema": "delivery"},
    )

    change_set_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("delivery.change_sets.id", ondelete="CASCADE"),
        primary_key=True,
    )
    repository_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    head_sha: Mapped[str] = mapped_column(String(40), index=True)
    pull_request_number: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40), index=True)


class DeliveryArchiveRecord(Base):
    __tablename__ = "delivery_archives"
    __table_args__ = ({"schema": "delivery"},)

    delivery_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    archived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InMemoryDeliveryArchiveStore:
    def __init__(self) -> None:
        self.items: dict[UUID, DeliveryArchiveView] = {}

    async def add(self, archive: DeliveryArchiveView) -> None:
        if archive.delivery_id in self.items:
            raise DeliveryConflict("delivery is already archived")
        self.items[archive.delivery_id] = archive

    async def get(self, delivery_id: UUID) -> DeliveryArchiveView | None:
        return self.items.get(delivery_id)

    async def list_ids(self) -> tuple[UUID, ...]:
        return tuple(self.items)


class PostgresDeliveryArchiveStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def add(self, archive: DeliveryArchiveView) -> None:
        try:
            async with self._database.transaction() as session:
                session.add(
                    DeliveryArchiveRecord(
                        delivery_id=archive.delivery_id,
                        archived_at=archive.archived_at,
                    )
                )
        except IntegrityError as error:
            raise DeliveryConflict("delivery is already archived") from error

    async def get(self, delivery_id: UUID) -> DeliveryArchiveView | None:
        async with self._database.transaction() as session:
            record = await session.get(DeliveryArchiveRecord, delivery_id)
        if record is None:
            return None
        return DeliveryArchiveView(
            delivery_id=record.delivery_id,
            archived_at=_aware(record.archived_at),
        )

    async def list_ids(self) -> tuple[UUID, ...]:
        async with self._database.transaction() as session:
            ids = (
                await session.scalars(select(DeliveryArchiveRecord.delivery_id))
            ).all()
        return tuple(ids)


class InMemoryDeliveryAuditLog:
    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    async def append(self, event: EventEnvelope) -> None:
        self.events.append(event)


class PostgresDeliveryAuditLog:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def append(self, event: EventEnvelope) -> None:
        async with self._database.transaction() as session:
            session.add(AuditEventRecord.from_envelope(event))


class SCMObservationRecord(Base):
    __tablename__ = "scm_observations"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "source",
            "external_id",
            name="uq_scm_observations_external_fact",
        ),
        {"schema": "delivery"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSON_DOCUMENT)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    change_set_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    repository_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(2000))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SCMPollCursorRecord(Base):
    __tablename__ = "scm_poll_cursors"
    __table_args__ = ({"schema": "delivery"},)

    change_set_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("delivery.change_sets.id", ondelete="CASCADE"),
        primary_key=True,
    )
    repository_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_poll_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_error: Mapped[str | None] = mapped_column(String(2000))
    version: Mapped[int] = mapped_column(Integer, nullable=False)


class SCMCommandRecord(Base):
    __tablename__ = "scm_commands"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_scm_commands_idempotency"),
        {"schema": "delivery"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    change_set_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("delivery.change_sets.id", ondelete="CASCADE"),
        index=True,
    )
    repository_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    kind: Mapped[str] = mapped_column(String(50), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict[str, object]] = mapped_column(JSON_DOCUMENT)
    status: Mapped[str] = mapped_column(String(20), index=True)
    attempts: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InMemorySCMCommandStore:
    def __init__(self) -> None:
        self.items: dict[UUID, SCMCommand] = {}
        self.keys: dict[str, UUID] = {}

    async def add(self, command: SCMCommand) -> None:
        if command.idempotency_key in self.keys:
            raise DeliveryConflict("duplicate SCM command")
        self.items[command.id] = command
        self.keys[command.idempotency_key] = command.id

    async def get(self, command_id: UUID) -> SCMCommand | None:
        return self.items.get(command_id)

    async def get_by_idempotency_key(self, key: str) -> SCMCommand | None:
        command_id = self.keys.get(key)
        return self.items.get(command_id) if command_id else None

    async def update(self, command: SCMCommand, *, expected_version: int) -> None:
        current = self.items.get(command.id)
        if current is None or current.version != expected_version:
            raise DeliveryConflict("SCM command version changed")
        self.items[command.id] = command

    async def list_dispatchable(
        self, *, stale_before: datetime, max_attempts: int, limit: int
    ) -> tuple[SCMCommand, ...]:
        values = (
            item
            for item in self.items.values()
            if item.attempts < max_attempts
            and (
                item.status in {SCMCommandStatus.PENDING, SCMCommandStatus.FAILED}
                or (
                    item.status is SCMCommandStatus.PROCESSING
                    and item.claimed_at is not None
                    and item.claimed_at <= stale_before
                )
            )
        )
        return tuple(sorted(values, key=lambda item: item.created_at)[:limit])


class PostgresSCMCommandStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def add(self, command: SCMCommand) -> None:
        try:
            async with self._database.transaction() as session:
                session.add(self._record(command))
        except IntegrityError as error:
            raise DeliveryConflict("duplicate SCM command") from error

    async def get(self, command_id: UUID) -> SCMCommand | None:
        async with self._database.transaction() as session:
            record = await session.get(SCMCommandRecord, command_id)
        return self._hydrate(record) if record else None

    async def get_by_idempotency_key(self, key: str) -> SCMCommand | None:
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(SCMCommandRecord).where(SCMCommandRecord.idempotency_key == key)
            )
        return self._hydrate(record) if record else None

    async def update(self, command: SCMCommand, *, expected_version: int) -> None:
        async with self._database.transaction() as session:
            record = await session.get(SCMCommandRecord, command.id)
            if record is None or record.version != expected_version:
                raise DeliveryConflict("SCM command version changed")
            record.status = command.status.value
            record.attempts = command.attempts
            record.version = command.version
            record.last_error = command.last_error
            record.claimed_at = command.claimed_at
            record.completed_at = command.completed_at

    async def list_dispatchable(
        self, *, stale_before: datetime, max_attempts: int, limit: int
    ) -> tuple[SCMCommand, ...]:
        async with self._database.transaction() as session:
            records = (
                await session.scalars(
                    select(SCMCommandRecord)
                    .where(
                        SCMCommandRecord.attempts < max_attempts,
                        or_(
                            SCMCommandRecord.status.in_(
                                (SCMCommandStatus.PENDING.value, SCMCommandStatus.FAILED.value)
                            ),
                            (
                                (SCMCommandRecord.status == SCMCommandStatus.PROCESSING.value)
                                & (SCMCommandRecord.claimed_at <= stale_before)
                            ),
                        ),
                    )
                    .order_by(SCMCommandRecord.created_at)
                    .limit(limit)
                )
            ).all()
        return tuple(self._hydrate(record) for record in records)

    @staticmethod
    def _record(command: SCMCommand) -> SCMCommandRecord:
        return SCMCommandRecord(
            id=command.id,
            change_set_id=command.change_set_id,
            repository_id=command.repository_id,
            kind=command.kind.value,
            idempotency_key=command.idempotency_key,
            payload=command.payload,
            status=command.status.value,
            attempts=command.attempts,
            version=command.version,
            last_error=command.last_error,
            created_at=command.created_at,
            claimed_at=command.claimed_at,
            completed_at=command.completed_at,
        )

    @staticmethod
    def _hydrate(record: SCMCommandRecord) -> SCMCommand:
        return SCMCommand(
            id=record.id,
            change_set_id=record.change_set_id,
            repository_id=record.repository_id,
            kind=SCMCommandKind(record.kind),
            idempotency_key=record.idempotency_key,
            payload=record.payload,
            status=SCMCommandStatus(record.status),
            attempts=record.attempts,
            version=record.version,
            last_error=record.last_error,
            created_at=_aware(record.created_at),
            claimed_at=_aware(record.claimed_at) if record.claimed_at else None,
            completed_at=_aware(record.completed_at) if record.completed_at else None,
        )


class InMemorySCMPollCursorStore:
    def __init__(self) -> None:
        self.items: dict[tuple[UUID, UUID], SCMPollCursor] = {}

    async def get(self, change_set_id: UUID, repository_id: UUID) -> SCMPollCursor | None:
        return self.items.get((change_set_id, repository_id))

    async def upsert(self, cursor: SCMPollCursor, *, expected_version: int | None) -> None:
        key = (cursor.change_set_id, cursor.repository_id)
        current = self.items.get(key)
        if expected_version is None:
            if current is not None:
                raise DeliveryConflict("SCM poll cursor already exists")
        elif current is None or current.version != expected_version:
            raise DeliveryConflict("SCM poll cursor version changed")
        self.items[key] = cursor


class PostgresSCMPollCursorStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(self, change_set_id: UUID, repository_id: UUID) -> SCMPollCursor | None:
        async with self._database.transaction() as session:
            record = await session.get(SCMPollCursorRecord, (change_set_id, repository_id))
        if record is None:
            return None
        return SCMPollCursor(
            change_set_id=record.change_set_id,
            repository_id=record.repository_id,
            consecutive_failures=record.consecutive_failures,
            last_polled_at=_aware(record.last_polled_at) if record.last_polled_at else None,
            next_poll_at=_aware(record.next_poll_at),
            last_error=record.last_error,
            version=record.version,
        )

    async def upsert(self, cursor: SCMPollCursor, *, expected_version: int | None) -> None:
        async with self._database.transaction() as session:
            record = await session.get(
                SCMPollCursorRecord,
                (cursor.change_set_id, cursor.repository_id),
            )
            if expected_version is None:
                if record is not None:
                    raise DeliveryConflict("SCM poll cursor already exists")
                session.add(
                    SCMPollCursorRecord(
                        change_set_id=cursor.change_set_id,
                        repository_id=cursor.repository_id,
                        consecutive_failures=cursor.consecutive_failures,
                        last_polled_at=cursor.last_polled_at,
                        next_poll_at=cursor.next_poll_at,
                        last_error=cursor.last_error,
                        version=cursor.version,
                    )
                )
                return
            if record is None or record.version != expected_version:
                raise DeliveryConflict("SCM poll cursor version changed")
            record.consecutive_failures = cursor.consecutive_failures
            record.last_polled_at = cursor.last_polled_at
            record.next_poll_at = cursor.next_poll_at
            record.last_error = cursor.last_error
            record.version = cursor.version


class InMemorySCMObservationStore:
    def __init__(self) -> None:
        self.items: dict[UUID, SCMObservation] = {}
        self.identities: dict[tuple[str, str, str], UUID] = {}

    async def add(self, observation: SCMObservation) -> None:
        identity = (observation.provider, observation.source.value, observation.external_id)
        if identity in self.identities:
            raise DeliveryConflict("duplicate SCM observation")
        self.items[observation.id] = observation
        self.identities[identity] = observation.id

    async def get(self, observation_id: UUID) -> SCMObservation | None:
        return self.items.get(observation_id)

    async def get_by_identity(
        self, provider: str, source: str, external_id: str
    ) -> SCMObservation | None:
        observation_id = self.identities.get((provider, source, external_id))
        return self.items.get(observation_id) if observation_id else None

    async def update(self, observation: SCMObservation, *, expected_version: int) -> None:
        current = self.items.get(observation.id)
        if current is None or current.version != expected_version:
            raise DeliveryConflict("SCM observation version changed")
        self.items[observation.id] = observation

    async def list_replayable(
        self,
        *,
        stale_before: datetime,
        max_attempts: int,
        limit: int,
    ) -> tuple[SCMObservation, ...]:
        values = (
            item
            for item in self.items.values()
            if item.attempts < max_attempts
            and (
                item.status in {SCMObservationStatus.PENDING, SCMObservationStatus.FAILED}
                or (
                    item.status is SCMObservationStatus.PROCESSING
                    and item.claimed_at is not None
                    and item.claimed_at <= stale_before
                )
            )
        )
        return tuple(sorted(values, key=lambda item: item.received_at)[:limit])


class PostgresSCMObservationStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def add(self, observation: SCMObservation) -> None:
        try:
            async with self._database.transaction() as session:
                session.add(self._record(observation))
        except IntegrityError as error:
            raise DeliveryConflict("duplicate SCM observation") from error

    async def list_by_change_set(self, change_set_id: UUID) -> tuple[SCMObservation, ...]:
        async with self._database.transaction() as session:
            records = (
                await session.scalars(
                    select(SCMObservationRecord)
                    .where(SCMObservationRecord.change_set_id == change_set_id)
                    .order_by(SCMObservationRecord.observed_at)
                )
            ).all()
        return tuple(self._hydrate_observation(record) for record in records)

    async def get(self, observation_id: UUID) -> SCMObservation | None:
        async with self._database.transaction() as session:
            record = await session.get(SCMObservationRecord, observation_id)
        return self._hydrate_observation(record) if record else None

    async def get_by_identity(
        self, provider: str, source: str, external_id: str
    ) -> SCMObservation | None:
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(SCMObservationRecord).where(
                    SCMObservationRecord.provider == provider,
                    SCMObservationRecord.source == source,
                    SCMObservationRecord.external_id == external_id,
                )
            )
        return self._hydrate_observation(record) if record else None

    async def update(self, observation: SCMObservation, *, expected_version: int) -> None:
        async with self._database.transaction() as session:
            record = await session.get(SCMObservationRecord, observation.id)
            if record is None or record.version != expected_version:
                raise DeliveryConflict("SCM observation version changed")
            record.status = observation.status.value
            record.attempts = observation.attempts
            record.version = observation.version
            record.last_error = observation.last_error
            record.claimed_at = observation.claimed_at
            record.processed_at = observation.processed_at

    async def list_replayable(
        self,
        *,
        stale_before: datetime,
        max_attempts: int,
        limit: int,
    ) -> tuple[SCMObservation, ...]:
        async with self._database.transaction() as session:
            records = (
                await session.scalars(
                    select(SCMObservationRecord)
                    .where(
                        SCMObservationRecord.attempts < max_attempts,
                        or_(
                            SCMObservationRecord.status.in_(
                                (
                                    SCMObservationStatus.PENDING.value,
                                    SCMObservationStatus.FAILED.value,
                                )
                            ),
                            (
                                (
                                    SCMObservationRecord.status
                                    == SCMObservationStatus.PROCESSING.value
                                )
                                & (SCMObservationRecord.claimed_at <= stale_before)
                            ),
                        ),
                    )
                    .order_by(SCMObservationRecord.received_at)
                    .limit(limit)
                )
            ).all()
        return tuple(self._hydrate_observation(record) for record in records)

    @staticmethod
    def _record(observation: SCMObservation) -> SCMObservationRecord:
        return SCMObservationRecord(
            id=observation.id,
            provider=observation.provider,
            source=observation.source.value,
            external_id=observation.external_id,
            event_type=observation.event_type,
            payload=observation.payload,
            payload_hash=observation.payload_hash,
            status=observation.status.value,
            change_set_id=observation.change_set_id,
            repository_id=observation.repository_id,
            attempts=observation.attempts,
            version=observation.version,
            last_error=observation.last_error,
            observed_at=observation.observed_at,
            received_at=observation.received_at,
            claimed_at=observation.claimed_at,
            processed_at=observation.processed_at,
        )

    @staticmethod
    def _hydrate_observation(record: SCMObservationRecord) -> SCMObservation:
        return SCMObservation(
            id=record.id,
            provider=record.provider,
            source=SCMObservationSource(record.source),
            external_id=record.external_id,
            event_type=record.event_type,
            payload=record.payload,
            payload_hash=record.payload_hash,
            status=SCMObservationStatus(record.status),
            change_set_id=record.change_set_id,
            repository_id=record.repository_id,
            attempts=record.attempts,
            version=record.version,
            last_error=record.last_error,
            observed_at=_aware(record.observed_at),
            received_at=_aware(record.received_at),
            claimed_at=_aware(record.claimed_at) if record.claimed_at else None,
            processed_at=_aware(record.processed_at) if record.processed_at else None,
        )


class InMemoryChangeSetStore:
    def __init__(self) -> None:
        self.items: dict[UUID, ChangeSet] = {}
        self.keys: dict[str, tuple[UUID, str]] = {}

    async def add(self, change_set: ChangeSet, *, idempotency_key: str, fingerprint: str) -> None:
        if idempotency_key in self.keys:
            raise DeliveryConflict("duplicate ChangeSet idempotency key")
        self.items[change_set.id] = change_set
        self.keys[idempotency_key] = (change_set.id, fingerprint)

    async def get(self, change_set_id: UUID) -> ChangeSet | None:
        return self.items.get(change_set_id)

    async def get_by_idempotency_key(self, key: str) -> tuple[ChangeSet, str] | None:
        binding = self.keys.get(key)
        return (self.items[binding[0]], binding[1]) if binding else None

    async def update(self, change_set: ChangeSet, *, expected_version: int) -> None:
        current = self.items.get(change_set.id)
        if current is None or current.version != expected_version:
            raise DeliveryConflict("ChangeSet version changed")
        self.items[change_set.id] = change_set

    async def find_by_candidate(self, repository_id: UUID, head_sha: str) -> tuple[ChangeSet, ...]:
        normalized = head_sha.strip().lower()
        return tuple(
            change_set
            for change_set in self.items.values()
            if any(
                item.repository_id == repository_id and item.commit_sha == normalized
                for item in change_set.repositories
            )
        )

    async def list_active(self) -> tuple[ChangeSet, ...]:
        terminal = {ChangeSetStatus.DELIVERED, ChangeSetStatus.COMPENSATED}
        return tuple(item for item in self.items.values() if item.status not in terminal)


class PostgresChangeSetStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def add(self, change_set: ChangeSet, *, idempotency_key: str, fingerprint: str) -> None:
        try:
            async with self._database.transaction() as session:
                session.add(
                    ChangeSetRecord(
                        id=change_set.id,
                        organization_id=change_set.organization_id,
                        project_id=change_set.project_id,
                        status=change_set.status.value,
                        version=change_set.version,
                        idempotency_key=idempotency_key,
                        fingerprint=fingerprint,
                        payload=self._payload(change_set),
                        created_at=change_set.created_at,
                        updated_at=change_set.updated_at,
                    )
                )
                session.add_all(self._repository_records(change_set))
        except IntegrityError as error:
            raise DeliveryConflict("duplicate ChangeSet") from error

    async def get(self, change_set_id: UUID) -> ChangeSet | None:
        async with self._database.transaction() as session:
            record = await session.get(ChangeSetRecord, change_set_id)
        return self._hydrate(record) if record else None

    async def get_by_idempotency_key(self, key: str) -> tuple[ChangeSet, str] | None:
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(ChangeSetRecord).where(ChangeSetRecord.idempotency_key == key)
            )
        return (self._hydrate(record), record.fingerprint) if record else None

    async def update(self, change_set: ChangeSet, *, expected_version: int) -> None:
        async with self._database.transaction() as session:
            record = await session.get(ChangeSetRecord, change_set.id)
            if record is None or record.version != expected_version:
                raise DeliveryConflict("ChangeSet version changed")
            record.status = change_set.status.value
            record.version = change_set.version
            record.payload = self._payload(change_set)
            record.updated_at = change_set.updated_at
            existing = {
                item.repository_id: item
                for item in (
                    await session.scalars(
                        select(ChangeSetRepositoryRecord).where(
                            ChangeSetRepositoryRecord.change_set_id == change_set.id
                        )
                    )
                ).all()
            }
            for candidate in change_set.repositories:
                item = existing[candidate.repository_id]
                item.head_sha = candidate.commit_sha
                item.pull_request_number = candidate.pull_request_number
                item.status = candidate.status.value

    async def find_by_candidate(self, repository_id: UUID, head_sha: str) -> tuple[ChangeSet, ...]:
        normalized = head_sha.strip().lower()
        async with self._database.transaction() as session:
            ids = (
                await session.scalars(
                    select(ChangeSetRepositoryRecord.change_set_id).where(
                        ChangeSetRepositoryRecord.repository_id == repository_id,
                        ChangeSetRepositoryRecord.head_sha == normalized,
                    )
                )
            ).all()
            if not ids:
                return ()
            records = (
                await session.scalars(select(ChangeSetRecord).where(ChangeSetRecord.id.in_(ids)))
            ).all()
        return tuple(self._hydrate(record) for record in records)

    async def list_active(self) -> tuple[ChangeSet, ...]:
        terminal = (ChangeSetStatus.DELIVERED.value, ChangeSetStatus.COMPENSATED.value)
        async with self._database.transaction() as session:
            records = (
                await session.scalars(
                    select(ChangeSetRecord)
                    .where(ChangeSetRecord.status.not_in(terminal))
                    .order_by(ChangeSetRecord.updated_at)
                )
            ).all()
        return tuple(self._hydrate(record) for record in records)

    @staticmethod
    def _repository_records(
        change_set: ChangeSet,
    ) -> list[ChangeSetRepositoryRecord]:
        return [
            ChangeSetRepositoryRecord(
                change_set_id=change_set.id,
                repository_id=item.repository_id,
                head_sha=item.commit_sha,
                pull_request_number=item.pull_request_number,
                status=item.status.value,
            )
            for item in change_set.repositories
        ]

    @staticmethod
    def _payload(change_set: ChangeSet) -> dict[str, object]:
        return {
            "created_by_agent_id": str(change_set.created_by_agent_id),
            "title": change_set.title,
            "validation_snapshot_id": (
                str(change_set.validation_snapshot_id)
                if change_set.validation_snapshot_id is not None
                else None
            ),
            "merge_cursor": change_set.merge_cursor,
            "repositories": [
                {
                    "repository_id": str(item.repository_id),
                    "task_id": str(item.task_id),
                    "commit_sha": item.commit_sha,
                    "base_sha": item.base_sha,
                    "branch_name": item.branch_name,
                    "depends_on": [str(value) for value in item.depends_on],
                    "merge_order": item.merge_order,
                    "status": item.status.value,
                    "pull_request_number": item.pull_request_number,
                    "pull_request_url": item.pull_request_url,
                    "ci_check_run_id": item.ci_check_run_id,
                    "ci_summary": item.ci_summary,
                    "merge_sha": item.merge_sha,
                    "required_checks": list(item.required_checks),
                    "ci_checks": [
                        {
                            "check_name": check.check_name,
                            "check_run_id": check.check_run_id,
                            "passed": check.passed,
                            "summary": check.summary,
                        }
                        for check in item.ci_checks
                    ],
                    "required_approvals": item.required_approvals,
                    "reviews": [
                        {
                            "review_id": review.review_id,
                            "reviewer": review.reviewer,
                            "state": review.state.value,
                            "summary": review.summary,
                        }
                        for review in item.reviews
                    ],
                }
                for item in change_set.repositories
            ],
            "recovery_plans": [
                {
                    "id": str(plan.id),
                    "trigger": plan.trigger.value,
                    "reason": plan.reason,
                    "created_at": plan.created_at.isoformat(),
                    "actions": [
                        {
                            "id": str(action.id),
                            "sequence": action.sequence,
                            "kind": action.kind.value,
                            "status": action.status.value,
                            "repository_id": (
                                str(action.repository_id) if action.repository_id else None
                            ),
                            "run_id": str(action.run_id) if action.run_id else None,
                            "detail": action.detail,
                        }
                        for action in plan.actions
                    ],
                }
                for plan in change_set.recovery_plans
            ],
            "governance_decisions": [
                {
                    "id": str(item.id),
                    "repository_id": str(item.repository_id),
                    "head_sha": item.head_sha,
                    "decision": item.decision.value,
                    "decided_by_agent_id": str(item.decided_by_agent_id),
                    "reason": item.reason,
                    "decided_at": item.decided_at.isoformat(),
                }
                for item in change_set.governance_decisions
            ],
            "candidate_revisions": [
                {
                    "id": str(item.id),
                    "repository_id": str(item.repository_id),
                    "task_id": str(item.task_id),
                    "sequence": item.sequence,
                    "head_sha": item.head_sha,
                    "previous_head_sha": item.previous_head_sha,
                    "reason": item.reason,
                    "created_at": item.created_at.isoformat(),
                }
                for item in change_set.candidate_revisions
            ],
        }

    @staticmethod
    def _hydrate(record: ChangeSetRecord) -> ChangeSet:
        payload = record.payload
        repositories = tuple(
            RepositoryDelivery(
                repository_id=UUID(str(item["repository_id"])),
                task_id=UUID(str(item["task_id"])),
                commit_sha=str(item["commit_sha"]),
                base_sha=str(item["base_sha"]),
                branch_name=str(item["branch_name"]),
                depends_on=tuple(UUID(str(value)) for value in item["depends_on"]),
                merge_order=int(item["merge_order"]),
                status=RepositoryDeliveryStatus(str(item["status"])),
                pull_request_number=item.get("pull_request_number"),
                pull_request_url=item.get("pull_request_url"),
                ci_check_run_id=item.get("ci_check_run_id"),
                ci_summary=item.get("ci_summary"),
                merge_sha=item.get("merge_sha"),
                required_checks=tuple(item.get("required_checks", ())),
                ci_checks=tuple(
                    CICheckObservation(
                        check_name=str(check["check_name"]),
                        check_run_id=str(check["check_run_id"]),
                        passed=bool(check["passed"]),
                        summary=str(check["summary"]),
                    )
                    for check in item.get("ci_checks", ())
                ),
                required_approvals=int(item.get("required_approvals", 0)),
                reviews=tuple(
                    ReviewObservation(
                        review_id=str(review["review_id"]),
                        reviewer=str(review["reviewer"]),
                        state=ReviewState(str(review["state"])),
                        summary=str(review.get("summary", "")),
                    )
                    for review in item.get("reviews", ())
                ),
            )
            for item in payload["repositories"]
        )
        plans = tuple(
            RecoveryPlan(
                id=UUID(str(plan["id"])),
                trigger=RecoveryTrigger(str(plan["trigger"])),
                reason=str(plan["reason"]),
                created_at=datetime.fromisoformat(str(plan["created_at"])),
                actions=tuple(
                    RecoveryAction(
                        id=UUID(str(action["id"])),
                        sequence=int(action["sequence"]),
                        kind=RecoveryActionKind(str(action["kind"])),
                        status=RecoveryActionStatus(str(action["status"])),
                        repository_id=(
                            UUID(str(action["repository_id"]))
                            if action.get("repository_id")
                            else None
                        ),
                        run_id=UUID(str(action["run_id"])) if action.get("run_id") else None,
                        detail=str(action["detail"]),
                    )
                    for action in plan["actions"]
                ),
            )
            for plan in payload["recovery_plans"]
        )
        governance = tuple(
            GovernanceDecision(
                id=UUID(str(item["id"])),
                repository_id=UUID(str(item["repository_id"])),
                head_sha=str(item["head_sha"]),
                decision=GovernanceDecisionKind(str(item["decision"])),
                decided_by_agent_id=UUID(str(item["decided_by_agent_id"])),
                reason=str(item["reason"]),
                decided_at=datetime.fromisoformat(str(item["decided_at"])),
            )
            for item in payload.get("governance_decisions", ())
        )
        revisions = tuple(
            CandidateRevision(
                id=UUID(str(item["id"])),
                repository_id=UUID(str(item["repository_id"])),
                task_id=UUID(str(item["task_id"])),
                sequence=int(item["sequence"]),
                head_sha=str(item["head_sha"]),
                previous_head_sha=(
                    str(item["previous_head_sha"]) if item.get("previous_head_sha") else None
                ),
                reason=str(item["reason"]),
                created_at=datetime.fromisoformat(str(item["created_at"])),
            )
            for item in payload.get("candidate_revisions", ())
        )
        return ChangeSet(
            id=record.id,
            organization_id=record.organization_id,
            project_id=record.project_id,
            created_by_agent_id=UUID(str(payload["created_by_agent_id"])),
            title=str(payload["title"]),
            validation_snapshot_id=(
                UUID(str(payload["validation_snapshot_id"]))
                if payload.get("validation_snapshot_id")
                else None
            ),
            repositories=repositories,
            status=ChangeSetStatus(record.status),
            recovery_plans=plans,
            governance_decisions=governance,
            candidate_revisions=revisions,
            version=record.version,
            merge_cursor=int(payload.get("merge_cursor", 0)),
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
