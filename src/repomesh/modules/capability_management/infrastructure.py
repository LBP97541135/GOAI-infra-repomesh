import hashlib
import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, Integer, String, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.persistence import Database
from repomesh.persistence.base import Base

from .contracts import (
    SkillLifecycleRefused,
    SkillVersionStatus,
    assert_skill_transition,
)

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")

_SEMVER = "^\\d+\\.\\d+\\.\\d+$"


class SkillVersionRecord(Base):
    __tablename__ = "skill_versions"
    __table_args__ = (
        UniqueConstraint("skill_id", "version", name="uq_skill_versions_id_version"),
        {"schema": "capability_management"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    skill_id: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), index=True)
    local_path: Mapped[str] = mapped_column(String(500))
    content_hash: Mapped[str] = mapped_column(String(80))
    created_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SkillEvaluationRecord(Base):
    __tablename__ = "skill_evaluations"
    __table_args__ = ({"schema": "capability_management"},)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    scenario: Mapped[str] = mapped_column(String(500))
    negative_case: Mapped[str] = mapped_column(String(500))
    outcome: Mapped[str] = mapped_column(String(10))
    evidence: Mapped[str] = mapped_column(String(2000))
    evaluated_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SkillSnapshotRecord(Base):
    __tablename__ = "skill_snapshots"
    __table_args__ = (
        UniqueConstraint("organization_id", "versions", name="uq_skill_snapshots_org_versions"),
        {"schema": "capability_management"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    versions: Mapped[list[str]] = mapped_column(JSON_DOCUMENT)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class McpServerPolicyRecord(Base):
    __tablename__ = "mcp_server_policies"
    __table_args__ = ({"schema": "capability_management"},)

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer)
    max_retries: Mapped[int] = mapped_column(Integer)
    retryable_only_reads: Mapped[bool] = mapped_column()
    degraded_block_writes: Mapped[bool] = mapped_column()
    required_task_features: Mapped[list[str]] = mapped_column(JSON_DOCUMENT)


#: Seeded from capabilities/mcp/servers.json contracts. repomesh-task-control
#: is a start action, not idempotent, so it retries nothing and times out fast.
_DEFAULT_MCP_POLICIES: tuple[tuple[str, int, int, bool, bool, list[str]], ...] = (
    ("github", 30, 2, True, True, []),
    ("context7", 20, 2, True, True, []),
    ("playwright", 60, 1, True, True, ["web_e2e"]),
    ("repomesh-task-control", 10, 0, False, False, []),
)


async def seed_mcp_policies(registry: "SkillRegistryService") -> None:
    for policy_id, timeout, retries, read_only, block_writes, features in _DEFAULT_MCP_POLICIES:
        await registry.seed_mcp_policy(
            policy_id=policy_id,
            timeout_seconds=timeout,
            max_retries=retries,
            retryable_only_reads=read_only,
            degraded_block_writes=block_writes,
            required_task_features=features,
        )


class SkillRegistryService:
    """Skill version lifecycle: register, evaluate, canary, promote, rollback.

    Transition and gate rules live here, not in the API layer: the API only
    translates ``SkillLifecycleRefused`` into a 409. Evaluation evidence is
    retained for every version — a refused promotion is as much a fact as an
    accepted one, which is why refusals append records instead of raising away.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    async def register_version(
        self,
        *,
        skill_id: str,
        version: str,
        local_path: str,
        content_hash: str,
        created_by: str,
    ) -> SkillVersionRecord:
        if not re.match(_SEMVER, version):
            raise SkillLifecycleRefused(
                "invalid_version", f"version {version!r} is not MAJOR.MINOR.PATCH"
            )
        async with self._database.transaction() as session:
            current = await session.scalar(
                select(SkillVersionRecord)
                .where(
                    SkillVersionRecord.skill_id == skill_id,
                    SkillVersionRecord.status.in_(
                        (
                            SkillVersionStatus.DRAFT.value,
                            SkillVersionStatus.EVALUATING.value,
                            SkillVersionStatus.CANARY.value,
                            SkillVersionStatus.PROMOTED.value,
                        )
                    ),
                )
                .order_by(SkillVersionRecord.created_at.desc())
                .limit(1)
            )
            if current is not None and current.version == version:
                raise SkillLifecycleRefused(
                    "duplicate_version",
                    f"{skill_id}@{version} already exists in state {current.status}",
                )
            record = SkillVersionRecord(
                id=uuid4(),
                skill_id=skill_id,
                version=version,
                status=SkillVersionStatus.DRAFT.value,
                local_path=local_path,
                content_hash=content_hash,
                created_by=created_by,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(record)
            return record

    async def transition(
        self, version_id: UUID, target: SkillVersionStatus, *, actor: str
    ) -> SkillVersionRecord:
        del actor  # recorded on the evaluation/audit trail by the caller's log emit
        async with self._database.transaction() as session:
            record = await session.get(SkillVersionRecord, version_id)
            if record is None:
                raise SkillLifecycleRefused("unknown_version", "no such skill version")
            current = SkillVersionStatus(record.status)
            assert_skill_transition(current, target)
            if target is SkillVersionStatus.CANARY:
                await self._require_clean_gate(session, version_id)
            if target is SkillVersionStatus.PROMOTED:
                await self._require_canary_window_pass(session, record)
            record.status = target.value
            record.updated_at = datetime.now(UTC)
            return record

    async def record_evaluation(
        self,
        *,
        version_id: UUID,
        scenario: str,
        negative_case: str,
        outcome: bool,
        evidence: str,
        evaluated_by: str,
    ) -> SkillEvaluationRecord:
        async with self._database.transaction() as session:
            record = await session.get(SkillVersionRecord, version_id)
            if record is None:
                raise SkillLifecycleRefused("unknown_version", "no such skill version")
            if record.status not in (
                SkillVersionStatus.EVALUATING.value,
                SkillVersionStatus.CANARY.value,
            ):
                raise SkillLifecycleRefused(
                    "evaluation_not_accepted",
                    "evaluations are only accepted while evaluating or canary, "
                    f"not {record.status}",
                )
            evaluation = SkillEvaluationRecord(
                id=uuid4(),
                version_id=version_id,
                scenario=scenario,
                negative_case=negative_case,
                outcome="pass" if outcome else "fail",
                evidence=evidence,
                evaluated_by=evaluated_by,
                created_at=datetime.now(UTC),
            )
            session.add(evaluation)
            if not outcome and record.status == SkillVersionStatus.CANARY.value:
                record.status = SkillVersionStatus.ROLLED_BACK.value
                record.updated_at = datetime.now(UTC)
            return evaluation

    async def rollback(self, version_id: UUID, *, actor: str) -> SkillVersionRecord:
        del actor
        async with self._database.transaction() as session:
            record = await session.get(SkillVersionRecord, version_id)
            if record is None:
                raise SkillLifecycleRefused("unknown_version", "no such skill version")
            current = SkillVersionStatus(record.status)
            assert_skill_transition(current, SkillVersionStatus.ROLLED_BACK)
            record.status = SkillVersionStatus.ROLLED_BACK.value
            record.updated_at = datetime.now(UTC)
            return record

    async def seed_mcp_policy(
        self,
        *,
        policy_id: str,
        timeout_seconds: int,
        max_retries: int,
        retryable_only_reads: bool,
        degraded_block_writes: bool,
        required_task_features: list[str],
    ) -> None:
        async with self._database.transaction() as session:
            existing = await session.get(McpServerPolicyRecord, policy_id)
            if existing is not None:
                return
            session.add(
                McpServerPolicyRecord(
                    id=policy_id,
                    timeout_seconds=timeout_seconds,
                    max_retries=max_retries,
                    retryable_only_reads=retryable_only_reads,
                    degraded_block_writes=degraded_block_writes,
                    required_task_features=required_task_features,
                )
            )

    async def seed_promoted(self, *, skill_id: str, local_path: str) -> bool:
        """Register 1.0.0 as promoted without walking the evaluation gate.

        The seed IS the shipped baseline — demanding evaluation evidence
        retroactively would be theatre. Idempotent: an existing row for
        (skill_id, 1.0.0) short-circuits and keeps its status, so an operator
        who rolled the seed back is not silently re-promoted on next boot.
        """

        async with self._database.transaction() as session:
            existing = await session.scalar(
                select(SkillVersionRecord).where(
                    SkillVersionRecord.skill_id == skill_id,
                    SkillVersionRecord.version == "1.0.0",
                )
            )
            if existing is not None:
                return False
            try:
                wrapper = open(local_path, "rb").read()  # noqa: SIM115
            except OSError:
                wrapper = None
            content_hash = (
                f"sha256:{hashlib.sha256(wrapper).hexdigest()}" if wrapper else "seeded"
            )
            now = datetime.now(UTC)
            session.add(
                SkillVersionRecord(
                    id=uuid4(),
                    skill_id=skill_id,
                    version="1.0.0",
                    status=SkillVersionStatus.PROMOTED.value,
                    local_path=local_path,
                    content_hash=content_hash,
                    created_by="bootstrap",
                    created_at=now,
                    updated_at=now,
                )
            )
            return True

    async def resolve_current(
        self, skill_id: str, organization_id: UUID | None
    ) -> SkillVersionRecord | None:
        """Promoted version for the skill, or its canary version for this org. None if none."""
        async with self._database.transaction() as session:
            promoted = await session.scalar(
                select(SkillVersionRecord)
                .where(
                    SkillVersionRecord.skill_id == skill_id,
                    SkillVersionRecord.status == SkillVersionStatus.PROMOTED.value,
                )
                .order_by(SkillVersionRecord.updated_at.desc())
                .limit(1)
            )
            if promoted is not None:
                return promoted
            if organization_id is None:
                return None
            return await session.scalar(
                select(SkillVersionRecord)
                .where(
                    SkillVersionRecord.skill_id == skill_id,
                    SkillVersionRecord.status == SkillVersionStatus.CANARY.value,
                    SkillVersionRecord.created_by == str(organization_id),
                )
                .order_by(SkillVersionRecord.updated_at.desc())
                .limit(1)
            )

    async def create_snapshot(
        self, *, organization_id: UUID | None, versions: list[str]
    ) -> SkillSnapshotRecord:
        now = datetime.now(UTC)
        try:
            async with self._database.transaction() as session:
                existing = await session.scalar(
                    select(SkillSnapshotRecord).where(
                        SkillSnapshotRecord.organization_id == organization_id,
                        SkillSnapshotRecord.versions == versions,
                    )
                )
                if existing is not None:
                    return existing
                snapshot = SkillSnapshotRecord(
                    id=uuid4(),
                    organization_id=organization_id,
                    versions=versions,
                    created_at=now,
                    superseded_at=None,
                )
                session.add(snapshot)
                return snapshot
        except IntegrityError:
            async with self._database.transaction() as session:
                return await session.scalar(
                    select(SkillSnapshotRecord).where(
                        SkillSnapshotRecord.organization_id == organization_id,
                        SkillSnapshotRecord.versions == versions,
                    )
                )

    async def list_versions(self, skill_id: str | None = None) -> list[SkillVersionRecord]:
        async with self._database.transaction() as session:
            statement = select(SkillVersionRecord).order_by(
                SkillVersionRecord.skill_id, SkillVersionRecord.created_at.desc()
            )
            if skill_id is not None:
                statement = statement.where(SkillVersionRecord.skill_id == skill_id)
            return list((await session.scalars(statement)).all())

    async def list_mcp_policies(self) -> list[McpServerPolicyRecord]:
        async with self._database.transaction() as session:
            rows = await session.scalars(
                select(McpServerPolicyRecord).order_by(McpServerPolicyRecord.id)
            )
            return list(rows.all())

    async def update_mcp_policy(
        self,
        policy_id: str,
        *,
        timeout_seconds: int,
        max_retries: int,
        retryable_only_reads: bool,
        degraded_block_writes: bool,
    ) -> McpServerPolicyRecord:
        async with self._database.transaction() as session:
            record = await session.get(McpServerPolicyRecord, policy_id)
            if record is None:
                raise LookupError(policy_id)
            record.timeout_seconds = timeout_seconds
            record.max_retries = max_retries
            record.retryable_only_reads = retryable_only_reads
            record.degraded_block_writes = degraded_block_writes
            return record

    async def get_mcp_policy(self, policy_id: str) -> McpServerPolicyRecord | None:
        async with self._database.transaction() as session:
            return await session.get(McpServerPolicyRecord, policy_id)

    async def _require_clean_gate(self, session, version_id: UUID) -> None:
        fails = await session.scalar(
            select(SkillEvaluationRecord.id)
            .where(
                SkillEvaluationRecord.version_id == version_id,
                SkillEvaluationRecord.outcome == "fail",
            )
            .limit(1)
        )
        if fails is not None:
            raise SkillLifecycleRefused(
                "evaluation_gate_failed",
                "a failing evaluation record exists; fix and register a new version",
            )
        passes = await session.scalar(
            select(SkillEvaluationRecord.id)
            .where(
                SkillEvaluationRecord.version_id == version_id,
                SkillEvaluationRecord.outcome == "pass",
            )
            .limit(1)
        )
        if passes is None:
            raise SkillLifecycleRefused(
                "evaluation_gate_failed",
                "entering canary requires at least one passing evaluation",
            )

    async def _require_canary_window_pass(self, session, record: SkillVersionRecord) -> None:
        window_fail = await session.scalar(
            select(SkillEvaluationRecord.id)
            .where(
                SkillEvaluationRecord.version_id == record.id,
                SkillEvaluationRecord.outcome == "fail",
                SkillEvaluationRecord.created_at >= record.updated_at,
            )
            .limit(1)
        )
        if window_fail is not None:
            raise SkillLifecycleRefused(
                "evaluation_gate_failed", "a canary-window evaluation failed"
            )
        window_pass = await session.scalar(
            select(SkillEvaluationRecord.id)
            .where(
                SkillEvaluationRecord.version_id == record.id,
                SkillEvaluationRecord.outcome == "pass",
                SkillEvaluationRecord.created_at >= record.updated_at,
            )
            .limit(1)
        )
        if window_pass is None:
            raise SkillLifecycleRefused(
                "evaluation_gate_failed",
                "promotion requires a passing evaluation recorded during the canary window",
            )
