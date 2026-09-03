from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Index, Integer, String, UniqueConstraint, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.persistence import Database
from repomesh.persistence.base import Base

from .domain import TaskConflict
from .infrastructure import TaskRecord


class AssignmentAttemptState(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    COMPLETED = "completed"
    FAILED = "failed"


class AssignmentReason(StrEnum):
    INITIAL = "initial"
    LEASE_EXPIRED = "lease_expired"
    WORKER_UNREACHABLE = "worker_unreachable"
    RUNNER_INTERRUPTED = "runner_interrupted"
    OPERATOR = "operator"


class AssignmentActorKind(StrEnum):
    AGENT = "agent"
    HUMAN = "human"
    SYSTEM = "system"


@dataclass(frozen=True, slots=True)
class TaskAssignmentAttempt:
    id: UUID
    organization_id: UUID
    project_id: UUID
    repository_id: UUID
    task_id: UUID
    worker_agent_id: UUID
    generation: int
    state: AssignmentAttemptState
    reason: AssignmentReason
    assigned_by: AssignmentActorKind
    assigned_by_id: UUID | None
    previous_attempt_id: UUID | None
    execution_id: UUID | None
    created_at: datetime
    finished_at: datetime | None


class TaskAssignmentAttemptRecord(Base):
    __tablename__ = "task_assignment_attempts"
    __table_args__ = (
        UniqueConstraint(
            "task_id", "generation", name="uq_task_assignment_attempts_generation"
        ),
        Index(
            "uq_task_assignment_attempts_active_task",
            "task_id",
            unique=True,
            postgresql_where=text("state = 'active'"),
            sqlite_where=text("state = 'active'"),
        ),
        {"schema": "task_orchestration"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    repository_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    worker_agent_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(30), index=True)
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    assigned_by: Mapped[str] = mapped_column(String(20), nullable=False)
    assigned_by_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    previous_attempt_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    execution_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PostgresTaskAssignmentStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def ensure_initial(self, task_id: UUID) -> TaskAssignmentAttempt:
        try:
            async with self._database.transaction() as session:
                active = await session.scalar(self._active(task_id).with_for_update())
                if active is not None:
                    return self._domain(active)
                task = await session.scalar(
                    select(TaskRecord).where(TaskRecord.id == task_id).with_for_update()
                )
                if task is None:
                    raise TaskConflict("task does not exist")
                # A rerun redispatch reopens a task whose attempts have all
                # reached a terminal state. There is nothing active to return,
                # and inserting generation=1 again would violate the
                # (task_id, generation) unique constraint, so the reopened run
                # continues the lineage with a fresh generation instead of
                # failing every start_assigned_task call.
                latest = await session.scalar(
                    select(TaskAssignmentAttemptRecord)
                    .where(TaskAssignmentAttemptRecord.task_id == task_id)
                    .order_by(TaskAssignmentAttemptRecord.generation.desc())
                    .limit(1)
                )
                record = TaskAssignmentAttemptRecord(
                    id=uuid4(),
                    organization_id=task.organization_id,
                    project_id=task.project_id,
                    repository_id=task.repository_id,
                    task_id=task.id,
                    worker_agent_id=task.assignee_agent_id,
                    generation=1 if latest is None else latest.generation + 1,
                    state=AssignmentAttemptState.ACTIVE.value,
                    reason=(
                        AssignmentReason.INITIAL.value
                        if latest is None
                        else AssignmentReason.OPERATOR.value
                    ),
                    assigned_by=AssignmentActorKind.AGENT.value,
                    assigned_by_id=task.assigned_by_agent_id,
                    previous_attempt_id=None if latest is None else latest.id,
                    execution_id=None,
                    created_at=datetime.now(UTC),
                    finished_at=None,
                )
                session.add(record)
                await session.flush()
                return self._domain(record)
        except IntegrityError:
            active = await self.active(task_id)
            if active is None:
                raise
            return active

    async def active(self, task_id: UUID) -> TaskAssignmentAttempt | None:
        async with self._database.transaction() as session:
            record = await session.scalar(self._active(task_id))
            return self._domain(record) if record is not None else None

    async def history(self, task_id: UUID) -> tuple[TaskAssignmentAttempt, ...]:
        async with self._database.transaction() as session:
            records = (
                await session.scalars(
                    select(TaskAssignmentAttemptRecord)
                    .where(TaskAssignmentAttemptRecord.task_id == task_id)
                    .order_by(TaskAssignmentAttemptRecord.generation)
                )
            ).all()
            return tuple(self._domain(record) for record in records)

    async def reassign(
        self,
        task_id: UUID,
        *,
        expected_task_version: int,
        expected_generation: int,
        replacement_worker_id: UUID,
        reason: AssignmentReason,
        assigned_by: AssignmentActorKind = AssignmentActorKind.SYSTEM,
        assigned_by_id: UUID | None = None,
    ) -> TaskAssignmentAttempt:
        async with self._database.transaction() as session:
            task = await session.scalar(
                select(TaskRecord).where(TaskRecord.id == task_id).with_for_update()
            )
            active = await session.scalar(self._active(task_id).with_for_update())
            if task is None or active is None:
                raise TaskConflict("task assignment does not exist")
            if task.version != expected_task_version or active.generation != expected_generation:
                raise TaskConflict("task assignment generation changed")
            if active.worker_agent_id == replacement_worker_id:
                raise TaskConflict("replacement Worker must differ from current assignee")
            now = datetime.now(UTC)
            active.state = AssignmentAttemptState.SUPERSEDED.value
            active.finished_at = now
            task.assignee_agent_id = replacement_worker_id
            task.version += 1
            task.status = "assigned"
            task.result_summary = None
            replacement = TaskAssignmentAttemptRecord(
                id=uuid4(),
                organization_id=task.organization_id,
                project_id=task.project_id,
                repository_id=task.repository_id,
                task_id=task.id,
                worker_agent_id=replacement_worker_id,
                generation=active.generation + 1,
                state=AssignmentAttemptState.ACTIVE.value,
                reason=reason.value,
                assigned_by=assigned_by.value,
                assigned_by_id=assigned_by_id,
                previous_attempt_id=active.id,
                execution_id=None,
                created_at=now,
                finished_at=None,
            )
            session.add(replacement)
            await session.flush()
            return self._domain(replacement)

    async def reopen_same_assignment(
        self,
        task_id: UUID,
        *,
        expected_task_version: int,
        expected_generation: int,
    ) -> TaskAssignmentAttempt:
        async with self._database.transaction() as session:
            task = await session.scalar(
                select(TaskRecord).where(TaskRecord.id == task_id).with_for_update()
            )
            active = await session.scalar(self._active(task_id).with_for_update())
            if task is None or active is None:
                raise TaskConflict("task assignment does not exist")
            if task.version != expected_task_version or active.generation != expected_generation:
                raise TaskConflict("task assignment generation changed")
            task.status = "assigned"
            task.result_summary = None
            task.version += 1
            return self._domain(active)

    async def bind_execution(
        self,
        task_id: UUID,
        *,
        expected_generation: int,
        execution_id: UUID,
    ) -> TaskAssignmentAttempt:
        async with self._database.transaction() as session:
            active = await session.scalar(self._active(task_id).with_for_update())
            if active is None or active.generation != expected_generation:
                raise TaskConflict("task assignment generation changed")
            active.execution_id = execution_id
            return self._domain(active)

    async def allows_projection(
        self, task_id: UUID, assignment_attempt_id: UUID, generation: int
    ) -> bool:
        async with self._database.transaction() as session:
            record = await session.get(TaskAssignmentAttemptRecord, assignment_attempt_id)
            return bool(
                record is not None
                and record.task_id == task_id
                and record.generation == generation
                and record.state != AssignmentAttemptState.SUPERSEDED.value
            )

    async def complete_current(
        self, task_id: UUID, assignment_attempt_id: UUID, generation: int
    ) -> None:
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(TaskAssignmentAttemptRecord)
                .where(TaskAssignmentAttemptRecord.id == assignment_attempt_id)
                .with_for_update()
            )
            if (
                record is None
                or record.task_id != task_id
                or record.generation != generation
                or record.state == AssignmentAttemptState.SUPERSEDED.value
            ):
                raise TaskConflict("task assignment generation changed")
            if record.state == AssignmentAttemptState.ACTIVE.value:
                record.state = AssignmentAttemptState.COMPLETED.value
                record.finished_at = datetime.now(UTC)

    @staticmethod
    def _active(task_id: UUID):
        return select(TaskAssignmentAttemptRecord).where(
            TaskAssignmentAttemptRecord.task_id == task_id,
            TaskAssignmentAttemptRecord.state == AssignmentAttemptState.ACTIVE.value,
        )

    @staticmethod
    def _domain(record: TaskAssignmentAttemptRecord) -> TaskAssignmentAttempt:
        return TaskAssignmentAttempt(
            id=record.id,
            organization_id=record.organization_id,
            project_id=record.project_id,
            repository_id=record.repository_id,
            task_id=record.task_id,
            worker_agent_id=record.worker_agent_id,
            generation=record.generation,
            state=AssignmentAttemptState(record.state),
            reason=AssignmentReason(record.reason),
            assigned_by=AssignmentActorKind(record.assigned_by),
            assigned_by_id=record.assigned_by_id,
            previous_attempt_id=record.previous_attempt_id,
            execution_id=record.execution_id,
            created_at=record.created_at,
            finished_at=record.finished_at,
        )
