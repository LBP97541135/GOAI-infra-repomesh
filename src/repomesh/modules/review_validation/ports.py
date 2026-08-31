from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from .contracts import DatabaseValidationCommand, DatabaseValidationResult
from .domain import DatabaseBranchValidation, ValidationSnapshot


class ValidationSnapshotStore(Protocol):
    async def add(self, snapshot: ValidationSnapshot) -> None: ...

    async def get(self, snapshot_id: UUID) -> ValidationSnapshot | None: ...

    async def list_by_project(self, project_id: UUID) -> tuple[ValidationSnapshot, ...]: ...


class DatabaseBranchValidationStore(Protocol):
    async def reserve(
        self, run: DatabaseBranchValidation
    ) -> tuple[DatabaseBranchValidation, bool]: ...

    async def update(self, run: DatabaseBranchValidation) -> None: ...

    async def get(self, run_id: UUID) -> DatabaseBranchValidation | None: ...


class DatabaseValidationEvidenceReader(Protocol):
    async def get(self, run_id: UUID): ...


@dataclass(frozen=True, slots=True)
class ProvisionedDatabaseBranch:
    branch_ref: str
    engine_version: str


class DatabaseBranchProvider(Protocol):
    name: str

    async def create_branch(
        self, *, source_database_ref: str, idempotency_key: str
    ) -> ProvisionedDatabaseBranch: ...

    async def execute(
        self, branch: ProvisionedDatabaseBranch, command: DatabaseValidationCommand
    ) -> DatabaseValidationResult: ...

    async def delete_branch(self, branch_ref: str) -> None: ...
