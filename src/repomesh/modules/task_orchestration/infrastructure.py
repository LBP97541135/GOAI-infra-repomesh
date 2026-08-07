from uuid import UUID

from sqlalchemy import JSON, Integer, String, Text, UniqueConstraint, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.modules.task_orchestration.contracts import TaskExecutionMode, TaskStatus
from repomesh.modules.task_orchestration.domain import Task, TaskConflict
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
    execution_mode: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), index=True)
    result_summary: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    request_fingerprint: Mapped[str] = mapped_column(String(71))


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
            "execution_mode": task.execution_mode.value,
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
            execution_mode=TaskExecutionMode(record.execution_mode),
            status=TaskStatus(record.status),
            result_summary=record.result_summary,
            version=record.version,
        )
