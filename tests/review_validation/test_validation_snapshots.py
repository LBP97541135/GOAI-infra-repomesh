from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from repomesh.modules.delivery import DeliveryService, InMemoryChangeSetStore
from repomesh.modules.delivery.contracts import (
    CIObservationCommand,
    PrepareChangeSetCommand,
    PullRequestObservationCommand,
    RepositoryCandidateInput,
)
from repomesh.modules.review_validation import (
    CreateValidationSnapshotCommand,
    InMemoryValidationSnapshotStore,
    ValidationSnapshotService,
    ValidationTestInput,
)


@pytest.mark.asyncio
async def test_snapshot_hash_is_canonical_and_bound_to_candidate_heads() -> None:
    repository_id = uuid4()
    store = InMemoryValidationSnapshotStore()
    service = ValidationSnapshotService(store)
    command = CreateValidationSnapshotCommand(
        uuid4(),
        uuid4(),
        uuid4(),
        {repository_id: "a" * 40},
        (ValidationTestInput(repository_id, "pytest", 0, "passed"),),
        {"python": "3.12", "runner": "v1"},
    )

    first = await service.create(command)
    reordered = await service.create(
        CreateValidationSnapshotCommand(
            command.organization_id,
            command.project_id,
            command.specification_version_id,
            command.candidate_heads,
            command.tests,
            {"runner": "v1", "python": "3.12"},
        )
    )

    assert first.environment_hash == reordered.environment_hash
    assert (
        await service.validate_for_delivery(first.id, command.project_id, {repository_id: "a" * 40})
    ).valid
    stale = await service.validate_for_delivery(
        first.id, command.project_id, {repository_id: "b" * 40}
    )
    assert "current candidate heads" in stale.reasons[0]


@pytest.mark.asyncio
async def test_merge_gate_rejects_expired_validation_snapshot() -> None:
    repository_id = uuid4()
    validation_store = InMemoryValidationSnapshotStore()
    validation = ValidationSnapshotService(validation_store)
    snapshot = await validation.create(
        CreateValidationSnapshotCommand(
            uuid4(),
            uuid4(),
            None,
            {repository_id: "a" * 40},
            (ValidationTestInput(repository_id, "pytest", 0),),
            {"runner": "v1"},
        )
    )
    stored = validation_store.items[snapshot.id]
    validation_store.items[snapshot.id] = type(stored)(
        **{
            name: getattr(stored, name)
            for name in stored.__dataclass_fields__
            if name != "expires_at"
        },
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    delivery = DeliveryService(
        InMemoryChangeSetStore(),
        require_validation=True,
        validation_reader=validation,
    )
    change_set = await delivery.prepare(
        PrepareChangeSetCommand(
            uuid4(),
            snapshot.project_id,
            uuid4(),
            "Expired evidence",
            snapshot.id,
            (
                RepositoryCandidateInput(
                    repository_id,
                    uuid4(),
                    "a" * 40,
                    "b" * 40,
                    "repomesh/validation",
                ),
            ),
        ),
        idempotency_key="expired-validation",
    )
    await delivery.observe_pull_request(
        PullRequestObservationCommand(
            change_set.id, repository_id, 1, "https://example.test/pr/1", "a" * 40
        )
    )
    await delivery.observe_ci(
        CIObservationCommand(change_set.id, repository_id, True, "ci", "passed")
    )

    gate = await delivery.evaluate_merge_gate(change_set.id, repository_id)
    assert "validation snapshot has expired" in gate.reasons
