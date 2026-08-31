from datetime import UTC, datetime
from uuid import uuid4

import pytest

from repomesh.modules.review_validation import (
    DatabaseValidationResult,
    DatabaseValidationStage,
    DatabaseValidationStatus,
    PostgresDatabaseBranchValidationStore,
)
from repomesh.modules.review_validation.domain import DatabaseBranchValidation


@pytest.mark.asyncio
async def test_database_validation_evidence_round_trips(application_container) -> None:
    store = PostgresDatabaseBranchValidationStore(application_container.database)
    now = datetime.now(UTC)
    run = DatabaseBranchValidation(
        organization_id=uuid4(),
        project_id=uuid4(),
        repository_id=uuid4(),
        candidate_sha="a" * 40,
        source_database_ref="production-snapshot-policy",
        provider="polar-agentic-database",
        provider_branch_ref="branch-42",
        engine_version="PolarDB-PG-16",
        request_hash="b" * 64,
        idempotency_key="db-validation-persistence",
        status=DatabaseValidationStatus.CLEANED,
        results=(
            DatabaseValidationResult(
                DatabaseValidationStage.MIGRATION, "upgrade", 0, "passed"
            ),
        ),
        created_at=now,
        updated_at=now,
    )

    reserved, created = await store.reserve(run)
    replay, replay_created = await store.reserve(run)
    loaded = await store.get(run.id)

    assert created and not replay_created
    assert replay.id == reserved.id
    assert loaded == run
    assert "postgresql://" not in repr(loaded)
