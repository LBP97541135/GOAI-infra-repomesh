import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete

from repomesh.modules.delivery import (
    DeliveryConflict,
    DeliveryService,
    PostgresChangeSetStore,
    PostgresSCMCommandStore,
    SCMCommandService,
)
from repomesh.modules.delivery.contracts import (
    EnqueueSCMCommand,
    PrepareChangeSetCommand,
    RepositoryCandidateInput,
    SCMCommandKind,
)
from repomesh.modules.delivery.infrastructure import ChangeSetRecord, SCMCommandRecord
from repomesh.persistence import Database

POSTGRES_URL = os.getenv("REPOMESH_TEST_DATABASE_URL") or os.getenv(
    "REPOMESH_TEST_POSTGRES_URL"
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="PostgreSQL test URL is not configured"),
]


async def _queued_command(database: Database):
    repository_id = uuid4()
    delivery = DeliveryService(PostgresChangeSetStore(database))
    change_set = await delivery.prepare(
        PrepareChangeSetCommand(
            organization_id=uuid4(),
            project_id=uuid4(),
            created_by_agent_id=uuid4(),
            title="SCM lease concurrency",
            validation_snapshot_id=uuid4(),
            candidates=(
                RepositoryCandidateInput(
                    repository_id=repository_id,
                    task_id=uuid4(),
                    commit_sha="a" * 40,
                    base_sha="b" * 40,
                    branch_name="repomesh/lease-test",
                ),
            ),
        ),
        idempotency_key=f"lease-test:{uuid4()}",
    )
    commands = SCMCommandService(PostgresSCMCommandStore(database), lease_seconds=60)
    queued = await commands.enqueue(
        EnqueueSCMCommand(
            change_set.id,
            repository_id,
            SCMCommandKind.MERGE_PULL_REQUEST,
            f"lease-command:{uuid4()}",
            {"pull_request_number": 1, "expected_head_sha": "a" * 40},
        )
    )
    return commands, queued, change_set.id


@pytest.mark.asyncio
async def test_postgres_atomic_claim_has_one_winner_and_fences_loser() -> None:
    assert POSTGRES_URL is not None
    database = Database(POSTGRES_URL)
    command_id = None
    change_set_id = None
    try:
        commands, queued, change_set_id = await _queued_command(database)
        command_id = queued.id
        claims = await asyncio.gather(
            *(commands.claim_batch(f"dispatcher-{index}", limit=1) for index in range(32))
        )
        winners = [batch[0] for batch in claims if batch]
        assert len(winners) == 1
        winner = winners[0]
        assert winner.id == queued.id

        with pytest.raises(DeliveryConflict, match="ownership"):
            await commands.accept(winner.id, "stale-dispatcher", winner.version)

        accepted = await commands.accept(winner.id, winner.lease_owner or "", winner.version)
        assert accepted.lease_owner is None
    finally:
        async with database.transaction() as session:
            if command_id is not None:
                await session.execute(
                    delete(SCMCommandRecord).where(SCMCommandRecord.id == command_id)
                )
            if change_set_id is not None:
                await session.execute(
                    delete(ChangeSetRecord).where(ChangeSetRecord.id == change_set_id)
                )
        await database.dispose()


@pytest.mark.asyncio
async def test_postgres_expired_claim_is_reclaimed_and_old_owner_is_fenced() -> None:
    assert POSTGRES_URL is not None
    database = Database(POSTGRES_URL)
    command_id = None
    change_set_id = None
    try:
        commands, queued, change_set_id = await _queued_command(database)
        command_id = queued.id
        first = (await commands.claim_batch("dispatcher-old", limit=1))[0]

        async with database.transaction() as session:
            record = await session.get(SCMCommandRecord, first.id)
            assert record is not None
            record.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)

        reclaimed = (await commands.claim_batch("dispatcher-new", limit=1))[0]
        assert reclaimed.id == first.id
        assert reclaimed.attempts == first.attempts + 1
        assert reclaimed.version == first.version + 1

        with pytest.raises(DeliveryConflict, match="ownership"):
            await commands.accept(first.id, "dispatcher-old", first.version)

        renewed = await commands.renew(
            reclaimed.id, "dispatcher-new", reclaimed.version
        )
        assert renewed.version == reclaimed.version
        await commands.fail(
            reclaimed.id,
            "controlled acceptance failure",
            "dispatcher-new",
            reclaimed.version,
        )
    finally:
        async with database.transaction() as session:
            if command_id is not None:
                await session.execute(
                    delete(SCMCommandRecord).where(SCMCommandRecord.id == command_id)
                )
            if change_set_id is not None:
                await session.execute(
                    delete(ChangeSetRecord).where(ChangeSetRecord.id == change_set_id)
                )
        await database.dispose()
