from uuid import uuid4

import pytest

from repomesh.modules.delivery.application import DeliveryService
from repomesh.modules.delivery.contracts import (
    ChangeSetStatus,
    CIObservationCommand,
    MergeObservationCommand,
    PlanRecoveryCommand,
    PrepareChangeSetCommand,
    PullRequestObservationCommand,
    RecordRecoveryActionCommand,
    RecoveryActionKind,
    RecoveryActionStatus,
    RecoveryTrigger,
    RepositoryCandidateInput,
    RepositoryDeliveryStatus,
)
from repomesh.modules.delivery.domain import DeliveryConflict, DeliveryNotFound
from repomesh.modules.delivery.infrastructure import InMemoryChangeSetStore


def command():
    upstream = uuid4()
    downstream = uuid4()
    return PrepareChangeSetCommand(
        organization_id=uuid4(),
        project_id=uuid4(),
        created_by_agent_id=uuid4(),
        title="Pricing delivery",
        validation_snapshot_id=uuid4(),
        candidates=(
            RepositoryCandidateInput(
                repository_id=upstream,
                task_id=uuid4(),
                commit_sha="a" * 40,
                base_sha="1" * 40,
                branch_name="repomesh/pricing-api",
            ),
            RepositoryCandidateInput(
                repository_id=downstream,
                task_id=uuid4(),
                commit_sha="b" * 40,
                base_sha="2" * 40,
                branch_name="repomesh/pricing-web",
                depends_on=(upstream,),
            ),
        ),
    ), upstream, downstream


@pytest.mark.asyncio
async def test_changeset_enforces_dependency_merge_order() -> None:
    create, upstream, downstream = command()
    service = DeliveryService(InMemoryChangeSetStore())
    view = await service.prepare(create, idempotency_key="pricing")

    ordered_repositories = [
        item.repository_id
        for item in sorted(view.repositories, key=lambda item: item.merge_order)
    ]
    assert ordered_repositories == [
        upstream,
        downstream,
    ]
    for candidate, number in zip(create.candidates, (11, 12), strict=True):
        await service.observe_pull_request(
            PullRequestObservationCommand(
                change_set_id=view.id,
                repository_id=candidate.repository_id,
                pull_request_number=number,
                pull_request_url=f"https://example.test/pulls/{number}",
                head_sha=candidate.commit_sha,
            )
        )
        await service.observe_ci(
            CIObservationCommand(
                change_set_id=view.id,
                repository_id=candidate.repository_id,
                passed=True,
                check_run_id=f"ci-{number}",
                summary="all required checks passed",
            )
        )

    with pytest.raises(DeliveryConflict, match="upstream"):
        await service.observe_merge(
            MergeObservationCommand(view.id, downstream, "d" * 40)
        )
    await service.observe_merge(MergeObservationCommand(view.id, upstream, "c" * 40))
    completed = await service.observe_merge(
        MergeObservationCommand(view.id, downstream, "d" * 40)
    )

    assert completed.status is ChangeSetStatus.DELIVERED
    assert all(
        item.status is RepositoryDeliveryStatus.MERGED for item in completed.repositories
    )


@pytest.mark.asyncio
async def test_merge_gate_explains_ci_and_dependency_blocks() -> None:
    create, upstream, downstream = command()
    service = DeliveryService(InMemoryChangeSetStore())
    view = await service.prepare(create, idempotency_key="merge-gate")

    initial = await service.evaluate_merge_gate(view.id, downstream)
    assert not initial.allowed
    assert "required CI checks have not passed" in initial.reasons
    assert any("upstream repository" in reason for reason in initial.reasons)

    for candidate, number in zip(create.candidates, (31, 32), strict=True):
        await service.observe_pull_request(
            PullRequestObservationCommand(
                view.id,
                candidate.repository_id,
                number,
                f"https://example.test/pulls/{number}",
                candidate.commit_sha,
            )
        )
        await service.observe_ci(
            CIObservationCommand(
                view.id, candidate.repository_id, True, f"ci-{number}", "passed"
            )
        )
    blocked = await service.evaluate_merge_gate(view.id, downstream)
    assert not blocked.allowed
    assert blocked.reasons == (f"upstream repository is not merged: {upstream}",)

    await service.observe_merge(MergeObservationCommand(view.id, upstream, "c" * 40))
    allowed = await service.evaluate_merge_gate(view.id, downstream)
    assert allowed.allowed
    assert allowed.reasons == ()


@pytest.mark.asyncio
async def test_all_required_checks_must_pass_and_reruns_replace_old_results() -> None:
    repository_id = uuid4()
    service = DeliveryService(InMemoryChangeSetStore())
    view = await service.prepare(
        PrepareChangeSetCommand(
            organization_id=uuid4(),
            project_id=uuid4(),
            created_by_agent_id=uuid4(),
            title="Required check aggregation",
            validation_snapshot_id=uuid4(),
            candidates=(
                RepositoryCandidateInput(
                    repository_id=repository_id,
                    task_id=uuid4(),
                    commit_sha="a" * 40,
                    base_sha="b" * 40,
                    branch_name="repomesh/checks",
                    required_checks=("unit-test", "integration-test"),
                ),
            ),
        ),
        idempotency_key="required-checks",
    )
    await service.observe_pull_request(
        PullRequestObservationCommand(
            view.id,
            repository_id,
            41,
            "https://example.test/pulls/41",
            "a" * 40,
        )
    )

    unrelated = await service.observe_ci(
        CIObservationCommand(
            view.id, repository_id, True, "ci-1", "lint passed", "lint"
        )
    )
    assert unrelated.repositories[0].status is RepositoryDeliveryStatus.CI_PENDING
    unit = await service.observe_ci(
        CIObservationCommand(
            view.id, repository_id, True, "ci-2", "unit passed", "unit-test"
        )
    )
    assert unit.repositories[0].status is RepositoryDeliveryStatus.CI_PENDING
    failed = await service.observe_ci(
        CIObservationCommand(
            view.id,
            repository_id,
            False,
            "ci-3",
            "integration failed",
            "integration-test",
        )
    )
    assert failed.repositories[0].status is RepositoryDeliveryStatus.CI_FAILED
    recovered = await service.observe_ci(
        CIObservationCommand(
            view.id,
            repository_id,
            True,
            "ci-4",
            "integration passed",
            "integration-test",
        )
    )
    candidate = recovered.repositories[0]
    assert candidate.status is RepositoryDeliveryStatus.READY_TO_MERGE
    assert len(candidate.ci_checks) == 3
    assert next(
        check for check in candidate.ci_checks if check.check_name == "integration-test"
    ).check_run_id == "ci-4"

    repeated = await service.observe_ci(
        CIObservationCommand(
            view.id,
            repository_id,
            True,
            "ci-4",
            "integration passed",
            "integration-test",
        )
    )
    assert repeated.version == recovered.version


@pytest.mark.asyncio
async def test_candidate_resolution_requires_exactly_one_active_changeset() -> None:
    store = InMemoryChangeSetStore()
    service = DeliveryService(store)
    create, upstream, _ = command()
    first = await service.prepare(create, idempotency_key="resolve-first")

    resolved, repository_id = await service.resolve_candidate(upstream, "a" * 40)
    assert resolved.id == first.id
    assert repository_id == upstream

    await service.prepare(create, idempotency_key="resolve-second")
    with pytest.raises(DeliveryConflict, match="multiple active"):
        await service.resolve_candidate(upstream, "a" * 40)
    with pytest.raises(DeliveryNotFound, match="no active"):
        await service.resolve_candidate(upstream, "f" * 40)


@pytest.mark.asyncio
async def test_partial_merge_compensation_is_reverse_dependency_order() -> None:
    create, upstream, downstream = command()
    service = DeliveryService(InMemoryChangeSetStore())
    view = await service.prepare(create, idempotency_key="partial")
    for candidate, number in zip(create.candidates, (21, 22), strict=True):
        await service.observe_pull_request(
            PullRequestObservationCommand(
                view.id,
                candidate.repository_id,
                number,
                f"https://example.test/pulls/{number}",
                candidate.commit_sha,
            )
        )
        await service.observe_ci(
            CIObservationCommand(
                view.id, candidate.repository_id, True, f"ci-{number}", "passed"
            )
        )
    await service.observe_merge(MergeObservationCommand(view.id, upstream, "c" * 40))

    recovered = await service.plan_recovery(
        PlanRecoveryCommand(
            change_set_id=view.id,
            trigger=RecoveryTrigger.PARTIAL_MERGE,
            reason="downstream merge failed",
        )
    )
    actions = recovered.recovery_plans[-1].actions

    assert actions[0].repository_id == downstream
    assert actions[0].kind is RecoveryActionKind.CLOSE_PULL_REQUEST
    assert actions[1].repository_id == upstream
    assert actions[1].kind is RecoveryActionKind.CREATE_REVERT_PULL_REQUEST
    assert actions[2].kind is RecoveryActionKind.MERGE_REVERT_PULL_REQUEST
    assert actions[-1].kind is RecoveryActionKind.REVALIDATE_CHANGESET

    current = recovered
    for action in actions:
        current = await service.record_recovery_action(
            RecordRecoveryActionCommand(
                change_set_id=view.id,
                recovery_plan_id=current.recovery_plans[-1].id,
                action_id=action.id,
                status=RecoveryActionStatus.SUCCEEDED,
                detail="completed",
            )
        )
    assert current.status is ChangeSetStatus.COMPENSATED


@pytest.mark.asyncio
async def test_runner_recovery_prefers_resume_only_with_confirmed_session() -> None:
    create, upstream, _ = command()
    service = DeliveryService(InMemoryChangeSetStore())
    view = await service.prepare(create, idempotency_key="runner-recovery")
    run_id = uuid4()

    retry = await service.plan_recovery(
        PlanRecoveryCommand(
            change_set_id=view.id,
            trigger=RecoveryTrigger.RUNNER_FAILED,
            reason="session never opened",
            repository_id=upstream,
            run_id=run_id,
        )
    )
    resume = await service.plan_recovery(
        PlanRecoveryCommand(
            change_set_id=view.id,
            trigger=RecoveryTrigger.RUNNER_INTERRUPTED,
            reason="host restarted",
            repository_id=upstream,
            run_id=run_id,
            native_session_id="claude-session-1",
        )
    )

    assert retry.recovery_plans[-1].actions[0].kind is RecoveryActionKind.RETRY_RUNNER
    assert resume.recovery_plans[-1].actions[0].kind is RecoveryActionKind.RESUME_RUNNER_SESSION
