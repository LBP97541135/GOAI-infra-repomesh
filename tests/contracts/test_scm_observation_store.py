from datetime import UTC, datetime, timedelta

import pytest

from repomesh.modules.delivery import (
    DeliveryConflict,
    InMemorySCMObservationStore,
    SCMObservationService,
)
from repomesh.modules.delivery.contracts import (
    RecordSCMObservationCommand,
    SCMObservationSource,
    SCMObservationStatus,
)


def command(payload_hash: str = "a" * 64) -> RecordSCMObservationCommand:
    return RecordSCMObservationCommand(
        provider="github",
        source=SCMObservationSource.WEBHOOK,
        external_id="delivery-1",
        event_type="check_run",
        payload={"action": "completed"},
        payload_hash=payload_hash,
        observed_at=datetime(2026, 8, 10, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_processed_external_fact_is_append_only_and_idempotent() -> None:
    service = SCMObservationService(InMemorySCMObservationStore())
    recorded = await service.record(command())
    claimed = await service.claim(recorded.observation.id)
    assert claimed is not None
    completed = await service.complete(recorded.observation.id)

    repeated = await service.record(command())

    assert completed.status is SCMObservationStatus.PROCESSED
    assert not repeated.created
    assert repeated.observation.id == recorded.observation.id
    assert await service.claim(recorded.observation.id) is None


@pytest.mark.asyncio
async def test_failed_external_fact_is_retained_and_replayable() -> None:
    service = SCMObservationService(InMemorySCMObservationStore())
    recorded = await service.record(command())
    await service.claim(recorded.observation.id)
    failed = await service.fail(recorded.observation.id, "temporary routing failure")

    replayable = await service.list_replayable()
    claimed_again = await service.claim(recorded.observation.id)

    assert failed.status is SCMObservationStatus.FAILED
    assert replayable[0].id == recorded.observation.id
    assert claimed_again is not None
    assert claimed_again.attempts == 2


@pytest.mark.asyncio
async def test_stale_processing_lease_can_be_reclaimed_after_restart() -> None:
    clock = [datetime(2026, 8, 10, tzinfo=UTC)]
    service = SCMObservationService(
        InMemorySCMObservationStore(),
        now=lambda: clock[0],
        lease_timeout=timedelta(minutes=5),
    )
    recorded = await service.record(command())
    await service.claim(recorded.observation.id)

    clock[0] += timedelta(minutes=6)

    assert [item.id for item in await service.list_replayable()] == [
        recorded.observation.id
    ]
    reclaimed = await service.claim(recorded.observation.id)
    assert reclaimed is not None
    assert reclaimed.attempts == 2


@pytest.mark.asyncio
async def test_external_identity_cannot_be_reused_for_another_payload() -> None:
    service = SCMObservationService(InMemorySCMObservationStore())
    await service.record(command())

    with pytest.raises(DeliveryConflict, match="reused"):
        await service.record(command("b" * 64))
