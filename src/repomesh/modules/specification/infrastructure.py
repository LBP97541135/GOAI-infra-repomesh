from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import JSON, DateTime, Integer, String, UniqueConstraint, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.modules.specification.contracts import SpecificationKind, SpecificationStatus
from repomesh.modules.specification.domain import (
    Specification,
    SpecificationConflict,
    SpecificationContent,
    SpecificationVersion,
)
from repomesh.persistence import Database
from repomesh.persistence.base import Base

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class SpecificationRecord(Base):
    __tablename__ = "specifications"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_specifications_idempotency"),
        {"schema": "specification"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    repository_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    task_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    kind: Mapped[str] = mapped_column(String(30), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    title: Mapped[str] = mapped_column(String(500))
    owner_agent_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    current_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    request_fingerprint: Mapped[str] = mapped_column(String(71))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SpecificationVersionRecord(Base):
    __tablename__ = "specification_versions"
    __table_args__ = (
        UniqueConstraint(
            "specification_id", "version", name="uq_specification_versions_number"
        ),
        {"schema": "specification"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    specification_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict[str, object]] = mapped_column(JSON_DOCUMENT)
    content_hash: Mapped[str] = mapped_column(String(71))
    created_by_agent_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    supersedes_version_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InMemorySpecificationStore:
    def __init__(self) -> None:
        self.specifications: dict[UUID, Specification] = {}
        self.versions: dict[tuple[UUID, int], SpecificationVersion] = {}
        self.idempotency: dict[str, tuple[UUID, str]] = {}

    async def add(
        self,
        specification: Specification,
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> None:
        if specification.id in self.specifications or idempotency_key in self.idempotency:
            raise SpecificationConflict("specification already exists")
        self.specifications[specification.id] = specification
        self.versions[(specification.id, 1)] = specification.current_version
        self.idempotency[idempotency_key] = (
            specification.id,
            request_fingerprint,
        )

    async def get(self, specification_id: UUID) -> Specification | None:
        return self.specifications.get(specification_id)

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> tuple[Specification, str] | None:
        binding = self.idempotency.get(idempotency_key)
        if binding is None:
            return None
        specification_id, fingerprint = binding
        return self.specifications[specification_id], fingerprint

    async def update(
        self, specification: Specification, *, expected_revision: int
    ) -> None:
        current = self.specifications.get(specification.id)
        if current is None or current.revision != expected_revision:
            raise SpecificationConflict("specification revision changed")
        self.specifications[specification.id] = specification
        self.versions[(specification.id, specification.current_version.version)] = (
            specification.current_version
        )

    async def list_by_project(self, project_id: UUID) -> tuple[Specification, ...]:
        return tuple(
            value for value in self.specifications.values() if value.project_id == project_id
        )

    async def get_version(
        self, specification_id: UUID, version: int
    ) -> SpecificationVersion | None:
        return self.versions.get((specification_id, version))


class PostgresSpecificationStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def add(
        self,
        specification: Specification,
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> None:
        try:
            async with self._database.transaction() as session:
                session.add(
                    SpecificationRecord(
                        **self._specification_values(specification),
                        idempotency_key=idempotency_key,
                        request_fingerprint=request_fingerprint,
                    )
                )
                session.add(self._version_record(specification.current_version))
        except IntegrityError as error:
            raise SpecificationConflict("specification already exists") from error

    async def get(self, specification_id: UUID) -> Specification | None:
        async with self._database.transaction() as session:
            record = await session.get(SpecificationRecord, specification_id)
            if record is None:
                return None
            version = await session.get(
                SpecificationVersionRecord, record.current_version_id
            )
        return self._to_domain(record, version)

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> tuple[Specification, str] | None:
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(SpecificationRecord).where(
                    SpecificationRecord.idempotency_key == idempotency_key
                )
            )
            if record is None:
                return None
            version = await session.get(
                SpecificationVersionRecord, record.current_version_id
            )
        return self._to_domain(record, version), record.request_fingerprint

    async def update(
        self, specification: Specification, *, expected_revision: int
    ) -> None:
        async with self._database.transaction() as session:
            existing_version = await session.get(
                SpecificationVersionRecord, specification.current_version.id
            )
            if existing_version is None:
                session.add(self._version_record(specification.current_version))
            result = await session.execute(
                update(SpecificationRecord)
                .where(
                    SpecificationRecord.id == specification.id,
                    SpecificationRecord.revision == expected_revision,
                )
                .values(
                    status=specification.status.value,
                    current_version_id=specification.current_version.id,
                    revision=specification.revision,
                )
            )
            if result.rowcount != 1:
                raise SpecificationConflict("specification revision changed")

    async def list_by_project(self, project_id: UUID) -> tuple[Specification, ...]:
        async with self._database.transaction() as session:
            records = (
                await session.scalars(
                    select(SpecificationRecord)
                    .where(SpecificationRecord.project_id == project_id)
                    .order_by(SpecificationRecord.id)
                )
            ).all()
            results = []
            for record in records:
                version = await session.get(
                    SpecificationVersionRecord, record.current_version_id
                )
                results.append(self._to_domain(record, version))
        return tuple(results)

    async def get_version(
        self, specification_id: UUID, version: int
    ) -> SpecificationVersion | None:
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(SpecificationVersionRecord).where(
                    SpecificationVersionRecord.specification_id == specification_id,
                    SpecificationVersionRecord.version == version,
                )
            )
        return self._version_to_domain(record) if record is not None else None

    @staticmethod
    def _specification_values(specification: Specification) -> dict[str, object]:
        return {
            "id": specification.id,
            "organization_id": specification.organization_id,
            "project_id": specification.project_id,
            "repository_id": specification.repository_id,
            "task_id": specification.task_id,
            "kind": specification.kind.value,
            "status": specification.status.value,
            "title": specification.title,
            "owner_agent_id": specification.owner_agent_id,
            "current_version_id": specification.current_version.id,
            "revision": specification.revision,
            "created_at": specification.created_at,
        }

    @staticmethod
    def _version_record(version: SpecificationVersion) -> SpecificationVersionRecord:
        return SpecificationVersionRecord(
            id=version.id,
            specification_id=version.specification_id,
            version=version.version,
            content=version.content.canonical_payload(),
            content_hash=version.content_hash,
            created_by_agent_id=version.created_by_agent_id,
            supersedes_version_id=version.supersedes_version_id,
            created_at=version.created_at,
        )

    @classmethod
    def _to_domain(cls, record, version_record) -> Specification:
        return Specification(
            id=record.id,
            organization_id=record.organization_id,
            project_id=record.project_id,
            repository_id=record.repository_id,
            task_id=record.task_id,
            kind=SpecificationKind(record.kind),
            status=SpecificationStatus(record.status),
            title=record.title,
            owner_agent_id=record.owner_agent_id,
            current_version=cls._version_to_domain(version_record),
            revision=record.revision,
            created_at=_as_utc(record.created_at),
        )

    @staticmethod
    def _version_to_domain(record) -> SpecificationVersion:
        content = record.content
        return SpecificationVersion(
            id=record.id,
            specification_id=record.specification_id,
            version=record.version,
            content=SpecificationContent(
                goal=content["goal"],
                acceptance=tuple(content["acceptance"]),
                scope=tuple(content["scope"]),
                constraints=tuple(content["constraints"]),
                tests=tuple(content["tests"]),
                dependencies=tuple(content["dependencies"]),
                allowed_paths=tuple(content["allowed_paths"]),
                forbidden_paths=tuple(content.get("forbidden_paths", ())),
                interface_changes=tuple(content["interface_changes"]),
            ),
            created_by_agent_id=record.created_by_agent_id,
            supersedes_version_id=record.supersedes_version_id,
            created_at=_as_utc(record.created_at),
            content_hash=record.content_hash,
        )
