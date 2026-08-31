"""Runner gateway behaviour that only a real PostgreSQL can prove.

The suite in ``tests/integrations/runner`` drives the same store over SQLite,
where three things silently do not exist: ``FOR UPDATE SKIP LOCKED`` is parsed
and ignored, ``JSONB`` is plain ``JSON`` text, and the unique constraints are
checked by a different engine. Those are exactly the mechanisms the dispatch
ledger relies on to stop two Workers from taking the same run and to make a
redelivered task a no-op, so they are asserted here against the real database.
"""

import asyncio
import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from repomesh.modules.agent_runtime.runner_store import (
    PostgresRunnerGatewayStore,
    RunnerDispatchRecord,
    RunnerGatewayConflict,
)
from repomesh.persistence import Database
from repomesh_runner.contracts import (
    ContextBundleRef,
    RepositoryCheckout,
    RunnerPermissions,
    RunnerTask,
)

POSTGRES_URL = os.getenv("REPOMESH_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="REPOMESH_TEST_POSTGRES_URL is not configured"),
]

SHA = "sha256:" + "a" * 64


def _dispatch_payload(worker_agent_id: UUID, *, idempotency_key: str) -> dict[str, object]:
    return RunnerTask(
        organization_id=uuid4(),
        project_id=uuid4(),
        task_id=uuid4(),
        run_id=uuid4(),
        correlation_id=uuid4(),
        attempt=1,
        adapter_id="claude",
        instruction="Read task context and implement",
        repository=RepositoryCheckout(uuid4(), "https://example/repo.git", "main"),
        context_bundle=ContextBundleRef(uuid4(), 1, "file:///manifest.json", SHA),
        permissions=RunnerPermissions(),
        idempotency_key=idempotency_key,
        issued_at=datetime.now(UTC),
        worker_agent_id=worker_agent_id,
    ).to_wire()


@pytest.mark.asyncio
async def test_a_locked_dispatch_is_skipped_instead_of_waited_on() -> None:
    """One Worker holding a row must not make the next poll block on it.

    ``lease_next`` asks for ``SKIP LOCKED``. If that clause were ever dropped,
    this poll would wait for the holder's transaction instead of returning, and
    a long-polling Runner would hang for the whole lease. SQLite cannot show
    the difference: it accepts ``FOR UPDATE`` and does nothing with it.
    """

    assert POSTGRES_URL is not None
    database = Database(POSTGRES_URL)
    store = PostgresRunnerGatewayStore(database)
    worker_agent_id = uuid4()
    payload = _dispatch_payload(worker_agent_id, idempotency_key=f"lock-{uuid4().hex}")
    try:
        await store.enqueue(payload)

        async with database.transaction() as holder:
            held = await holder.scalar(
                select(RunnerDispatchRecord)
                .where(RunnerDispatchRecord.run_id == UUID(str(payload["runId"])))
                .with_for_update()
            )
            assert held is not None
            # A second poll on another connection: skipped, not queued behind.
            skipped = await asyncio.wait_for(store.lease_next(worker_agent_id), timeout=10)
            assert skipped is None

        # And the row was genuinely leasable all along -- the None above came
        # from the lock, not from a query that never matched anything.
        leased = await asyncio.wait_for(store.lease_next(worker_agent_id), timeout=10)
        assert leased is not None
        assert leased["runId"] == payload["runId"]
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_a_redelivered_dispatch_survives_the_jsonb_round_trip() -> None:
    """Replay is decided by comparing the stored payload with the new one.

    On PostgreSQL the payload goes through ``JSONB``, which normalises what it
    stores -- key order and number formatting are not preserved as written. If
    the round trip changed the document, a redelivered task would stop looking
    like a replay and start looking like a key collision.
    """

    assert POSTGRES_URL is not None
    database = Database(POSTGRES_URL)
    store = PostgresRunnerGatewayStore(database)
    worker_agent_id = uuid4()
    idempotency_key = f"replay-{uuid4().hex}"
    payload = _dispatch_payload(worker_agent_id, idempotency_key=idempotency_key)
    try:
        await store.enqueue(payload)
        await store.enqueue(payload)  # the redelivery: silently accepted

        stored = await store.get_dispatch(UUID(str(payload["runId"])))
        assert stored is not None
        assert stored.task_payload == payload

        different = _dispatch_payload(worker_agent_id, idempotency_key=idempotency_key)
        with pytest.raises(RunnerGatewayConflict, match="different task"):
            await store.enqueue(different)
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_two_events_cannot_share_one_sequence_number() -> None:
    """``uq_runner_events_sequence`` is what makes the event log ordered.

    Two different events claiming the same position must not both land. The
    store answers that with ``False`` rather than an exception, because the
    sender is a Runner retrying, not a caller making a mistake.
    """

    assert POSTGRES_URL is not None
    database = Database(POSTGRES_URL)
    store = PostgresRunnerGatewayStore(database)
    worker_agent_id = uuid4()
    payload = _dispatch_payload(worker_agent_id, idempotency_key=f"seq-{uuid4().hex}")
    try:
        await store.enqueue(payload)

        def event(event_type: str) -> dict[str, object]:
            return {
                "schemaVersion": "runtime.v1",
                "eventId": str(uuid4()),
                "eventType": event_type,
                "organizationId": payload["organizationId"],
                "projectId": payload["projectId"],
                "taskId": payload["taskId"],
                "runId": payload["runId"],
                "correlationId": payload["correlationId"],
                "attempt": 1,
                "sequence": 1,
                "occurredAt": datetime.now(UTC).isoformat(),
                "payload": {"summary": "runner reported"},
            }

        assert await store.record_event(event("runner.accepted")) is True
        assert await store.record_event(event("runner.completed")) is False

        # The rejected event left nothing behind, and the accepted one still
        # moved the dispatch: a rolled-back insert must not roll back the lease.
        stored = await store.get_dispatch(UUID(str(payload["runId"])))
        assert stored is not None
        assert stored.status == "accepted"
    finally:
        await database.dispose()
