from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.modules.agent_runtime.contracts import (
    WorkerPreflightDecision,
    WorkerPreflightView,
)
from repomesh.persistence import Database
from repomesh.persistence.base import Base


class WorkerPreflightRecord(Base):
    __tablename__ = "worker_preflights"
    __table_args__ = {"schema": "agent_runtime"}

    task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    worker_agent_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    decision: Mapped[str] = mapped_column(String(30), index=True)
    spec_understood: Mapped[bool] = mapped_column(Boolean)
    scope_sufficient: Mapped[bool] = mapped_column(Boolean)
    tests_defined: Mapped[bool] = mapped_column(Boolean)
    dependencies_ready: Mapped[bool] = mapped_column(Boolean)
    notes: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer)
    assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InMemoryWorkerPreflightStore:
    def __init__(self) -> None:
        self.items: dict[UUID, WorkerPreflightView] = {}

    async def get(self, task_id: UUID) -> WorkerPreflightView | None:
        return self.items.get(task_id)

    async def save(self, assessment: WorkerPreflightView) -> WorkerPreflightView:
        self.items[assessment.task_id] = assessment
        return assessment


class PostgresWorkerPreflightStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(self, task_id: UUID) -> WorkerPreflightView | None:
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(WorkerPreflightRecord).where(WorkerPreflightRecord.task_id == task_id)
            )
        return self._view(record) if record is not None else None

    async def save(self, assessment: WorkerPreflightView) -> WorkerPreflightView:
        async with self._database.transaction() as session:
            record = await session.get(WorkerPreflightRecord, assessment.task_id)
            if record is None:
                session.add(WorkerPreflightRecord(**self._values(assessment)))
            else:
                for key, value in self._values(assessment).items():
                    setattr(record, key, value)
        return assessment

    @staticmethod
    def _values(view: WorkerPreflightView) -> dict[str, object]:
        return {
            "task_id": view.task_id,
            "worker_agent_id": view.worker_agent_id,
            "decision": view.decision.value,
            "spec_understood": view.spec_understood,
            "scope_sufficient": view.scope_sufficient,
            "tests_defined": view.tests_defined,
            "dependencies_ready": view.dependencies_ready,
            "notes": view.notes,
            "revision": view.revision,
            "assessed_at": view.assessed_at,
        }

    @staticmethod
    def _view(record: WorkerPreflightRecord) -> WorkerPreflightView:
        return WorkerPreflightView(
            task_id=record.task_id,
            worker_agent_id=record.worker_agent_id,
            decision=WorkerPreflightDecision(record.decision),
            spec_understood=record.spec_understood,
            scope_sufficient=record.scope_sufficient,
            tests_defined=record.tests_defined,
            dependencies_ready=record.dependencies_ready,
            notes=record.notes,
            revision=record.revision,
            assessed_at=record.assessed_at,
        )
