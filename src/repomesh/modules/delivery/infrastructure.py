from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.persistence import Database
from repomesh.persistence.base import Base

from .contracts import (
    ChangeSetStatus,
    RecoveryActionKind,
    RecoveryActionStatus,
    RecoveryTrigger,
    RepositoryDeliveryStatus,
)
from .domain import (
    ChangeSet,
    CICheckObservation,
    DeliveryConflict,
    RecoveryAction,
    RecoveryPlan,
    RepositoryDelivery,
)

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


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


class InMemoryChangeSetStore:
    def __init__(self) -> None:
        self.items: dict[UUID, ChangeSet] = {}
        self.keys: dict[str, tuple[UUID, str]] = {}

    async def add(
        self, change_set: ChangeSet, *, idempotency_key: str, fingerprint: str
    ) -> None:
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

    async def find_by_candidate(
        self, repository_id: UUID, head_sha: str
    ) -> tuple[ChangeSet, ...]:
        normalized = head_sha.strip().lower()
        return tuple(
            change_set
            for change_set in self.items.values()
            if any(
                item.repository_id == repository_id and item.commit_sha == normalized
                for item in change_set.repositories
            )
        )


class PostgresChangeSetStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def add(
        self, change_set: ChangeSet, *, idempotency_key: str, fingerprint: str
    ) -> None:
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
                session.add_all(
                    self._repository_records(change_set)
                )
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

    async def find_by_candidate(
        self, repository_id: UUID, head_sha: str
    ) -> tuple[ChangeSet, ...]:
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
            "validation_snapshot_id": str(change_set.validation_snapshot_id),
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
        return ChangeSet(
            id=record.id,
            organization_id=record.organization_id,
            project_id=record.project_id,
            created_by_agent_id=UUID(str(payload["created_by_agent_id"])),
            title=str(payload["title"]),
            validation_snapshot_id=UUID(str(payload["validation_snapshot_id"])),
            repositories=repositories,
            status=ChangeSetStatus(record.status),
            recovery_plans=plans,
            version=record.version,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
