import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from repomesh.modules.agent_runtime import (
    PostgresWorkerExecutionReservationStore,
    WorkerCapacityUnavailable,
    WorkerExecutionReservationConflict,
    WorkerExecutionReservationRecord,
)
from repomesh.persistence import Database
from repomesh.persistence.base import ALL_SCHEMAS


@pytest.fixture
async def reservation_store(tmp_path):
    database = Database(
        f"sqlite+aiosqlite:///{tmp_path / 'reservations.db'}",
        schema_translate_map={schema: None for schema in ALL_SCHEMAS},
    )
    await database.create_all_for_tests()
    try:
        yield PostgresWorkerExecutionReservationStore(database)
    finally:
        await database.dispose()


def _binding(*, task_id=None, worker_id=None, project_id=None):
    return {
        "organization_id": uuid4(),
        "project_id": project_id or uuid4(),
        "repository_id": uuid4(),
        "task_id": task_id or uuid4(),
        "worker_agent_id": worker_id or uuid4(),
        "lease_seconds": 60,
    }


@pytest.mark.asyncio
async def test_concurrent_reservation_returns_one_run(reservation_store) -> None:
    binding = _binding()
    results = await asyncio.gather(
        *(
            reservation_store.reserve(
                **binding,
                lease_owner=f"api-{index}",
            )
            for index in range(16)
        )
    )

    assert sum(result.created for result in results) == 1
    assert len({result.reservation.run_id for result in results}) == 1


@pytest.mark.asyncio
async def test_worker_slot_is_global_across_projects(reservation_store) -> None:
    worker_id = uuid4()
    first = await reservation_store.reserve(
        **_binding(worker_id=worker_id), lease_owner="api-a"
    )

    with pytest.raises(WorkerCapacityUnavailable, match="active execution"):
        await reservation_store.reserve(
            **_binding(worker_id=worker_id), lease_owner="api-b"
        )

    await reservation_store.fail_preparation(
        first.reservation.id,
        "controlled release",
        lease_owner="api-a",
        fencing_version=first.reservation.version,
    )
    second = await reservation_store.reserve(
        **_binding(worker_id=worker_id), lease_owner="api-b"
    )
    assert second.created is True


@pytest.mark.asyncio
async def test_bind_requires_current_owner_and_fencing_version(reservation_store) -> None:
    reserved = await reservation_store.reserve(
        **_binding(), lease_owner="api-a"
    )

    with pytest.raises(WorkerExecutionReservationConflict, match="ownership"):
        await reservation_store.bind_payload(
            reserved.reservation.id,
            {"runId": str(reserved.reservation.run_id)},
            lease_owner="api-b",
            fencing_version=reserved.reservation.version,
        )

    running = await reservation_store.bind_payload(
        reserved.reservation.id,
        {"runId": str(reserved.reservation.run_id)},
        lease_owner="api-a",
        fencing_version=reserved.reservation.version,
    )
    assert running.task_payload == {"runId": str(reserved.reservation.run_id)}
    assert running.version == reserved.reservation.version + 1


@pytest.mark.asyncio
async def test_expired_preparation_is_reclaimed_with_new_run(reservation_store) -> None:
    binding = _binding()
    first = await reservation_store.reserve(
        **binding, lease_owner="api-old"
    )
    async with reservation_store._database.transaction() as session:
        record = await session.get(
            WorkerExecutionReservationRecord, first.reservation.id
        )
        assert record is not None
        record.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    second = await reservation_store.reserve(
        **binding, lease_owner="api-new"
    )
    assert second.created is True
    assert second.reservation.run_id != first.reservation.run_id
    assert second.reservation.attempt == 2

    with pytest.raises(WorkerExecutionReservationConflict, match="ownership"):
        await reservation_store.bind_payload(
            first.reservation.id,
            {"runId": str(first.reservation.run_id)},
            lease_owner="api-old",
            fencing_version=first.reservation.version,
        )
