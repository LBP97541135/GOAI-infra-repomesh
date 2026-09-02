from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON, Uuid

from repomesh.modules.project.contracts import ProjectTopologyReader
from repomesh.persistence import Database
from repomesh.persistence.base import Base

from .contracts import (
    AppendPlanTasksCommand,
    DynamicPlanRevisionView,
)
from .domain import ExecutionPlan, PlannedRepositoryTask, TaskConflict
from .infrastructure import ExecutionPlanRecord, PostgresExecutionPlanStore

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


class ExecutionPlanRevisionRecord(Base):
    __tablename__ = "execution_plan_revisions"
    __table_args__ = (
        UniqueConstraint("plan_id", "revision", name="uq_execution_plan_revisions_number"),
        UniqueConstraint("idempotency_key", name="uq_execution_plan_revisions_idempotency"),
        {"schema": "task_orchestration"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    plan_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    base_plan_version: Mapped[int] = mapped_column(Integer)
    result_plan_version: Mapped[int | None] = mapped_column(Integer)
    actor_agent_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), index=True)
    appended_items: Mapped[list[dict[str, object]]] = mapped_column(JSON_DOCUMENT)
    previous_batches: Mapped[list[list[str]]] = mapped_column(JSON_DOCUMENT)
    new_batches: Mapped[list[list[str]]] = mapped_column(JSON_DOCUMENT)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PostgresExecutionPlanRevisionStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def commit(
        self,
        original: ExecutionPlan,
        updated: ExecutionPlan,
        *,
        actor_agent_id: UUID,
        reason: str,
        appended: tuple[PlannedRepositoryTask, ...],
        idempotency_key: str,
    ) -> DynamicPlanRevisionView:
        try:
            async with self._database.transaction() as session:
                existing = await session.scalar(
                    select(ExecutionPlanRevisionRecord).where(
                        ExecutionPlanRevisionRecord.idempotency_key == idempotency_key
                    )
                )
                if existing is not None:
                    self._assert_replay(existing, original, appended, reason)
                    return self._view(existing)
                result = await session.execute(
                    update(ExecutionPlanRecord)
                    .where(
                        ExecutionPlanRecord.id == original.id,
                        ExecutionPlanRecord.version == original.version,
                    )
                    .values(
                        batches=PostgresExecutionPlanStore._encode(updated),
                        version=updated.version,
                        updated_at=datetime.now(UTC),
                        delivery_refusal=None,
                    )
                )
                if result.rowcount != 1:
                    raise TaskConflict("execution plan version changed")
                revision = 1 + int(
                    await session.scalar(
                        select(func.max(ExecutionPlanRevisionRecord.revision)).where(
                            ExecutionPlanRevisionRecord.plan_id == original.id
                        )
                    )
                    or 0
                )
                record = ExecutionPlanRevisionRecord(
                    id=uuid4(), plan_id=original.id, revision=revision,
                    base_plan_version=original.version,
                    result_plan_version=updated.version,
                    actor_agent_id=actor_agent_id, reason=reason.strip(), status="committed",
                    appended_items=[self._item(item) for item in appended],
                    previous_batches=self._batches(original), new_batches=self._batches(updated),
                    idempotency_key=idempotency_key, created_at=datetime.now(UTC),
                )
                session.add(record)
                await session.flush()
                return self._view(record)
        except IntegrityError as error:
            raise TaskConflict("dynamic plan revision conflicts with another commit") from error

    async def history(self, plan_id: UUID) -> tuple[DynamicPlanRevisionView, ...]:
        async with self._database.transaction() as session:
            records = (
                await session.scalars(
                    select(ExecutionPlanRevisionRecord)
                    .where(ExecutionPlanRevisionRecord.plan_id == plan_id)
                    .order_by(ExecutionPlanRevisionRecord.revision)
                )
            ).all()
            return tuple(self._view(record) for record in records)

    async def replay(
        self, command: AppendPlanTasksCommand, *, idempotency_key: str
    ) -> DynamicPlanRevisionView | None:
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(ExecutionPlanRevisionRecord).where(
                    ExecutionPlanRevisionRecord.idempotency_key == idempotency_key
                )
            )
            if record is None:
                return None
            if (
                record.plan_id != command.plan_id
                or record.base_plan_version != command.expected_plan_version
                or record.actor_agent_id != command.actor_agent_id
                or record.reason != command.reason.strip()
                or tuple(UUID(str(item["repository_id"])) for item in record.appended_items)
                != tuple(item.repository_id for item in command.items)
            ):
                raise TaskConflict("dynamic revision idempotency key changed meaning")
            return self._view(record)

    @classmethod
    def preview(
        cls, original: ExecutionPlan, updated: ExecutionPlan,
        *, actor_agent_id: UUID, reason: str, appended: tuple[PlannedRepositoryTask, ...],
    ) -> DynamicPlanRevisionView:
        return DynamicPlanRevisionView(
            id=uuid4(), plan_id=original.id, revision=0,
            base_plan_version=original.version, result_plan_version=None,
            actor_agent_id=actor_agent_id, reason=reason, status="preview",
            appended_repository_ids=tuple(item.repository_id for item in appended),
            previous_batches=tuple(
                tuple(item.repository_id for item in batch) for batch in original.batches
            ),
            new_batches=tuple(
                tuple(item.repository_id for item in batch) for batch in updated.batches
            ),
            created_at=datetime.now(UTC),
        )

    @staticmethod
    def _item(item: PlannedRepositoryTask) -> dict[str, object]:
        return {
            "repository_id": str(item.repository_id), "title": item.title,
            "instruction": item.instruction, "acceptance": list(item.acceptance),
            "depends_on": [str(value) for value in item.depends_on],
            "tests": list(item.tests), "test_paths": list(item.test_paths),
        }

    @staticmethod
    def _batches(plan: ExecutionPlan) -> list[list[str]]:
        return [[str(item.repository_id) for item in batch] for batch in plan.batches]

    @classmethod
    def _assert_replay(cls, record, original, appended, reason):
        if (
            record.plan_id != original.id
            or record.base_plan_version != original.version
            or record.reason != reason.strip()
            or record.appended_items != [cls._item(item) for item in appended]
        ):
            raise TaskConflict("dynamic revision idempotency key changed meaning")

    @staticmethod
    def _view(record: ExecutionPlanRevisionRecord) -> DynamicPlanRevisionView:
        return DynamicPlanRevisionView(
            id=record.id, plan_id=record.plan_id, revision=record.revision,
            base_plan_version=record.base_plan_version,
            result_plan_version=record.result_plan_version,
            actor_agent_id=record.actor_agent_id, reason=record.reason, status=record.status,
            appended_repository_ids=tuple(
                UUID(str(item["repository_id"])) for item in record.appended_items
            ),
            previous_batches=tuple(
                tuple(UUID(value) for value in batch) for batch in record.previous_batches
            ),
            new_batches=tuple(
                tuple(UUID(value) for value in batch) for batch in record.new_batches
            ),
            created_at=record.created_at,
        )


class DynamicPlanRevisionService:
    def __init__(
        self,
        plans: PostgresExecutionPlanStore,
        revisions: PostgresExecutionPlanRevisionStore,
        topologies: ProjectTopologyReader,
    ) -> None:
        self._plans = plans
        self._revisions = revisions
        self._topologies = topologies

    async def append(
        self, command: AppendPlanTasksCommand, *, idempotency_key: str
    ) -> DynamicPlanRevisionView:
        if command.mode not in {"preview", "commit"}:
            raise ValueError("dynamic plan mode must be preview or commit")
        if not command.reason.strip() or not idempotency_key.strip():
            raise ValueError("reason and idempotency key are required")
        if command.mode == "commit":
            replay = await self._revisions.replay(
                command, idempotency_key=idempotency_key.strip()
            )
            if replay is not None:
                return replay
        plan = await self._plans.get(command.plan_id)
        if plan is None:
            raise TaskConflict("execution plan does not exist")
        if plan.version != command.expected_plan_version:
            raise TaskConflict("execution plan version changed")
        if plan.created_by_agent_id != command.actor_agent_id:
            raise TaskConflict("only the plan owner can append dynamic tasks")
        topology = await self._topologies.get_view(plan.project_id)
        approved = {
            team.repository_id for team in topology.repository_teams
        } if topology is not None else set()
        requested = {item.repository_id for item in command.items}
        if command.mode == "commit" and not requested <= approved:
            raise TaskConflict("project scope expansion requires approval")
        appended = tuple(
            PlannedRepositoryTask(
                repository_id=item.repository_id, title=item.title,
                instruction=item.instruction, acceptance=item.acceptance,
                depends_on=item.depends_on, tests=item.tests, test_paths=item.test_paths,
            )
            for item in command.items
        )
        updated = plan.append_tasks(appended)
        if command.mode == "preview":
            return self._revisions.preview(
                plan, updated, actor_agent_id=command.actor_agent_id,
                reason=command.reason.strip(), appended=appended,
            )
        return await self._revisions.commit(
            plan, updated, actor_agent_id=command.actor_agent_id,
            reason=command.reason.strip(), appended=appended,
            idempotency_key=idempotency_key.strip(),
        )
