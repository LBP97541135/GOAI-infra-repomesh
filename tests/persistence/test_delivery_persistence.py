from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio

from repomesh.modules.delivery.application import DeliveryService, SCMObservationService
from repomesh.modules.delivery.contracts import (
    PlanRecoveryCommand,
    PrepareChangeSetCommand,
    RecordSCMObservationCommand,
    RecoveryActionKind,
    RecoveryTrigger,
    RepositoryCandidateInput,
    SCMObservationSource,
    SCMObservationStatus,
)
from repomesh.modules.delivery.infrastructure import (
    PostgresChangeSetStore,
    PostgresSCMObservationStore,
)
from repomesh.persistence import Database
from repomesh.persistence.base import ALL_SCHEMAS


@pytest_asyncio.fixture
async def database(tmp_path: object) -> Database:
    database_path = tmp_path.joinpath("repomesh-delivery.db")
    instance = Database(
        f"sqlite+aiosqlite:///{database_path}",
        schema_translate_map={schema: None for schema in ALL_SCHEMAS},
    )
    await instance.create_all_for_tests()
    yield instance
    await instance.dispose()


@pytest.mark.asyncio
async def test_changeset_and_recovery_plan_survive_reload(database: Database) -> None:
    service = DeliveryService(PostgresChangeSetStore(database))
    repository_id = uuid4()
    created = await service.prepare(
        PrepareChangeSetCommand(
            organization_id=uuid4(),
            project_id=uuid4(),
            created_by_agent_id=uuid4(),
            title="Persistent delivery",
            validation_snapshot_id=uuid4(),
            candidates=(
                RepositoryCandidateInput(
                    repository_id=repository_id,
                    task_id=uuid4(),
                    commit_sha="a" * 40,
                    base_sha="b" * 40,
                    branch_name="repomesh/persistent",
                    required_checks=("unit-test", "integration-test"),
                ),
            ),
        ),
        idempotency_key="persistent-delivery",
    )
    await service.plan_recovery(
        PlanRecoveryCommand(
            change_set_id=created.id,
            trigger=RecoveryTrigger.RUNNER_INTERRUPTED,
            reason="runner host restarted",
            repository_id=repository_id,
            run_id=uuid4(),
            native_session_id="session-1",
        )
    )

    reloaded = await DeliveryService(PostgresChangeSetStore(database)).get(created.id)

    assert reloaded.recovery_plans[0].actions[0].kind is (
        RecoveryActionKind.RESUME_RUNNER_SESSION
    )
    assert reloaded.repositories[0].required_checks == (
        "unit-test",
        "integration-test",
    )
    resolved, resolved_repository_id = await DeliveryService(
        PostgresChangeSetStore(database)
    ).resolve_candidate(repository_id, "a" * 40)
    assert resolved.id == created.id
    assert resolved_repository_id == repository_id
    assert [item.id for item in await service.list_active()] == [created.id]


@pytest.mark.asyncio
async def test_failed_scm_observation_survives_restart_and_replays(
    database: Database,
) -> None:
    service = SCMObservationService(PostgresSCMObservationStore(database))
    recorded = await service.record(
        RecordSCMObservationCommand(
            provider="github",
            source=SCMObservationSource.WEBHOOK,
            external_id="delivery-persisted",
            event_type="check_run",
            payload={"action": "completed"},
            payload_hash="a" * 64,
            observed_at=datetime(2026, 8, 10, tzinfo=UTC),
        )
    )
    await service.claim(recorded.observation.id)
    await service.fail(recorded.observation.id, "process interrupted")

    restarted = SCMObservationService(PostgresSCMObservationStore(database))
    replayable = await restarted.list_replayable()
    claimed = await restarted.claim(recorded.observation.id)
    assert replayable[0].id == recorded.observation.id
    assert claimed is not None
    completed = await restarted.complete(recorded.observation.id)
    assert completed.status is SCMObservationStatus.PROCESSED
