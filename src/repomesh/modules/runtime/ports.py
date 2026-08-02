from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from repomesh.shared.domain import new_id


class RunStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CodingRunRequest:
    task_id: UUID
    repository_url: str
    instruction: str
    base_revision: str = "main"
    run_id: UUID = field(default_factory=new_id)


@dataclass(frozen=True, slots=True)
class CodingRunResult:
    run_id: UUID
    status: RunStatus
    summary: str
    changed_files: tuple[str, ...] = ()
    test_command: str | None = None


class CodingAgent(Protocol):
    name: str

    async def execute(self, request: CodingRunRequest) -> CodingRunResult: ...

