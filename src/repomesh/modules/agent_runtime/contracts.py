from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class CodingRunFinished:
    run_id: UUID
    task_id: UUID
    status: str
