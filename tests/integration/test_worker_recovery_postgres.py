import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import delete

from repomesh.modules.agent_runtime.recovery import (
    PostgresWorkerRecoveryStore,
    WorkerRecoveryOperationRecord,
)
from repomesh.modules.task_orchestration.assignment import (
    AssignmentReason,
    PostgresTaskAssignmentStore,
    TaskAssignmentAttemptRecord,
)
from repomesh.modules.task_orchestration.domain import Task, TaskConflict
from repomesh.modules.task_orchestration.infrastructure import PostgresTaskStore, TaskRecord
from repomesh.persistence import Database

POSTGRES_URL = os.getenv("REPOMESH_TEST_DATABASE_URL") or os.getenv(
    "REPOMESH_TEST_POSTGRES_URL"
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="PostgreSQL test URL is not configured"),
]


@pytest.mark.asyncio
async def test_postgres_recovery_claim_and_reassignment_have_one_winner() -> None:
    assert POSTGRES_URL is not None
    database = Database(POSTGRES_URL)
    task = Task(
        organization_id=uuid4(), project_id=uuid4(), repository_id=uuid4(),
        assigned_by_agent_id=uuid4(), assignee_agent_id=uuid4(), title="Recover",
        instruction="Recover safely", acceptance=("completed",),
    )
    tasks = PostgresTaskStore(database)
    assignments = PostgresTaskAssignmentStore(database)
    recoveries = PostgresWorkerRecoveryStore(database)
    execution_id = uuid4()
    try:
        await tasks.add(
            task, idempotency_key=f"pg-recovery:{task.id}",
            request_fingerprint="sha256:" + "a" * 64,
        )
        initial_results = await asyncio.gather(
            *(assignments.ensure_initial(task.id) for _ in range(32))
        )
        assert len({item.id for item in initial_results}) == 1
        initial = initial_results[0]
        await recoveries.ensure(
            execution_id=execution_id, task_id=task.id,
            assignment_attempt_id=initial.id, assignment_generation=1,
            failed_worker_id=task.assignee_agent_id, reason="interrupted",
            native_session_id=None,
        )
        claims = await asyncio.gather(
            *(recoveries.claim(f"reconciler-{index}") for index in range(32))
        )
        assert len([item for item in claims if item is not None]) == 1

        replacements = [uuid4() for _ in range(16)]

        async def reassign(worker_id):
            try:
                return await assignments.reassign(
                    task.id, expected_task_version=task.version,
                    expected_generation=initial.generation,
                    replacement_worker_id=worker_id,
                    reason=AssignmentReason.WORKER_UNREACHABLE,
                )
            except TaskConflict:
                return None

        results = await asyncio.gather(*(reassign(worker) for worker in replacements))
        winners = [item for item in results if item is not None]
        assert len(winners) == 1
        assert winners[0].generation == 2
        history = await assignments.history(task.id)
        assert [item.generation for item in history] == [1, 2]
    finally:
        async with database.transaction() as session:
            await session.execute(
                delete(WorkerRecoveryOperationRecord).where(
                    WorkerRecoveryOperationRecord.execution_id == execution_id
                )
            )
            await session.execute(
                delete(TaskAssignmentAttemptRecord).where(
                    TaskAssignmentAttemptRecord.task_id == task.id
                )
            )
            await session.execute(delete(TaskRecord).where(TaskRecord.id == task.id))
        await database.dispose()
