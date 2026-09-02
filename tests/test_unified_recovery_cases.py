import asyncio
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from repomesh.integrations.recovery import RecoverySourceProjector, UnifiedRecoveryActionHandlers
from repomesh.modules.agent_runtime.recovery import (
    PostgresWorkerRecoveryStore,
    WorkerRecoveryDecision,
    WorkerRecoveryState,
)
from repomesh.modules.delivery.conflicts import (
    DeliveryConflictKind,
    PostgresDeliveryConflictCaseStore,
)
from repomesh.modules.delivery.contracts import RecoveryActionStatus
from repomesh.modules.project.contracts import HumanReviewStatus
from repomesh.modules.recovery_management import (
    PostgresRecoveryCaseStore,
    RecoveryAction,
    RecoveryActionExecutor,
    RecoveryCaseConflict,
    RecoveryCaseStatus,
    RecoveryCaseUpsert,
    RecoverySeverity,
    RecoverySourceType,
)
from repomesh.modules.task_orchestration.domain import Task
from repomesh.modules.task_orchestration.infrastructure import PostgresTaskStore
from repomesh.persistence import Database
from repomesh.persistence.base import ALL_SCHEMAS


@pytest.fixture
async def recovery_store(tmp_path):
    database = Database(
        f"sqlite+aiosqlite:///{tmp_path / 'unified-recovery.db'}",
        schema_translate_map={schema: None for schema in ALL_SCHEMAS},
    )
    await database.create_all_for_tests()
    try:
        yield database, PostgresRecoveryCaseStore(database)
    finally:
        await database.dispose()


def _case_command(source_id=None, evidence="e1"):
    return RecoveryCaseUpsert(
        source_type=RecoverySourceType.WORKER_EXECUTION,
        source_id=source_id or uuid4(), organization_id=uuid4(), project_id=uuid4(),
        repository_id=uuid4(), task_id=uuid4(), evidence_version=evidence,
        summary="Worker recovery required", severity=RecoverySeverity.CRITICAL,
        available_actions=(RecoveryAction.REASSIGN_WORKER, RecoveryAction.MANUAL_RESOLUTION),
    )


@pytest.mark.asyncio
async def test_source_replay_updates_one_case_and_fences_old_decision(recovery_store) -> None:
    _, store = recovery_store
    command = _case_command(uuid4(), "e1")
    first = await store.ensure_case(command)
    replay = await store.ensure_case(command)
    assert replay.id == first.id and replay.version == first.version

    updated = await store.ensure_case(replace(command, evidence_version="e2"))
    assert updated.id == first.id and updated.version == first.version + 1
    with pytest.raises(RecoveryCaseConflict, match="evidence changed"):
        await store.decide(
            first.id, expected_version=first.version, evidence_version="e1",
            action=RecoveryAction.REASSIGN_WORKER,
            decided_by_human_id=uuid4(), reason="use another Worker",
        )


@pytest.mark.asyncio
async def test_concurrent_decisions_have_one_winner_and_operation_is_leased(recovery_store) -> None:
    _, store = recovery_store
    case = await store.ensure_case(_case_command())

    async def decide():
        try:
            return await store.decide(
                case.id, expected_version=case.version,
                evidence_version=case.evidence_version,
                action=RecoveryAction.REASSIGN_WORKER,
                decided_by_human_id=uuid4(), reason="recover",
            )
        except RecoveryCaseConflict:
            return None

    results = await asyncio.gather(*(decide() for _ in range(16)))
    winners = [item for item in results if item is not None]
    assert len(winners) == 1
    operation = await store.claim_operation("executor-a", lease_seconds=60)
    assert operation is not None and operation.id == winners[0][1].id
    assert await store.claim_operation("executor-b", lease_seconds=60) is None
    finished = await store.finish_operation(operation.id, "executor-a", succeeded=True)
    assert finished.state == "succeeded"
    assert (await store.get(case.id)).status is RecoveryCaseStatus.VERIFYING


@pytest.mark.asyncio
async def test_projector_unifies_worker_and_delivery_sources_and_resolves_them(
    recovery_store,
) -> None:
    database, cases = recovery_store
    tasks = PostgresTaskStore(database)
    task = Task(
        organization_id=uuid4(), project_id=uuid4(), repository_id=uuid4(),
        assigned_by_agent_id=uuid4(), assignee_agent_id=uuid4(), title="Recover",
        instruction="Recover", acceptance=("done",),
    )
    await tasks.add(
        task, idempotency_key=f"task:{task.id}", request_fingerprint="sha256:" + "a" * 64
    )
    worker_store = PostgresWorkerRecoveryStore(database)
    await worker_store.ensure(
        execution_id=uuid4(), task_id=task.id, assignment_attempt_id=None,
        assignment_generation=1, failed_worker_id=task.assignee_agent_id,
        reason="interrupted", native_session_id=None,
    )
    conflict_store = PostgresDeliveryConflictCaseStore(database)
    conflict = await conflict_store.ensure(
        change_set_id=uuid4(), organization_id=task.organization_id,
        project_id=task.project_id, repository_id=task.repository_id,
        candidate_head_sha="a" * 40, kind=DeliveryConflictKind.BASE_DRIFT,
        expected_base_sha="b" * 40, observed_base_sha="c" * 40,
        detail="target moved",
    )
    projector = RecoverySourceProjector(cases, worker_store, conflict_store, tasks)
    await projector.run_once()
    projected = await cases.list_cases(project_id=task.project_id)
    assert {item.source_type for item in projected} == {
        RecoverySourceType.WORKER_EXECUTION,
        RecoverySourceType.DELIVERY_CONFLICT,
    }

    claimed = await worker_store.claim("worker-reconciler")
    assert claimed is not None
    await worker_store.finish(claimed.id, "worker-reconciler", WorkerRecoveryDecision.NO_ACTION)
    await conflict_store.resolve_for_revision(
        conflict.change_set_id, conflict.repository_id, conflict.candidate_head_sha
    )
    await projector.run_once()
    resolved = await cases.list_cases(
        project_id=task.project_id, status=RecoveryCaseStatus.RESOLVED
    )
    assert len(resolved) == 2


@pytest.mark.asyncio
async def test_approved_worker_reassignment_runs_through_leased_executor(
    recovery_store,
) -> None:
    database, cases = recovery_store
    worker_store = PostgresWorkerRecoveryStore(database)
    source = await worker_store.ensure(
        execution_id=uuid4(), task_id=uuid4(), assignment_attempt_id=None,
        assignment_generation=1, failed_worker_id=uuid4(), reason="worker_unreachable",
        native_session_id=None,
    )
    case = await cases.ensure_case(
        RecoveryCaseUpsert(
            source_type=RecoverySourceType.WORKER_EXECUTION, source_id=source.id,
            organization_id=uuid4(), project_id=uuid4(), task_id=source.task_id,
            evidence_version=f"worker:{source.id}:g1", summary="Worker unavailable",
            severity=RecoverySeverity.CRITICAL,
            available_actions=(RecoveryAction.REASSIGN_WORKER,),
        )
    )
    await cases.decide(
        case.id, expected_version=case.version, evidence_version=case.evidence_version,
        action=RecoveryAction.REASSIGN_WORKER, decided_by_human_id=uuid4(),
        reason="Move work to a healthy teammate",
    )
    handlers = UnifiedRecoveryActionHandlers(
        cases, worker_store, PostgresDeliveryConflictCaseStore(database)
    )
    executor = RecoveryActionExecutor(
        cases, handlers.handlers(), owner="executor-a"
    )

    assert await executor.run_once() is True
    updated_source = next(item for item in await worker_store.list_all() if item.id == source.id)
    assert updated_source.state is WorkerRecoveryState.PENDING
    assert updated_source.decision is WorkerRecoveryDecision.REASSIGN


@pytest.mark.asyncio
async def test_projector_includes_human_reviews_and_delivery_recovery(recovery_store) -> None:
    database, cases = recovery_store
    project_id, organization_id, review_id, recovery_id = (
        uuid4(), uuid4(), uuid4(), uuid4()
    )
    review = SimpleNamespace(
        id=review_id, project_id=project_id, repository_id=uuid4(),
        evidence_version="review-e1", title="Choose recovery action",
        status=HumanReviewStatus.PENDING,
    )
    action = SimpleNamespace(id=uuid4(), status=RecoveryActionStatus.PENDING)
    plan = SimpleNamespace(id=recovery_id, reason="partial merge", actions=(action,))
    change_set = SimpleNamespace(
        id=uuid4(), organization_id=organization_id, project_id=project_id,
        recovery_plans=(plan,),
    )

    class Reviews:
        async def list_all(self): return (review,)

    class Topologies:
        async def get_view(self, candidate):
            return (
                SimpleNamespace(organization_id=organization_id)
                if candidate == project_id
                else None
            )

    class Delivery:
        async def list_active(self): return (change_set,)

    projector = RecoverySourceProjector(
        cases,
        PostgresWorkerRecoveryStore(database),
        PostgresDeliveryConflictCaseStore(database),
        PostgresTaskStore(database),
        delivery=Delivery(), reviews=Reviews(), topologies=Topologies(),
    )
    await projector.run_once()
    projected = await cases.list_cases(project_id=project_id)
    assert {item.source_type for item in projected} == {
        RecoverySourceType.HUMAN_REVIEW,
        RecoverySourceType.DELIVERY_RECOVERY,
    }
