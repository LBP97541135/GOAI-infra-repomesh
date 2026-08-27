import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete

from repomesh.modules.agent_runtime import (
    PostgresWorkerExecutionReservationStore,
    WorkerCapacityUnavailable,
    WorkerExecutionReservationRecord,
)
from repomesh.persistence import Database

POSTGRES_URL = os.getenv("REPOMESH_TEST_DATABASE_URL") or os.getenv(
    "REPOMESH_TEST_POSTGRES_URL"
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="PostgreSQL test URL is not configured"),
]


@pytest.mark.asyncio
async def test_postgres_concurrent_task_reservation_has_one_run() -> None:
    assert POSTGRES_URL is not None
    database = Database(POSTGRES_URL)
    store = PostgresWorkerExecutionReservationStore(database)
    task_id = uuid4()
    worker_id = uuid4()
    binding = {
        "organization_id": uuid4(),
        "project_id": uuid4(),
        "repository_id": uuid4(),
        "task_id": task_id,
        "worker_agent_id": worker_id,
        "lease_seconds": 60,
    }
    try:
        results = await asyncio.gather(
            *(
                store.reserve(**binding, lease_owner=f"api-{index}")
                for index in range(32)
            )
        )
        assert sum(result.created for result in results) == 1
        assert len({result.reservation.run_id for result in results}) == 1

        with pytest.raises(WorkerCapacityUnavailable, match="active execution"):
            await store.reserve(
                organization_id=binding["organization_id"],
                project_id=uuid4(),
                repository_id=uuid4(),
                task_id=uuid4(),
                worker_agent_id=worker_id,
                lease_owner="another-project",
                lease_seconds=60,
            )

        async with database.transaction() as session:
            record = await session.scalar(
                WorkerExecutionReservationRecord.__table__.select().where(
                    WorkerExecutionReservationRecord.task_id == task_id
                )
            )
            assert record is not None
            await session.execute(
                WorkerExecutionReservationRecord.__table__.update()
                .where(WorkerExecutionReservationRecord.task_id == task_id)
                .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )

        recovered = await store.reserve(
            **binding,
            lease_owner="recovery-api",
        )
        assert recovered.created is True
        assert recovered.reservation.attempt == 2
        assert recovered.reservation.run_id not in {
            result.reservation.run_id for result in results
        }
    finally:
        async with database.transaction() as session:
            await session.execute(
                delete(WorkerExecutionReservationRecord).where(
                    WorkerExecutionReservationRecord.worker_agent_id == worker_id
                )
            )
        await database.dispose()
