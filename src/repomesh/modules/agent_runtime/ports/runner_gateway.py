from typing import Protocol
from uuid import UUID

from repomesh.modules.agent_runtime.contracts import CodingRunView


class RunnerGateway(Protocol):
    async def submit(self, run: CodingRunView) -> None: ...
    async def cancel(self, run_id: UUID) -> None: ...


class MockRunnerGateway:
    def __init__(self) -> None:
        self.submitted: list[CodingRunView] = []
        self.cancelled: list[UUID] = []

    async def submit(self, run: CodingRunView) -> None:
        self.submitted.append(run)

    async def cancel(self, run_id: UUID) -> None:
        self.cancelled.append(run_id)
