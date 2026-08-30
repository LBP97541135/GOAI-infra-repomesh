from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, Integer, String, Text, UniqueConstraint, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.persistence import Database
from repomesh.persistence.base import Base

from .contracts import (
    RecoveryAction,
    RecoveryCaseStatus,
    RecoveryCaseUpsert,
    RecoveryCaseView,
    RecoveryDecisionView,
    RecoveryOperationView,
    RecoverySeverity,
    RecoverySourceType,
)

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


class RecoveryCaseConflict(RuntimeError):
    pass


class RecoveryCaseRecord(Base):
    __tablename__ = "recovery_cases"
    __table_args__ = (
        UniqueConstraint("source_type", "source_id", name="uq_recovery_cases_source"),
        {"schema": "recovery_management"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(40), index=True)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    repository_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    task_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    change_set_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    severity: Mapped[str] = mapped_column(String(20), index=True)
    summary: Mapped[str] = mapped_column(Text)
    evidence_version: Mapped[str] = mapped_column(String(300))
    available_actions: Mapped[list[str]] = mapped_column(JSON_DOCUMENT)
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RecoveryDecisionRecord(Base):
    __tablename__ = "recovery_decisions"
    __table_args__ = (
        UniqueConstraint("case_id", "case_version", name="uq_recovery_decisions_case_version"),
        {"schema": "recovery_management"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    case_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    case_version: Mapped[int] = mapped_column(Integer)
    evidence_version: Mapped[str] = mapped_column(String(300))
    action: Mapped[str] = mapped_column(String(50))
    decided_by_human_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    reason: Mapped[str] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RecoveryOperationRecord(Base):
    __tablename__ = "recovery_operations"
    __table_args__ = (
        UniqueConstraint("decision_id", name="uq_recovery_operations_decision"),
        {"schema": "recovery_management"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    case_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    decision_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), unique=True)
    action: Mapped[str] = mapped_column(String(50))
    state: Mapped[str] = mapped_column(String(30), index=True)
    attempts: Mapped[int] = mapped_column(Integer)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PostgresRecoveryCaseStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def ensure_case(self, command: RecoveryCaseUpsert) -> RecoveryCaseView:
        try:
            async with self._database.transaction() as session:
                record = await session.scalar(
                    select(RecoveryCaseRecord)
                    .where(
                        RecoveryCaseRecord.source_type == command.source_type.value,
                        RecoveryCaseRecord.source_id == command.source_id,
                    )
                    .with_for_update()
                )
                now = datetime.now(UTC)
                status = (
                    RecoveryCaseStatus.AUTOMATIC_RECOVERY
                    if command.automatic else RecoveryCaseStatus.AWAITING_DECISION
                )
                actions = [item.value for item in command.available_actions]
                if record is None:
                    record = RecoveryCaseRecord(
                        id=uuid4(), source_type=command.source_type.value,
                        source_id=command.source_id, organization_id=command.organization_id,
                        project_id=command.project_id, repository_id=command.repository_id,
                        task_id=command.task_id, change_set_id=command.change_set_id,
                        status=status.value, severity=command.severity.value,
                        summary=command.summary[:2000], evidence_version=command.evidence_version,
                        available_actions=actions, version=1, created_at=now,
                        updated_at=now, resolved_at=None,
                    )
                    session.add(record)
                elif (
                    record.organization_id != command.organization_id
                    or record.project_id != command.project_id
                    or record.repository_id != command.repository_id
                    or record.task_id != command.task_id
                    or record.change_set_id != command.change_set_id
                ):
                    raise RecoveryCaseConflict("recovery source scope changed")
                elif (
                    record.evidence_version != command.evidence_version
                    or record.summary != command.summary[:2000]
                    or record.available_actions != actions
                ):
                    record.organization_id = command.organization_id
                    record.project_id = command.project_id
                    record.repository_id = command.repository_id
                    record.task_id = command.task_id
                    record.change_set_id = command.change_set_id
                    record.status = status.value
                    record.severity = command.severity.value
                    record.summary = command.summary[:2000]
                    record.evidence_version = command.evidence_version
                    record.available_actions = actions
                    record.version += 1
                    record.updated_at = now
                    record.resolved_at = None
                await session.flush()
                return self._case_view(record)
        except IntegrityError as error:
            raise RecoveryCaseConflict("recovery source changed concurrently") from error

    async def resolve_source(self, source_type: RecoverySourceType, source_id: UUID) -> None:
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(RecoveryCaseRecord)
                .where(
                    RecoveryCaseRecord.source_type == source_type.value,
                    RecoveryCaseRecord.source_id == source_id,
                )
                .with_for_update()
            )
            if record is not None and record.status != RecoveryCaseStatus.RESOLVED.value:
                now = datetime.now(UTC)
                record.status = RecoveryCaseStatus.RESOLVED.value
                record.version += 1
                record.updated_at = now
                record.resolved_at = now

    async def get(self, case_id: UUID) -> RecoveryCaseView | None:
        async with self._database.transaction() as session:
            record = await session.get(RecoveryCaseRecord, case_id)
            return self._case_view(record) if record else None

    async def mark_automatic(self, case_id: UUID) -> None:
        async with self._database.transaction() as session:
            record = await session.get(RecoveryCaseRecord, case_id)
            if record is None:
                raise RecoveryCaseConflict("recovery case does not exist")
            record.status = RecoveryCaseStatus.AUTOMATIC_RECOVERY.value
            record.version += 1
            record.updated_at = datetime.now(UTC)

    async def list_cases(
        self, *, project_id: UUID | None = None, status: RecoveryCaseStatus | None = None
    ) -> tuple[RecoveryCaseView, ...]:
        statement = select(RecoveryCaseRecord)
        if project_id is not None:
            statement = statement.where(RecoveryCaseRecord.project_id == project_id)
        if status is not None:
            statement = statement.where(RecoveryCaseRecord.status == status.value)
        async with self._database.transaction() as session:
            records = (
                await session.scalars(statement.order_by(RecoveryCaseRecord.updated_at.desc()))
            ).all()
            return tuple(self._case_view(record) for record in records)

    async def decide(
        self, case_id: UUID, *, expected_version: int, evidence_version: str,
        action: RecoveryAction, decided_by_human_id: UUID, reason: str,
    ) -> tuple[RecoveryDecisionView, RecoveryOperationView]:
        try:
            async with self._database.transaction() as session:
                case = await session.scalar(
                    select(RecoveryCaseRecord)
                    .where(RecoveryCaseRecord.id == case_id)
                    .with_for_update()
                )
                if case is None:
                    raise RecoveryCaseConflict("recovery case does not exist")
                if case.version != expected_version or case.evidence_version != evidence_version:
                    raise RecoveryCaseConflict("recovery evidence changed")
                if action.value not in case.available_actions:
                    raise RecoveryCaseConflict("recovery action is not available")
                if case.status not in {
                    RecoveryCaseStatus.DETECTED.value,
                    RecoveryCaseStatus.AWAITING_DECISION.value,
                    RecoveryCaseStatus.FAILED.value,
                }:
                    raise RecoveryCaseConflict("recovery case is not awaiting a decision")
                now = datetime.now(UTC)
                decision = RecoveryDecisionRecord(
                    id=uuid4(), case_id=case.id, case_version=case.version,
                    evidence_version=evidence_version, action=action.value,
                    decided_by_human_id=decided_by_human_id, reason=reason[:2000],
                    decided_at=now,
                )
                operation = RecoveryOperationRecord(
                    id=uuid4(), case_id=case.id, decision_id=decision.id,
                    action=action.value, state="pending", attempts=0, lease_owner=None,
                    lease_expires_at=None, error_code=None, created_at=now,
                    updated_at=now, finished_at=None,
                )
                session.add_all((decision, operation))
                case.status = RecoveryCaseStatus.APPROVED.value
                case.version += 1
                case.updated_at = now
                await session.flush()
                return self._decision_view(decision), self._operation_view(operation)
        except IntegrityError as error:
            raise RecoveryCaseConflict("a recovery decision already exists") from error

    async def claim_operation(self, owner: str, *, lease_seconds: int = 60):
        now = datetime.now(UTC)
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(RecoveryOperationRecord)
                .where(
                    or_(
                        RecoveryOperationRecord.state == "pending",
                        (
                            (RecoveryOperationRecord.state == "executing")
                            & (RecoveryOperationRecord.lease_expires_at < now)
                        ),
                    )
                )
                .order_by(RecoveryOperationRecord.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if record is None:
                return None
            record.state = "executing"
            record.attempts += 1
            record.lease_owner = owner
            record.lease_expires_at = now + timedelta(seconds=lease_seconds)
            record.updated_at = now
            case = await session.get(RecoveryCaseRecord, record.case_id)
            if case is not None:
                case.status = RecoveryCaseStatus.EXECUTING.value
                case.updated_at = now
            return self._operation_view(record)

    async def finish_operation(
        self, operation_id: UUID, owner: str, *, succeeded: bool, error_code: str | None = None
    ) -> RecoveryOperationView:
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(RecoveryOperationRecord)
                .where(RecoveryOperationRecord.id == operation_id)
                .with_for_update()
            )
            if record is None or record.lease_owner != owner:
                raise RecoveryCaseConflict("recovery operation ownership changed")
            now = datetime.now(UTC)
            record.state = "succeeded" if succeeded else "failed"
            record.error_code = error_code
            record.lease_owner = None
            record.lease_expires_at = None
            record.updated_at = now
            record.finished_at = now
            case = await session.get(RecoveryCaseRecord, record.case_id)
            if case is not None:
                case.status = (
                    RecoveryCaseStatus.VERIFYING.value
                    if succeeded else RecoveryCaseStatus.FAILED.value
                )
                case.version += 1
                case.updated_at = now
            return self._operation_view(record)

    @staticmethod
    def _case_view(record) -> RecoveryCaseView:
        return RecoveryCaseView(
            id=record.id, source_type=RecoverySourceType(record.source_type),
            source_id=record.source_id, organization_id=record.organization_id,
            project_id=record.project_id, repository_id=record.repository_id,
            task_id=record.task_id, change_set_id=record.change_set_id,
            status=RecoveryCaseStatus(record.status), severity=RecoverySeverity(record.severity),
            summary=record.summary, evidence_version=record.evidence_version,
            available_actions=tuple(RecoveryAction(item) for item in record.available_actions),
            version=record.version, created_at=record.created_at, updated_at=record.updated_at,
            resolved_at=record.resolved_at,
        )

    @staticmethod
    def _decision_view(record) -> RecoveryDecisionView:
        return RecoveryDecisionView(
            id=record.id, case_id=record.case_id, case_version=record.case_version,
            evidence_version=record.evidence_version, action=RecoveryAction(record.action),
            decided_by_human_id=record.decided_by_human_id, reason=record.reason,
            decided_at=record.decided_at,
        )

    @staticmethod
    def _operation_view(record) -> RecoveryOperationView:
        return RecoveryOperationView(
            id=record.id, case_id=record.case_id, decision_id=record.decision_id,
            action=RecoveryAction(record.action), state=record.state, attempts=record.attempts,
            lease_owner=record.lease_owner, error_code=record.error_code,
        )
