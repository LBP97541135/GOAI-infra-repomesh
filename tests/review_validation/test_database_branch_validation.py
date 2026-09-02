from uuid import uuid4

import pytest

from repomesh.modules.review_validation import (
    CreateValidationSnapshotCommand,
    DatabaseBranchValidationConflict,
    DatabaseBranchValidationService,
    DatabaseValidationCommand,
    DatabaseValidationResult,
    DatabaseValidationStage,
    DatabaseValidationStatus,
    InMemoryDatabaseBranchValidationStore,
    InMemoryValidationSnapshotStore,
    ProvisionedDatabaseBranch,
    StartDatabaseBranchValidation,
    ValidationSnapshotService,
    ValidationTestInput,
)


class RecordingProvider:
    name = "test-agentic-db"

    def __init__(self, *, failures: set[str] | None = None, cleanup_failures: int = 0):
        self.failures = failures or set()
        self.cleanup_failures = cleanup_failures
        self.created = 0
        self.executed: list[str] = []
        self.deleted: list[str] = []

    async def create_branch(self, *, source_database_ref: str, idempotency_key: str):
        self.created += 1
        return ProvisionedDatabaseBranch("branch-42", "PolarDB-PG-compatible-16")

    async def execute(self, branch, command):
        self.executed.append(command.name)
        return DatabaseValidationResult(
            command.stage,
            command.name,
            1 if command.name in self.failures else 0,
            "failed" if command.name in self.failures else "passed",
        )

    async def delete_branch(self, branch_ref: str):
        self.deleted.append(branch_ref)
        if self.cleanup_failures:
            self.cleanup_failures -= 1
            raise RuntimeError("provider detail must not be stored")


def request(*, key: str = "db-validation-1", sha: str = "a" * 40):
    return StartDatabaseBranchValidation(
        organization_id=uuid4(),
        project_id=uuid4(),
        repository_id=uuid4(),
        candidate_sha=sha,
        source_database_ref="production-snapshot-policy",
        commands=(
            DatabaseValidationCommand(
                DatabaseValidationStage.MIGRATION, "upgrade", "alembic:head"
            ),
            DatabaseValidationCommand(
                DatabaseValidationStage.BACKFILL, "orders-backfill", "job:orders-v2"
            ),
            DatabaseValidationCommand(
                DatabaseValidationStage.VERIFICATION, "contract-check", "suite:database"
            ),
        ),
        idempotency_key=key,
    )


@pytest.mark.asyncio
async def test_success_runs_in_order_cleans_and_replays_once() -> None:
    store = InMemoryDatabaseBranchValidationStore()
    provider = RecordingProvider()
    service = DatabaseBranchValidationService(store, provider)
    command = request()

    first = await service.start(command)
    replay = await service.start(command)

    assert replay.id == first.id
    assert provider.created == 1
    assert provider.executed == ["upgrade", "orders-backfill", "contract-check"]
    assert provider.deleted == ["branch-42"]
    assert first.status is DatabaseValidationStatus.CLEANED
    assert service.is_delivery_evidence(first)


@pytest.mark.asyncio
async def test_idempotency_key_cannot_name_another_request() -> None:
    store = InMemoryDatabaseBranchValidationStore()
    provider = RecordingProvider()
    service = DatabaseBranchValidationService(store, provider)
    command = request()
    await service.start(command)

    with pytest.raises(DatabaseBranchValidationConflict):
        await service.start(
            StartDatabaseBranchValidation(
                organization_id=command.organization_id,
                project_id=command.project_id,
                repository_id=command.repository_id,
                candidate_sha="b" * 40,
                source_database_ref=command.source_database_ref,
                commands=command.commands,
                idempotency_key=command.idempotency_key,
            )
        )


@pytest.mark.asyncio
async def test_failed_migration_stops_later_stages_but_still_cleans() -> None:
    provider = RecordingProvider(failures={"upgrade"})
    service = DatabaseBranchValidationService(
        InMemoryDatabaseBranchValidationStore(), provider
    )

    view = await service.start(request())

    assert provider.executed == ["upgrade"]
    assert provider.deleted == ["branch-42"]
    assert view.status is DatabaseValidationStatus.CLEANED
    assert view.failure_code == "migration_command_failed"
    assert not service.is_delivery_evidence(view)


@pytest.mark.asyncio
async def test_cleanup_failure_is_durable_and_retry_does_not_repeat_validation() -> None:
    provider = RecordingProvider(cleanup_failures=1)
    service = DatabaseBranchValidationService(
        InMemoryDatabaseBranchValidationStore(), provider
    )
    view = await service.start(request())

    assert view.status is DatabaseValidationStatus.CLEANING
    assert view.cleanup_pending
    assert not service.is_delivery_evidence(view)

    cleaned = await service.retry_cleanup(view.id)
    assert cleaned.status is DatabaseValidationStatus.CLEANED
    assert not cleaned.cleanup_pending
    assert service.is_delivery_evidence(cleaned)
    assert provider.executed == ["upgrade", "orders-backfill", "contract-check"]
    assert provider.deleted == ["branch-42", "branch-42"]


def test_stage_order_and_required_stages_are_validated() -> None:
    command = request()
    invalid = StartDatabaseBranchValidation(
        organization_id=command.organization_id,
        project_id=command.project_id,
        repository_id=command.repository_id,
        candidate_sha=command.candidate_sha,
        source_database_ref=command.source_database_ref,
        commands=tuple(reversed(command.commands)),
        idempotency_key=command.idempotency_key,
    )
    service = DatabaseBranchValidationService(
        InMemoryDatabaseBranchValidationStore(), RecordingProvider()
    )
    with pytest.raises(ValueError, match="stage order"):
        service._validate(invalid)


@pytest.mark.asyncio
async def test_cleaned_database_run_can_be_bound_to_validation_snapshot() -> None:
    database_store = InMemoryDatabaseBranchValidationStore()
    database_service = DatabaseBranchValidationService(database_store, RecordingProvider())
    command = request()
    database_run = await database_service.start(command)
    snapshots = ValidationSnapshotService(
        InMemoryValidationSnapshotStore(), database_store
    )

    snapshot = await snapshots.create(
        CreateValidationSnapshotCommand(
            organization_id=command.organization_id,
            project_id=command.project_id,
            specification_version_id=None,
            candidate_heads={command.repository_id: command.candidate_sha},
            tests=(ValidationTestInput(command.repository_id, "pytest", 0),),
            environment={"runner": "v1"},
            database_validation_ids=(database_run.id,),
        )
    )
    assert snapshot.database_validation_ids == (database_run.id,)

    with pytest.raises(ValueError, match="candidate head"):
        await snapshots.create(
            CreateValidationSnapshotCommand(
                organization_id=command.organization_id,
                project_id=command.project_id,
                specification_version_id=None,
                candidate_heads={command.repository_id: "b" * 40},
                tests=(ValidationTestInput(command.repository_id, "pytest", 0),),
                environment={"runner": "v1"},
                database_validation_ids=(database_run.id,),
            )
        )
