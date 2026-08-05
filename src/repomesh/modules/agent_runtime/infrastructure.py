from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import JSON, DateTime, Integer, String, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from repomesh.modules.agent_runtime.contracts import CodingRunStatus, WorkspaceStatus
from repomesh.modules.agent_runtime.domain import (
    CodingRun,
    CodingRunConflict,
    SessionBinding,
    Workspace,
)
from repomesh.persistence import Database
from repomesh.persistence.base import Base

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class WorkspaceRecord(Base):
    __tablename__ = "workspaces"
    __table_args__ = ({"schema": "agent_runtime"},)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    repository_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    path: Mapped[str] = mapped_column(String(2000), unique=True)
    base_sha: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), index=True)
    bound_run_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CodingRunRecord(Base):
    __tablename__ = "coding_runs"
    __table_args__ = ({"schema": "agent_runtime"},)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    repository_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    worker_agent_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    adapter_id: Mapped[str] = mapped_column(String(100), index=True)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    context_bundle_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    context_bundle_hash: Mapped[str] = mapped_column(String(71))
    coding_package_hash: Mapped[str] = mapped_column(String(71))
    base_sha: Mapped[str] = mapped_column(String(200))
    instruction: Mapped[str] = mapped_column(String(10000))
    acceptance: Mapped[list[str]] = mapped_column(JSON_DOCUMENT)
    required_tests: Mapped[list[str]] = mapped_column(JSON_DOCUMENT)
    allowed_tools: Mapped[list[str]] = mapped_column(JSON_DOCUMENT)
    allowed_paths: Mapped[list[str]] = mapped_column(JSON_DOCUMENT)
    denied_paths: Mapped[list[str]] = mapped_column(JSON_DOCUMENT)
    network_policy: Mapped[list[str]] = mapped_column(JSON_DOCUMENT)
    status: Mapped[str] = mapped_column(String(30), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    native_session_id: Mapped[str | None] = mapped_column(String(1000))
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SessionBindingRecord(Base):
    __tablename__ = "session_bindings"
    __table_args__ = ({"schema": "agent_runtime"},)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), unique=True, index=True)
    task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    adapter_id: Mapped[str] = mapped_column(String(100), index=True)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    context_bundle_hash: Mapped[str] = mapped_column(String(71))
    coding_package_hash: Mapped[str] = mapped_column(String(71))
    native_session_id: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InMemoryAgentRuntimeStore:
    def __init__(self) -> None:
        self.workspaces: dict[UUID, Workspace] = {}
        self.runs: dict[UUID, CodingRun] = {}
        self.bindings: dict[UUID, SessionBinding] = {}

    async def add(self, value) -> None:
        if isinstance(value, Workspace):
            target, key = self.workspaces, value.id
        elif isinstance(value, CodingRun):
            target, key = self.runs, value.id
        else:
            target, key = self.bindings, value.run_id
        if key in target:
            raise CodingRunConflict("agent runtime object already exists")
        target[key] = value

    async def get(self, object_id: UUID):
        return self.workspaces.get(object_id) or self.runs.get(object_id)

    async def prepare(
        self,
        run: CodingRun,
        workspace: Workspace,
        *,
        expected_workspace_revision: int,
    ) -> None:
        current = self.workspaces.get(workspace.id)
        if run.id in self.runs or current is None:
            raise CodingRunConflict("coding run preparation conflict")
        if current.revision != expected_workspace_revision:
            raise CodingRunConflict("workspace revision changed")
        self.runs[run.id] = run
        self.workspaces[workspace.id] = workspace

    async def update(self, value, *, expected_revision: int) -> None:
        target = self.workspaces if isinstance(value, Workspace) else self.runs
        current = target.get(value.id)
        if current is None or current.revision != expected_revision:
            raise CodingRunConflict("agent runtime revision changed")
        target[value.id] = value

    async def get_by_run(self, run_id: UUID) -> SessionBinding | None:
        return self.bindings.get(run_id)


class PostgresAgentRuntimeStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def add(self, value) -> None:
        if isinstance(value, Workspace):
            record = WorkspaceRecord(**self._workspace_values(value))
        elif isinstance(value, CodingRun):
            record = CodingRunRecord(**self._run_values(value))
        else:
            record = SessionBindingRecord(**self._binding_values(value))
        try:
            async with self._database.transaction() as session:
                session.add(record)
        except IntegrityError as error:
            raise CodingRunConflict("agent runtime object already exists") from error

    async def get(self, object_id: UUID):
        async with self._database.transaction() as session:
            workspace = await session.get(WorkspaceRecord, object_id)
            if workspace is not None:
                return self._workspace_domain(workspace)
            run = await session.get(CodingRunRecord, object_id)
        return self._run_domain(run) if run is not None else None

    async def prepare(
        self,
        run: CodingRun,
        workspace: Workspace,
        *,
        expected_workspace_revision: int,
    ) -> None:
        values = self._workspace_values(workspace)
        values.pop("id")
        try:
            async with self._database.transaction() as session:
                session.add(CodingRunRecord(**self._run_values(run)))
                result = await session.execute(
                    update(WorkspaceRecord)
                    .where(
                        WorkspaceRecord.id == workspace.id,
                        WorkspaceRecord.revision == expected_workspace_revision,
                    )
                    .values(**values)
                )
                if result.rowcount != 1:
                    raise CodingRunConflict("workspace revision changed")
        except IntegrityError as error:
            raise CodingRunConflict("coding run preparation conflict") from error

    async def update(self, value, *, expected_revision: int) -> None:
        record_type = WorkspaceRecord if isinstance(value, Workspace) else CodingRunRecord
        values = (
            self._workspace_values(value)
            if isinstance(value, Workspace)
            else self._run_values(value)
        )
        values.pop("id")
        async with self._database.transaction() as session:
            result = await session.execute(
                update(record_type)
                .where(record_type.id == value.id, record_type.revision == expected_revision)
                .values(**values)
            )
            if result.rowcount != 1:
                raise CodingRunConflict("agent runtime revision changed")

    async def get_by_run(self, run_id: UUID) -> SessionBinding | None:
        async with self._database.transaction() as session:
            record = await session.scalar(
                select(SessionBindingRecord).where(SessionBindingRecord.run_id == run_id)
            )
        return self._binding_domain(record) if record is not None else None

    @staticmethod
    def _workspace_values(value: Workspace) -> dict[str, object]:
        return {
            "id": value.id, "organization_id": value.organization_id,
            "project_id": value.project_id, "repository_id": value.repository_id,
            "task_id": value.task_id, "path": value.path, "base_sha": value.base_sha,
            "status": value.status.value, "bound_run_id": value.bound_run_id,
            "revision": value.revision, "created_at": value.created_at,
        }

    @staticmethod
    def _run_values(value: CodingRun) -> dict[str, object]:
        return {
            "id": value.id, "organization_id": value.organization_id,
            "project_id": value.project_id, "repository_id": value.repository_id,
            "task_id": value.task_id, "worker_agent_id": value.worker_agent_id,
            "adapter_id": value.adapter_id, "workspace_id": value.workspace_id,
            "context_bundle_id": value.context_bundle_id,
            "context_bundle_hash": value.context_bundle_hash,
            "coding_package_hash": value.coding_package_hash, "base_sha": value.base_sha,
            "instruction": value.instruction, "acceptance": list(value.acceptance),
            "required_tests": list(value.required_tests),
            "allowed_tools": list(value.allowed_tools),
            "allowed_paths": list(value.allowed_paths), "denied_paths": list(value.denied_paths),
            "network_policy": list(value.network_policy), "status": value.status.value,
            "attempt": value.attempt, "native_session_id": value.native_session_id,
            "revision": value.revision, "created_at": value.created_at,
        }

    @staticmethod
    def _binding_values(value: SessionBinding) -> dict[str, object]:
        return {
            "id": value.id, "run_id": value.run_id, "task_id": value.task_id,
            "adapter_id": value.adapter_id, "workspace_id": value.workspace_id,
            "context_bundle_hash": value.context_bundle_hash,
            "coding_package_hash": value.coding_package_hash,
            "native_session_id": value.native_session_id, "created_at": value.created_at,
        }

    @staticmethod
    def _workspace_domain(record: WorkspaceRecord) -> Workspace:
        return Workspace(
            id=record.id, organization_id=record.organization_id, project_id=record.project_id,
            repository_id=record.repository_id, task_id=record.task_id, path=record.path,
            base_sha=record.base_sha, status=WorkspaceStatus(record.status),
            bound_run_id=record.bound_run_id, revision=record.revision,
            created_at=_as_utc(record.created_at),
        )

    @staticmethod
    def _run_domain(record: CodingRunRecord) -> CodingRun:
        return CodingRun(
            id=record.id, organization_id=record.organization_id, project_id=record.project_id,
            repository_id=record.repository_id, task_id=record.task_id,
            worker_agent_id=record.worker_agent_id, adapter_id=record.adapter_id,
            workspace_id=record.workspace_id, context_bundle_id=record.context_bundle_id,
            context_bundle_hash=record.context_bundle_hash,
            coding_package_hash=record.coding_package_hash, base_sha=record.base_sha,
            instruction=record.instruction, acceptance=tuple(record.acceptance),
            required_tests=tuple(record.required_tests), allowed_tools=tuple(record.allowed_tools),
            allowed_paths=tuple(record.allowed_paths), denied_paths=tuple(record.denied_paths),
            network_policy=tuple(record.network_policy), status=CodingRunStatus(record.status),
            attempt=record.attempt, native_session_id=record.native_session_id,
            revision=record.revision, created_at=_as_utc(record.created_at),
        )

    @staticmethod
    def _binding_domain(record: SessionBindingRecord) -> SessionBinding:
        return SessionBinding(
            id=record.id, run_id=record.run_id, task_id=record.task_id,
            adapter_id=record.adapter_id, workspace_id=record.workspace_id,
            context_bundle_hash=record.context_bundle_hash,
            coding_package_hash=record.coding_package_hash,
            native_session_id=record.native_session_id,
            created_at=_as_utc(record.created_at),
        )
