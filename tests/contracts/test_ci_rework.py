from uuid import uuid4

import pytest

from repomesh.integrations.scm.rework import CIReworkTaskCreator
from repomesh.modules.task_orchestration.contracts import (
    CreateCIReworkTaskCommand,
    TaskOrigin,
)


class Tasks:
    def __init__(self) -> None:
        self.calls = []

    async def assign(self, command, *, idempotency_key, origin=TaskOrigin.PLANNED):
        self.calls.append((command, idempotency_key, origin))
        return command


@pytest.mark.asyncio
async def test_ci_rework_uses_stable_idempotency_and_worker_assignment() -> None:
    tasks = Tasks()
    creator = CIReworkTaskCreator(tasks)
    change_set_id = uuid4()
    repository_id = uuid4()
    manager_id = uuid4()
    worker_id = uuid4()

    await creator.create(
        CreateCIReworkTaskCommand(
            uuid4(),
            uuid4(),
            change_set_id,
            repository_id,
            manager_id,
            worker_id,
            uuid4(),
            "a" * 40,
            "unit test failed",
            ("unit passes",),
        )
    )

    assigned, key, origin = tasks.calls[0]
    assert assigned.assigned_by_agent_id == manager_id
    assert assigned.assignee_agent_id == worker_id
    assert assigned.parent_task_id is not None
    assert key == f"ci-rework:{change_set_id}:{repository_id}:{'a' * 40}"
    # The rework fact is declared, not left to be inferred from the title.
    assert origin is TaskOrigin.REWORK
