import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.persistence import Database
from repomesh.persistence.base import Base

from .contracts import CapabilityDefinition

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


class SkillVersionState(StrEnum):
    DRAFT = "draft"
    EVALUATING = "evaluating"
    CANARY = "canary"
    STABLE = "stable"
    DEPRECATED = "deprecated"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"


class SkillReleaseChannel(StrEnum):
    STABLE = "stable"
    CANARY = "canary"


class SkillRegistryConflict(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SkillEvaluationInput:
    dataset_id: str
    dataset_version: str
    completion_rate: float
    test_pass_rate: float
    human_rework_rate: float
    tool_error_rate: float
    average_tokens: float = 0
    average_duration_ms: float = 0


class SkillRecord(Base):
    __tablename__ = "skills"
    __table_args__ = ({"schema": "capability_management"},)
    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    allowed_roles: Mapped[list[str]] = mapped_column(JSON_DOCUMENT)
    source_repository: Mapped[str] = mapped_column(String(1000))
    source_path: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SkillVersionRecord(Base):
    __tablename__ = "skill_versions"
    __table_args__ = (
        UniqueConstraint("skill_id", "version", name="uq_skill_versions_semver"),
        {"schema": "capability_management"},
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    skill_id: Mapped[str] = mapped_column(String(120), index=True)
    version: Mapped[str] = mapped_column(String(80))
    content_hash: Mapped[str] = mapped_column(String(71))
    local_path: Mapped[str] = mapped_column(String(1000))
    state: Mapped[str] = mapped_column(String(30), index=True)
    created_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SkillEvaluationRecord(Base):
    __tablename__ = "skill_evaluations"
    __table_args__ = ({"schema": "capability_management"},)
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    dataset_id: Mapped[str] = mapped_column(String(200))
    dataset_version: Mapped[str] = mapped_column(String(100))
    metrics: Mapped[dict[str, float]] = mapped_column(JSON_DOCUMENT)
    thresholds: Mapped[dict[str, float]] = mapped_column(JSON_DOCUMENT)
    passed: Mapped[bool] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SkillReleaseRecord(Base):
    __tablename__ = "skill_releases"
    __table_args__ = (
        Index("uq_skill_releases_active_channel", "skill_id", "channel", unique=True,
              postgresql_where=text("status = 'active'"),
              sqlite_where=text("status = 'active'")),
        {"schema": "capability_management"},
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    skill_id: Mapped[str] = mapped_column(String(120), index=True)
    version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    channel: Mapped[str] = mapped_column(String(20), index=True)
    traffic_percent: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SkillAssignmentRecord(Base):
    __tablename__ = "skill_assignments"
    __table_args__ = (
        UniqueConstraint("task_id", "skill_id", name="uq_skill_assignments_task_skill"),
        {"schema": "capability_management"},
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    run_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    skill_id: Mapped[str] = mapped_column(String(120), index=True)
    version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    release_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PostgresSkillRegistry:
    THRESHOLDS = {
        "completion_rate": 0.90, "test_pass_rate": 0.90,
        "human_rework_rate": 0.20, "tool_error_rate": 0.10,
    }

    def __init__(self, database: Database, capability_root: Path = Path(".")) -> None:
        self._database = database
        self._capability_root = capability_root.resolve()

    async def bootstrap_definition(
        self, definition: CapabilityDefinition, capability_root: Path
    ) -> None:
        await self.ensure_skill(definition)
        if definition.local_path is None:
            return
        path = (capability_root.resolve() / definition.local_path).resolve()
        if not path.is_relative_to(capability_root.resolve()) or not path.is_file():
            raise SkillRegistryConflict(f"reviewed Skill wrapper is missing: {definition.id}")
        digest = f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
        try:
            async with self._database.transaction() as session:
                existing = await session.scalar(
                    select(SkillVersionRecord).where(
                        SkillVersionRecord.skill_id == definition.id,
                        SkillVersionRecord.version == "1.0.0",
                    )
                )
                if existing is not None:
                    return
                now = datetime.now(UTC)
                version = SkillVersionRecord(
                    id=uuid4(), skill_id=definition.id, version="1.0.0",
                    content_hash=digest, local_path=definition.local_path,
                    state=SkillVersionState.STABLE.value, created_by=None,
                    created_at=now, updated_at=now,
                )
                release = SkillReleaseRecord(
                    id=uuid4(), skill_id=definition.id, version_id=version.id,
                    channel=SkillReleaseChannel.STABLE.value, traffic_percent=100,
                    status="active", created_at=now, ended_at=None,
                )
                session.add_all((version, release))
        except IntegrityError:
            # Another process seeded the same trusted baseline.
            return

    async def ensure_skill(self, definition: CapabilityDefinition) -> None:
        try:
            async with self._database.transaction() as session:
                existing = await session.get(SkillRecord, definition.id)
                if existing is not None:
                    return
                session.add(SkillRecord(
                    id=definition.id, title=definition.title,
                    allowed_roles=[
                        role.value
                        for role in sorted(definition.allowed_roles, key=str)
                    ],
                    source_repository=definition.source.repository,
                    source_path=definition.source.path, created_at=datetime.now(UTC),
                ))
        except IntegrityError:
            return

    async def create_version(
        self, skill_id: str, version: str, content_hash: str, local_path: str,
        *, created_by: UUID | None = None,
    ) -> UUID:
        if not version.strip() or not content_hash.startswith("sha256:"):
            raise ValueError("Skill version and sha256 content hash are required")
        path = (self._capability_root / local_path).resolve()
        if not path.is_relative_to(self._capability_root) or not path.is_file():
            raise SkillRegistryConflict("Skill wrapper path is outside the reviewed root")
        actual_hash = f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
        if actual_hash != content_hash:
            raise SkillRegistryConflict("Skill wrapper content hash does not match")
        now = datetime.now(UTC)
        record = SkillVersionRecord(
            id=uuid4(), skill_id=skill_id, version=version.strip(),
            content_hash=content_hash, local_path=local_path,
            state=SkillVersionState.DRAFT.value, created_by=created_by,
            created_at=now, updated_at=now,
        )
        try:
            async with self._database.transaction() as session:
                if await session.get(SkillRecord, skill_id) is None:
                    raise SkillRegistryConflict("Skill does not exist")
                session.add(record)
        except IntegrityError as error:
            raise SkillRegistryConflict("Skill version already exists") from error
        return record.id

    async def evaluate(self, version_id: UUID, values: SkillEvaluationInput) -> bool:
        metrics = {
            "completion_rate": values.completion_rate,
            "test_pass_rate": values.test_pass_rate,
            "human_rework_rate": values.human_rework_rate,
            "tool_error_rate": values.tool_error_rate,
            "average_tokens": values.average_tokens,
            "average_duration_ms": values.average_duration_ms,
        }
        passed = (
            values.completion_rate >= self.THRESHOLDS["completion_rate"]
            and values.test_pass_rate >= self.THRESHOLDS["test_pass_rate"]
            and values.human_rework_rate <= self.THRESHOLDS["human_rework_rate"]
            and values.tool_error_rate <= self.THRESHOLDS["tool_error_rate"]
        )
        async with self._database.transaction() as session:
            version = await session.scalar(
                select(SkillVersionRecord)
                .where(SkillVersionRecord.id == version_id)
                .with_for_update()
            )
            if version is None or version.state not in {
                SkillVersionState.DRAFT.value,
                SkillVersionState.EVALUATING.value,
                SkillVersionState.CANARY.value,
            }:
                raise SkillRegistryConflict("Skill version cannot be evaluated")
            was_canary = version.state == SkillVersionState.CANARY.value
            version.state = (
                SkillVersionState.CANARY.value
                if passed and was_canary
                else SkillVersionState.EVALUATING.value
                if passed
                else SkillVersionState.ROLLED_BACK.value
                if was_canary
                else SkillVersionState.REJECTED.value
            )
            if was_canary and not passed:
                release = await session.scalar(
                    select(SkillReleaseRecord).where(
                        SkillReleaseRecord.version_id == version.id,
                        SkillReleaseRecord.channel == SkillReleaseChannel.CANARY.value,
                        SkillReleaseRecord.status == "active",
                    ).with_for_update()
                )
                if release is not None:
                    release.status = "rolled_back"
                    release.ended_at = datetime.now(UTC)
            version.updated_at = datetime.now(UTC)
            session.add(SkillEvaluationRecord(
                id=uuid4(), version_id=version_id, dataset_id=values.dataset_id,
                dataset_version=values.dataset_version, metrics=metrics,
                thresholds=dict(self.THRESHOLDS), passed=passed, created_at=datetime.now(UTC),
            ))
        return passed

    async def release(
        self, version_id: UUID, channel: SkillReleaseChannel, *, traffic_percent: int = 100
    ) -> UUID:
        if not 0 <= traffic_percent <= 100:
            raise ValueError("traffic_percent must be 0-100")
        async with self._database.transaction() as session:
            version = await session.scalar(
                select(SkillVersionRecord)
                .where(SkillVersionRecord.id == version_id)
                .with_for_update()
            )
            if version is None:
                raise SkillRegistryConflict("Skill version does not exist")
            allowed = (
                version.state == SkillVersionState.EVALUATING.value
                or (
                    channel is SkillReleaseChannel.STABLE
                    and version.state == SkillVersionState.CANARY.value
                )
            )
            if not allowed:
                raise SkillRegistryConflict("Skill version has not passed its promotion gate")
            active_statement = select(SkillReleaseRecord).where(
                SkillReleaseRecord.skill_id == version.skill_id,
                SkillReleaseRecord.status == "active",
            )
            if channel is SkillReleaseChannel.CANARY:
                active_statement = active_statement.where(
                    SkillReleaseRecord.channel == channel.value
                )
            active = (
                await session.scalars(active_statement.with_for_update())
            ).all()
            now = datetime.now(UTC)
            for item in active:
                item.status = "replaced"
                item.ended_at = now
                if channel is SkillReleaseChannel.STABLE:
                    replaced_version = await session.get(
                        SkillVersionRecord, item.version_id
                    )
                    if replaced_version is not None:
                        replaced_version.state = SkillVersionState.DEPRECATED.value
                        replaced_version.updated_at = now
            release = SkillReleaseRecord(
                id=uuid4(), skill_id=version.skill_id, version_id=version.id,
                channel=channel.value,
                traffic_percent=(traffic_percent if channel is SkillReleaseChannel.CANARY else 100),
                status="active", created_at=now, ended_at=None,
            )
            session.add(release)
            version.state = (
                SkillVersionState.CANARY.value
                if channel is SkillReleaseChannel.CANARY else SkillVersionState.STABLE.value
            )
            version.updated_at = now
            await session.flush()
            return release.id

    async def rollback_canary(self, skill_id: str) -> None:
        async with self._database.transaction() as session:
            release = await session.scalar(
                select(SkillReleaseRecord).where(
                    SkillReleaseRecord.skill_id == skill_id,
                    SkillReleaseRecord.channel == SkillReleaseChannel.CANARY.value,
                    SkillReleaseRecord.status == "active",
                ).with_for_update()
            )
            if release is None:
                raise SkillRegistryConflict("active canary release does not exist")
            version = await session.get(SkillVersionRecord, release.version_id)
            now = datetime.now(UTC)
            release.status = "rolled_back"
            release.ended_at = now
            if version is not None:
                version.state = SkillVersionState.ROLLED_BACK.value
                version.updated_at = now

    async def version_view(self, version_id: UUID) -> dict[str, object] | None:
        async with self._database.transaction() as session:
            version = await session.get(SkillVersionRecord, version_id)
            if version is None:
                return None
            return {
                "id": version.id,
                "skill_id": version.skill_id,
                "version": version.version,
                "content_hash": version.content_hash,
                "local_path": version.local_path,
                "state": version.state,
            }

    async def skill_history(self, skill_id: str) -> dict[str, object]:
        async with self._database.transaction() as session:
            skill = await session.get(SkillRecord, skill_id)
            if skill is None:
                raise SkillRegistryConflict("Skill does not exist")
            versions = (
                await session.scalars(
                    select(SkillVersionRecord)
                    .where(SkillVersionRecord.skill_id == skill_id)
                    .order_by(SkillVersionRecord.created_at)
                )
            ).all()
            releases = (
                await session.scalars(
                    select(SkillReleaseRecord)
                    .where(SkillReleaseRecord.skill_id == skill_id)
                    .order_by(SkillReleaseRecord.created_at)
                )
            ).all()
            return {
                "skill": {"id": skill.id, "title": skill.title},
                "versions": [
                    {"id": item.id, "version": item.version, "state": item.state,
                     "content_hash": item.content_hash, "local_path": item.local_path}
                    for item in versions
                ],
                "releases": [
                    {"id": item.id, "version_id": item.version_id,
                     "channel": item.channel, "traffic_percent": item.traffic_percent,
                     "status": item.status}
                    for item in releases
                ],
            }

    async def resolve(
        self, definition: CapabilityDefinition, task_id: UUID, *, run_id: UUID | None = None
    ) -> CapabilityDefinition:
        try:
            return await self._resolve_once(definition, task_id, run_id=run_id)
        except IntegrityError:
            async with self._database.transaction() as session:
                existing = await session.scalar(
                    select(SkillAssignmentRecord).where(
                        SkillAssignmentRecord.task_id == task_id,
                        SkillAssignmentRecord.skill_id == definition.id,
                    )
                )
                if existing is None:
                    raise
                version = await session.get(SkillVersionRecord, existing.version_id)
                return self._bound(
                    definition, version, existing.release_id, existing.id
                )

    async def _resolve_once(
        self, definition: CapabilityDefinition, task_id: UUID,
        *, run_id: UUID | None = None,
    ) -> CapabilityDefinition:
        async with self._database.transaction() as session:
            existing = await session.scalar(
                select(SkillAssignmentRecord).where(
                    SkillAssignmentRecord.task_id == task_id,
                    SkillAssignmentRecord.skill_id == definition.id,
                )
            )
            if existing is not None:
                version = await session.get(SkillVersionRecord, existing.version_id)
                return self._bound(definition, version, existing.release_id, existing.id)
            releases = (
                await session.scalars(
                    select(SkillReleaseRecord).where(
                        SkillReleaseRecord.skill_id == definition.id,
                        SkillReleaseRecord.status == "active",
                    )
                )
            ).all()
            stable = next((r for r in releases if r.channel == "stable"), None)
            canary = next((r for r in releases if r.channel == "canary"), None)
            selected = stable
            if canary is not None:
                bucket = int.from_bytes(
                    hashlib.sha256(f"{definition.id}:{task_id}".encode()).digest()[:4], "big"
                ) % 100
                if bucket < canary.traffic_percent:
                    selected = canary
            if selected is None:
                return definition
            version = await session.get(SkillVersionRecord, selected.version_id)
            assignment = SkillAssignmentRecord(
                id=uuid4(), task_id=task_id, run_id=run_id, skill_id=definition.id,
                version_id=selected.version_id, release_id=selected.id,
                assigned_at=datetime.now(UTC),
            )
            session.add(assignment)
            await session.flush()
            return self._bound(definition, version, selected.id, assignment.id)

    @staticmethod
    def _bound(definition, version, release_id, assignment_id):
        if version is None:
            raise SkillRegistryConflict("assigned Skill version is missing")
        return replace(
            definition, version=version.version, release_id=release_id,
            assignment_id=assignment_id, content_hash=version.content_hash,
            local_path=version.local_path,
        )
