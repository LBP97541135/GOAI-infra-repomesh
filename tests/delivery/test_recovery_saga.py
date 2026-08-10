from uuid import uuid4

import pytest

from repomesh.integrations.scm.recovery import RecoverySagaExecutor, RevertConflict
from repomesh.modules.delivery import DeliveryService, InMemoryChangeSetStore
from repomesh.modules.delivery.contracts import (
    CIObservationCommand,
    MergeObservationCommand,
    PlanRecoveryCommand,
    PrepareChangeSetCommand,
    PullRequestObservationCommand,
    RecoveryActionKind,
    RecoveryActionStatus,
    RecoveryTrigger,
    RepositoryCandidateInput,
)


class Handler:
    def __init__(self, *, conflict: bool = False) -> None:
        self.calls = []
        self.conflict = conflict

    async def execute(self, context):
        self.calls.append(context.action.kind)
        if self.conflict:
            raise RevertConflict("revert conflict")
        return f"completed {context.action.kind.value}"


class ConflictTasks:
    def __init__(self) -> None:
        self.task_id = uuid4()

    async def create_for_conflict(self, context, error):
        return self.task_id


async def merged_changeset():
    repository_id = uuid4()
    service = DeliveryService(InMemoryChangeSetStore())
    change_set = await service.prepare(
        PrepareChangeSetCommand(
            uuid4(),
            uuid4(),
            uuid4(),
            "Rollback delivery",
            uuid4(),
            (
                RepositoryCandidateInput(
                    repository_id,
                    uuid4(),
                    "a" * 40,
                    "b" * 40,
                    "repomesh/rollback",
                ),
            ),
        ),
        idempotency_key="rollback-delivery",
    )
    await service.observe_pull_request(
        PullRequestObservationCommand(
            change_set.id, repository_id, 10, "https://example.test/pr/10", "a" * 40
        )
    )
    await service.observe_ci(
        CIObservationCommand(change_set.id, repository_id, True, "ci", "passed")
    )
    await service.observe_merge(MergeObservationCommand(change_set.id, repository_id, "d" * 40))
    recovered = await service.plan_recovery(
        PlanRecoveryCommand(
            change_set.id,
            RecoveryTrigger.PARTIAL_MERGE,
            "downstream delivery failed",
        )
    )
    return service, recovered.id


@pytest.mark.asyncio
async def test_saga_executes_one_action_per_pass_in_strict_sequence() -> None:
    service, change_set_id = await merged_changeset()
    handler = Handler()
    saga = RecoverySagaExecutor(service, handler)

    await saga.run_once()
    first = await service.get(change_set_id)
    assert handler.calls == [RecoveryActionKind.CREATE_REVERT_PULL_REQUEST]
    assert first.recovery_plans[-1].actions[0].status is RecoveryActionStatus.SUCCEEDED

    await saga.run_once()
    assert handler.calls[-1] is RecoveryActionKind.MERGE_REVERT_PULL_REQUEST
    await saga.run_once()
    completed = await service.get(change_set_id)
    assert handler.calls[-1] is RecoveryActionKind.REVALIDATE_CHANGESET
    assert completed.status.value == "compensated"


@pytest.mark.asyncio
async def test_revert_conflict_waits_for_worker_without_advancing_saga() -> None:
    service, change_set_id = await merged_changeset()
    tasks = ConflictTasks()
    saga = RecoverySagaExecutor(service, Handler(conflict=True), tasks)

    await saga.run_once()
    waiting = await service.get(change_set_id)

    action = waiting.recovery_plans[-1].actions[0]
    assert action.status is RecoveryActionStatus.WAITING_WORKER
    assert str(tasks.task_id) in action.detail
    await saga.run_once()
    still_waiting = await service.get(change_set_id)
    assert still_waiting.recovery_plans[-1].actions[1].status is RecoveryActionStatus.PENDING
