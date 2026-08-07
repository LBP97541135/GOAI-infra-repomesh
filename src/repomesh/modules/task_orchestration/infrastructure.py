from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    delete,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.modules.task_orchestration.contracts import ExecutionPlanStatus, TaskStatus
from repomesh.modules.task_orchestration.domain import (
    ExecutionPlan,
    PlannedRepositoryTask,
    Task,
    TaskConflict,
)
from repomesh.persistence import Database
from repomesh.persistence.base import Base

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


class TaskRecord(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_tasks_idempotency_key"),
        {"schema": "task_orchestration"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    repository_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    parent_task_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    assigned_by_agent_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    assignee_agent_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    title: Mapped[str] = mapped_column(String(500))
    instruction: Mapped[str] = mapped_column(Text)
    acceptance: Mapped[list[str]] = mapped_column(JSON_DOCUMENT)
    status: Mapped[str] = mapped_column(String(30), index=True)
    result_summary: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    request_fingerprint: Mapped[str] = mapped_column(String(71))


class ExecutionPlanRecord(Base):
    __tablename__ = "execution_plans"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_execution_plans_idempotency_key"),
        {"schema": "task_orchestration"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    created_by_agent_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    current_batch_index: Mapped[int] = mapped_column(Integer)
    batches: Mapped[list[list[dict[str, object]]]] = mapped_column(JSON_DOCUMENT)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ExecutionPlanTaskRecord(Base):
    """Indexed mapping from an assigned repository-leader task back to its plan."""

    __tablename__ = "execution_plan_tasks"
    __table_args__ = ({"schema": "task_orchestration"},)

    leader_task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    plan_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    batch_index: Mapped[int] = mapped_column(Integer)


class InMemoryTaskStore:
    def __init__(self) -> None:
        self.tasks: dict[UUID, Task] = {}
        self.idempotency: dict[str, tuple[UUID, str]] = {}

    async def add(
        self,
        task: Task,
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> None:
        if task.id in self.tasks or idempotency_key in self.idempotency:
            raise TaskConflict("task already exists")
        self.tasks[task.id] = task
        self.idempotency[idempotency_key] = (task.id, request_fingerprint)

    async def get(self, task_id: UUID) -> Task | None:
        return self.tasks.get(task_id)

    async def get_view(self, task_id: UUID):
        task = await self.get(task_id)
        return task.to_view() if task is not None else None

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> tuple[Task, str] | None:
        binding = self.idempotency.get(idempotency_key)
        if binding is None:
            return None
        task_id, fingerprint = binding
        return self.tasks[task_id], fingerprint

    async def update(self, task: Task, *, expected_version: int) -> None:
        current = self.tasks.get(task.id)
        if current is None or current.version != expected_version:
            raise TaskConflict("task version changed")
        self.tasks[task.id] = task

    async def list_by_project(self, project_id: UUID) -> tuple[Task, ...]:
        return tuple(task for task in self.tasks.values() if task.project_id == project_id)

    async def list_by_parent(self, parent_task_id: UUID) -> tuple[Task, ...]:
        return tuple(
            task for task in self.tasks.values() if task.parent_task_id == parent_task_id
        )


class InMemoryExecutionPlanStore:
    def __init__(self) -> None:
        self.plans: dict[UUID, ExecutionPlan] = {}
        self.idempotency: dict[str, UUID] = {}

    async def add(self, plan: ExecutionPlan, *, idempotency_key: str) -> None:
        if plan.id in self.plans or idempotency_key in self.idempotency:
            raise TaskConflict("execution plan already exists")
        self.plans[plan.id] = plan
        self.idempotency[idempotency_key] = plan.id

    async def get(self, plan_id: UUID) -> ExecutionPlan | None:
        return self.plans.get(plan_id)

    async def get_by_idempotency_key(self, idempotency_key: str) -> ExecutionPlan | None:
        plan_id = self.idempotency.get(idempotency_key)
        return self.plans.get(plan_id) if plan_id is not None else None

    async def update(self, plan: ExecutionPlan, *, expected_version: int) -> None:
        current = self.plans.get(plan.id)
        if current is None or current.version != expected_version:
            raise TaskConflict("execution plan version changed")
        self.plans[plan.id] = plan

    async def find_by_leader_task(self, leader_task_id: UUID) -> ExecutionPlan | None:
        for plan in self.plans.values():
            for batch in plan.batches:
                if any(planned.leader_task_id == leader_task_id for planned in batch):
                    return plan
        return None


class PostgresTaskStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def add(
        self,
        task: Task,
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> None:
        try:
            async with self._database.transaction() as session:
                session.add(
                    TaskRecord(
                        **self._values(task),
                        idempotency_key=idempotency_key,
                        request_fingerprint=request_fingerprint,
                    )
                )
        except IntegrityError as error:
            raise TaskConflict("task already exists") from error

    async def get(self, task_id: UUID) -> Task | None:
        async with self._database.transaction() as session:
            record = await session.get(TaskRecord, task_id)
        return self._to_domain(record) if record is not None else None

    async def get_view(self, task_id: UUID):
        task = await self.get(task_id)
        return task.to_view() if task is not None else None

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> tuple[Task, str] | None:
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(TaskRecord).where(TaskRecord.idempotency_key == idempotency_key)
            )
        if record is None:
            return None
        return self._to_domain(record), record.request_fingerprint

    async def update(self, task: Task, *, expected_version: int) -> None:
        async with self._database.transaction() as session:
            result = await session.execute(
                update(TaskRecord)
                .where(TaskRecord.id == task.id, TaskRecord.version == expected_version)
                .values(
                    status=task.status.value,
                    result_summary=task.result_summary,
                    version=task.version,
                )
            )
            if result.rowcount != 1:
                raise TaskConflict("task version changed")

    async def list_by_project(self, project_id: UUID) -> tuple[Task, ...]:
        async with self._database.transaction() as session:
            records = (
                await session.scalars(
                    select(TaskRecord)
                    .where(TaskRecord.project_id == project_id)
                    .order_by(TaskRecord.id)
                )
            ).all()
        return tuple(self._to_domain(record) for record in records)

    async def list_by_parent(self, parent_task_id: UUID) -> tuple[Task, ...]:
        async with self._database.transaction() as session:
            records = (
                await session.scalars(
                    select(TaskRecord)
                    .where(TaskRecord.parent_task_id == parent_task_id)
                    .order_by(TaskRecord.id)
                )
            ).all()
        return tuple(self._to_domain(record) for record in records)

    @staticmethod
    def _values(task: Task) -> dict[str, object]:
        return {
            "id": task.id,
            "organization_id": task.organization_id,
            "project_id": task.project_id,
            "repository_id": task.repository_id,
            "parent_task_id": task.parent_task_id,
            "assigned_by_agent_id": task.assigned_by_agent_id,
            "assignee_agent_id": task.assignee_agent_id,
            "title": task.title,
            "instruction": task.instruction,
            "acceptance": list(task.acceptance),
            "status": task.status.value,
            "result_summary": task.result_summary,
            "version": task.version,
        }

    @staticmethod
    def _to_domain(record: TaskRecord) -> Task:
        return Task(
            id=record.id,
            organization_id=record.organization_id,
            project_id=record.project_id,
            repository_id=record.repository_id,
            parent_task_id=record.parent_task_id,
            assigned_by_agent_id=record.assigned_by_agent_id,
            assignee_agent_id=record.assignee_agent_id,
            title=record.title,
            instruction=record.instruction,
            acceptance=tuple(record.acceptance),
            status=TaskStatus(record.status),
            result_summary=record.result_summary,
            version=record.version,
        )


class PostgresExecutionPlanStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def add(self, plan: ExecutionPlan, *, idempotency_key: str) -> None:
        now = datetime.now(UTC)
        try:
            async with self._database.transaction() as session:
                session.add(
                    ExecutionPlanRecord(
                        id=plan.id,
                        organization_id=plan.organization_id,
                        project_id=plan.project_id,
                        created_by_agent_id=plan.created_by_agent_id,
                        status=plan.status.value,
                        current_batch_index=plan.current_batch_index,
                        batches=self._encode(plan),
                        idempotency_key=idempotency_key,
                        version=plan.version,
                        created_at=now,
                        updated_at=now,
                    )
                )
                await self._sync_leader_tasks(session, plan)
        except IntegrityError as error:
            raise TaskConflict("execution plan already exists") from error

    async def get(self, plan_id: UUID) -> ExecutionPlan | None:
        async with self._database.transaction() as session:
            record = await session.get(ExecutionPlanRecord, plan_id)
        return self._to_domain(record) if record is not None else None

    async def get_by_idempotency_key(self, idempotency_key: str) -> ExecutionPlan | None:
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(ExecutionPlanRecord).where(
                    ExecutionPlanRecord.idempotency_key == idempotency_key
                )
            )
        return self._to_domain(record) if record is not None else None

    async def update(self, plan: ExecutionPlan, *, expected_version: int) -> None:
        async with self._database.transaction() as session:
            result = await session.execute(
                update(ExecutionPlanRecord)
                .where(
                    ExecutionPlanRecord.id == plan.id,
                    ExecutionPlanRecord.version == expected_version,
                )
                .values(
                    status=plan.status.value,
                    current_batch_index=plan.current_batch_index,
                    batches=self._encode(plan),
                    version=plan.version,
                    updated_at=datetime.now(UTC),
                )
            )
            if result.rowcount != 1:
                raise TaskConflict("execution plan version changed")
            await self._sync_leader_tasks(session, plan)

    async def find_by_leader_task(self, leader_task_id: UUID) -> ExecutionPlan | None:
        async with self._database.transaction() as session:
            binding = await session.get(ExecutionPlanTaskRecord, leader_task_id)
            record = (
                await session.get(ExecutionPlanRecord, binding.plan_id)
                if binding is not None
                else None
            )
        return self._to_domain(record) if record is not None else None

    async def _sync_leader_tasks(self, session, plan: ExecutionPlan) -> None:
        assigned = {
            planned.leader_task_id: index
            for index, batch in enumerate(plan.batches)
            for planned in batch
            if planned.leader_task_id is not None
        }
        known = set(
            (
                await session.scalars(
                    select(ExecutionPlanTaskRecord.leader_task_id).where(
                        ExecutionPlanTaskRecord.plan_id == plan.id
                    )
                )
            ).all()
        )
        for leader_task_id, batch_index in assigned.items():
            if leader_task_id in known:
                continue
            session.add(
                ExecutionPlanTaskRecord(
                    leader_task_id=leader_task_id,
                    plan_id=plan.id,
                    batch_index=batch_index,
                )
            )
        stale = known - set(assigned)
        if stale:
            await session.execute(
                delete(ExecutionPlanTaskRecord).where(
                    ExecutionPlanTaskRecord.plan_id == plan.id,
                    ExecutionPlanTaskRecord.leader_task_id.in_(stale),
                )
            )

    @staticmethod
    def _encode(plan: ExecutionPlan) -> list[list[dict[str, object]]]:
        return [
            [
                {
                    "repository_id": str(planned.repository_id),
                    "title": planned.title,
                    "instruction": planned.instruction,
                    "acceptance": list(planned.acceptance),
                    "leader_task_id": (
                        str(planned.leader_task_id)
                        if planned.leader_task_id is not None
                        else None
                    ),
                }
                for planned in batch
            ]
            for batch in plan.batches
        ]

    @staticmethod
    def _to_domain(record: ExecutionPlanRecord) -> ExecutionPlan:
        return ExecutionPlan(
            id=record.id,
            organization_id=record.organization_id,
            project_id=record.project_id,
            created_by_agent_id=record.created_by_agent_id,
            status=ExecutionPlanStatus(record.status),
            current_batch_index=record.current_batch_index,
            version=record.version,
            batches=tuple(
                tuple(
                    PlannedRepositoryTask(
                        repository_id=UUID(str(planned["repository_id"])),
                        title=str(planned["title"]),
                        instruction=str(planned["instruction"]),
                        acceptance=tuple(planned["acceptance"]),
                        leader_task_id=(
                            UUID(str(planned["leader_task_id"]))
                            if planned["leader_task_id"] is not None
                            else None
                        ),
                    )
                    for planned in batch
                )
                for batch in record.batches
            ),
        )
