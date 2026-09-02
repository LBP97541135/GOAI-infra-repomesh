import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from repomesh.modules.platform_config import (
    BootstrapErrorCode,
    BootstrapPhase,
    BootstrapState,
    BootstrapTransitionError,
    PostgresBootstrapOperationStore,
)
from repomesh.modules.platform_config.bootstrap_store import BootstrapOperationRecord
from repomesh.persistence import Database


@pytest.mark.asyncio
async def test_bootstrap_operation_lifecycle_is_idempotent(application_container) -> None:
    store = PostgresBootstrapOperationStore(application_container.database)

    requested = await store.ensure_requested(requested_by=None)
    replay = await store.ensure_requested(requested_by=None)
    assert replay.id == requested.id
    assert requested.state is BootstrapState.PENDING
    assert requested.phase is BootstrapPhase.INSTALLING_AGENTTEAMS
    assert requested.attempt == 0

    claimed = await store.claim("reconciler-a", lease_seconds=60)
    assert claimed is not None
    assert claimed.id == requested.id
    assert claimed.state is BootstrapState.RUNNING
    assert claimed.attempt == 1
    assert claimed.lease_owner == "reconciler-a"
    assert await store.claim("reconciler-b", lease_seconds=60) is None

    renewed = await store.renew(claimed.id, "reconciler-a", lease_seconds=120)
    assert renewed.lease_expires_at is not None
    assert renewed.lease_expires_at > claimed.lease_expires_at

    installing = await store.transition(
        claimed.id,
        target=BootstrapState.RUNNING,
        phase=BootstrapPhase.INSTALLING_AGENTTEAMS,
        lease_owner="reconciler-a",
    )
    assert installing.phase is BootstrapPhase.INSTALLING_AGENTTEAMS

    failed = await store.transition(
        claimed.id,
        target=BootstrapState.RETRYABLE_FAILURE,
        phase=BootstrapPhase.INSTALLING_AGENTTEAMS,
        lease_owner="reconciler-a",
        error_code=BootstrapErrorCode.IMAGE_PULL_FAILED,
        error_detail="image registry was temporarily unavailable",
    )
    assert failed.error_code is BootstrapErrorCode.IMAGE_PULL_FAILED
    assert failed.lease_owner is None

    pending = await store.retry(claimed.id)
    assert pending.state is BootstrapState.PENDING
    assert pending.phase is BootstrapPhase.INSTALLING_AGENTTEAMS
    assert pending.attempt == 1
    reclaimed = await store.claim("reconciler-b", lease_seconds=60)
    assert reclaimed is not None
    assert reclaimed.attempt == 2

    completed = await store.transition(
        reclaimed.id,
        target=BootstrapState.COMPLETED,
        phase=BootstrapPhase.COMPLETE,
        lease_owner="reconciler-b",
    )
    assert completed.finished_at is not None
    assert completed.state is BootstrapState.COMPLETED

    next_operation = await store.ensure_requested(requested_by=None)
    assert next_operation.id != completed.id


@pytest.mark.asyncio
async def test_bootstrap_transition_requires_lease_owner(application_container) -> None:
    store = PostgresBootstrapOperationStore(application_container.database)
    requested = await store.ensure_requested(requested_by=None)

    with pytest.raises(BootstrapTransitionError, match="cannot transition"):
        await store.transition(
            requested.id,
            target=BootstrapState.COMPLETED,
            phase=BootstrapPhase.COMPLETE,
        )

    claimed = await store.claim("reconciler-a")
    assert claimed is not None
    with pytest.raises(BootstrapTransitionError, match="another lease"):
        await store.transition(
            claimed.id,
            target=BootstrapState.RETRYABLE_FAILURE,
            phase=BootstrapPhase.INSTALLING_AGENTTEAMS,
            lease_owner="reconciler-b",
        )

    with pytest.raises(ValueError, match="2000"):
        await store.transition(
            claimed.id,
            target=BootstrapState.RETRYABLE_FAILURE,
            phase=BootstrapPhase.INSTALLING_AGENTTEAMS,
            lease_owner="reconciler-a",
            error_detail="x" * 2001,
        )


@pytest.mark.asyncio
async def test_expired_bootstrap_lease_is_reclaimed(application_container) -> None:
    store = PostgresBootstrapOperationStore(application_container.database)
    requested = await store.ensure_requested(requested_by=None)
    claimed = await store.claim("reconciler-a", lease_seconds=60)
    assert claimed is not None

    async with application_container.database.transaction() as session:
        record = await session.get(BootstrapOperationRecord, requested.id)
        assert record is not None
        record.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    reclaimed = await store.claim("reconciler-b", lease_seconds=60)
    assert reclaimed is not None
    assert reclaimed.id == requested.id
    assert reclaimed.attempt == 2
    assert reclaimed.lease_owner == "reconciler-b"


@pytest.mark.asyncio
async def test_active_bootstrap_kind_is_unique(application_container) -> None:
    store = PostgresBootstrapOperationStore(application_container.database)
    first = await store.ensure_requested(requested_by=None)
    second = await store.ensure_requested(requested_by=None)

    async with application_container.database.transaction() as session:
        active = (
            await session.execute(
                BootstrapOperationRecord.__table__.select().where(
                    BootstrapOperationRecord.state.in_(
                        ("pending", "running", "waiting_for_user", "retryable_failure")
                    )
                )
            )
        ).all()
    assert first.id == second.id
    assert len(active) == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgres_concurrent_claim_has_one_winner() -> None:
    url = os.environ.get("REPOMESH_TEST_DATABASE_URL")
    if not url:
        pytest.skip("REPOMESH_TEST_DATABASE_URL is not set")
    database = Database(url)
    store = PostgresBootstrapOperationStore(database)
    try:
        async with database.transaction() as session:
            await session.execute(delete(BootstrapOperationRecord))
        await store.ensure_requested(requested_by=None)
        claims = await asyncio.gather(
            store.claim("reconciler-a"),
            store.claim("reconciler-b"),
        )
        winners = [claim for claim in claims if claim is not None]
        assert len(winners) == 1
        assert winners[0].attempt == 1
    finally:
        await database.dispose()
