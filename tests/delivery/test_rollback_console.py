"""Whole-ChangeSet rollback from the console (GUI batch E-1, contract §4.6).

The console's promise is narrow and the tests hold it to exactly that: pressing
"roll back" records a decision that closes the merge gate and hands a planned
action sequence to the recovery Saga. It does not revert anything itself, and
it never claims a clean restore.
"""

import asyncio
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

# tests/delivery is not a package, so this is the sibling module pytest put on
# sys.path — the console governance suite, whose principal/plan builders this
# one reuses rather than growing a second set that can drift from it.
from test_console_endpoints import StubDirectory, _plan, _principal

from repomesh.bootstrap import create_app
from repomesh.bootstrap.container import ApplicationContainer
from repomesh.modules.agent_directory.contracts import AgentPrincipalStatus, AgentRole
from repomesh.modules.delivery import (
    DeliveryConflict,
    DeliveryDenied,
    DeliveryNotFound,
    DeliveryRollbackService,
    DeliveryService,
    InMemoryChangeSetStore,
    InMemoryDeliveryAuditLog,
    delivery_change_set_key,
)
from repomesh.modules.delivery.contracts import (
    ChangeSetStatus,
    CIObservationCommand,
    GovernanceDecisionKind,
    MergeObservationCommand,
    PlanRecoveryCommand,
    PrepareChangeSetCommand,
    PullRequestObservationCommand,
    RecordMergeRequestedCommand,
    RecordRecoveryActionCommand,
    RecoveryActionKind,
    RecoveryActionStatus,
    RecoveryTrigger,
    RepositoryCandidateInput,
    RequestChangeSetRollbackCommand,
)
from repomesh.modules.task_orchestration.contracts import ExecutionPlanStatus
from repomesh.settings import get_settings

FIRST_HEAD = "a" * 40
SECOND_HEAD = "c" * 40
BASE = "b" * 40
MERGE_SHA = "d" * 40


async def _two_repository_change_set(
    delivery: DeliveryService,
    *,
    delivery_id: UUID,
    organization_id: UUID,
    project_id: UUID,
    first: UUID,
    second: UUID,
):
    """`first` merges before `second`: the rollback must undo them the other way."""

    return await delivery.prepare(
        PrepareChangeSetCommand(
            organization_id=organization_id,
            project_id=project_id,
            created_by_agent_id=uuid4(),
            title="Console delivery",
            validation_snapshot_id=None,
            candidates=(
                RepositoryCandidateInput(
                    repository_id=first,
                    task_id=uuid4(),
                    commit_sha=FIRST_HEAD,
                    base_sha=BASE,
                    branch_name="repomesh/first",
                    required_approvals=0,
                ),
                RepositoryCandidateInput(
                    repository_id=second,
                    task_id=uuid4(),
                    commit_sha=SECOND_HEAD,
                    base_sha=BASE,
                    branch_name="repomesh/second",
                    depends_on=(first,),
                    required_approvals=0,
                ),
            ),
        ),
        idempotency_key=delivery_change_set_key(delivery_id),
    )


async def rollback_scenario():
    """One merged repository, one still sitting on an open PR."""

    organization_id = uuid4()
    project_id = uuid4()
    merged_repository = uuid4()
    open_repository = uuid4()
    delivery_id = uuid4()
    org_leader = _principal(organization_id=organization_id, role=AgentRole.ORGANIZATION_LEADER)
    repo_leader = _principal(
        organization_id=organization_id,
        role=AgentRole.REPOSITORY_LEADER,
        repository_id=merged_repository,
    )
    worker = _principal(organization_id=organization_id, role=AgentRole.WORKER)
    foreign_leader = _principal(organization_id=uuid4(), role=AgentRole.ORGANIZATION_LEADER)
    disabled_leader = _principal(
        organization_id=organization_id,
        role=AgentRole.ORGANIZATION_LEADER,
        status=AgentPrincipalStatus.DISABLED,
    )
    delivery = DeliveryService(InMemoryChangeSetStore(), require_governance=True)
    change_set = await _two_repository_change_set(
        delivery,
        delivery_id=delivery_id,
        organization_id=organization_id,
        project_id=project_id,
        first=merged_repository,
        second=open_repository,
    )
    await delivery.observe_pull_request(
        PullRequestObservationCommand(
            change_set.id, merged_repository, 1, "https://example.test/pr/1", FIRST_HEAD
        )
    )
    await delivery.observe_ci(
        CIObservationCommand(change_set.id, merged_repository, True, "ci-1", "passed")
    )
    await delivery.record_merge_requested(
        RecordMergeRequestedCommand(change_set.id, merged_repository, FIRST_HEAD)
    )
    await delivery.observe_merge(
        MergeObservationCommand(change_set.id, merged_repository, MERGE_SHA)
    )
    await delivery.observe_pull_request(
        PullRequestObservationCommand(
            change_set.id, open_repository, 2, "https://example.test/pr/2", SECOND_HEAD
        )
    )
    audit = InMemoryDeliveryAuditLog()
    service = DeliveryRollbackService(
        delivery,
        StubDirectory(org_leader, repo_leader, worker, foreign_leader, disabled_leader),
        audit,
    )
    return {
        "service": service,
        "delivery": delivery,
        "audit": audit,
        "delivery_id": delivery_id,
        "change_set_id": change_set.id,
        "merged_repository": merged_repository,
        "open_repository": open_repository,
        "org_leader": org_leader,
        "repo_leader": repo_leader,
        "worker": worker,
        "foreign_leader": foreign_leader,
        "disabled_leader": disabled_leader,
    }


def _command(change_set_id: UUID, agent_id: UUID, reason: str = "bad data migration shipped"):
    return RequestChangeSetRollbackCommand(
        change_set_id=change_set_id,
        reason=reason,
        requested_by_agent_id=agent_id,
    )


@pytest.mark.asyncio
async def test_rollback_closes_every_merge_gate_and_plans_reverse_order() -> None:
    scenario = await rollback_scenario()
    service: DeliveryRollbackService = scenario["service"]
    delivery: DeliveryService = scenario["delivery"]

    receipt = await service.request(
        scenario["delivery_id"],
        _command(scenario["change_set_id"], scenario["org_leader"].id),
        idempotency_key="console-rollback",
    )

    # One head-bound decision per candidate — the whole set, not a repository.
    assert {decision.repository_id for decision in receipt.decisions} == {
        scenario["merged_repository"],
        scenario["open_repository"],
    }
    assert all(
        decision.decision is GovernanceDecisionKind.ROLLBACK_REQUIRED
        for decision in receipt.decisions
    )
    assert receipt.replayed is False

    # The gate is what actually stops another merge; assert on the gate, not on
    # the decision row that is supposed to close it.
    gate = await delivery.evaluate_merge_gate(
        scenario["change_set_id"], scenario["open_repository"]
    )
    assert gate.allowed is False
    assert any(reason.startswith("governance requires rollback") for reason in gate.reasons)

    plan = receipt.recovery_plan
    assert plan.trigger is RecoveryTrigger.OPERATOR_REQUESTED
    # Reverse merge order: the repository that merged last is undone first, and
    # the set is revalidated only after every repository is dealt with.
    assert [(action.kind, action.repository_id) for action in plan.actions] == [
        (RecoveryActionKind.CLOSE_PULL_REQUEST, scenario["open_repository"]),
        (RecoveryActionKind.CREATE_REVERT_PULL_REQUEST, scenario["merged_repository"]),
        (RecoveryActionKind.MERGE_REVERT_PULL_REQUEST, scenario["merged_repository"]),
        (RecoveryActionKind.REVALIDATE_CHANGESET, None),
    ]
    assert [action.sequence for action in plan.actions] == [1, 2, 3, 4]
    assert (await delivery.get(scenario["change_set_id"])).status is ChangeSetStatus.COMPENSATING
    assert [event.event_type for event in scenario["audit"].events] == [
        "DeliveryRollbackRequested"
    ]


@pytest.mark.asyncio
async def test_preview_matches_the_plan_the_saga_will_run() -> None:
    """The dialog's table and the Saga's plan come from the same generator."""

    scenario = await rollback_scenario()
    delivery: DeliveryService = scenario["delivery"]
    command = PlanRecoveryCommand(
        change_set_id=scenario["change_set_id"],
        trigger=RecoveryTrigger.OPERATOR_REQUESTED,
        reason="rollback scope preview",
    )

    preview = await delivery.preview_recovery(command)
    assert (await delivery.get(scenario["change_set_id"])).recovery_plans == ()

    planned = (await delivery.plan_recovery(command)).recovery_plans[-1].actions
    assert [(item.sequence, item.kind, item.repository_id) for item in preview] == [
        (item.sequence, item.kind, item.repository_id) for item in planned
    ]


@pytest.mark.asyncio
async def test_replaying_the_same_rollback_writes_nothing() -> None:
    scenario = await rollback_scenario()
    service: DeliveryRollbackService = scenario["service"]
    delivery: DeliveryService = scenario["delivery"]
    command = _command(scenario["change_set_id"], scenario["org_leader"].id)

    first = await service.request(
        scenario["delivery_id"], command, idempotency_key="console-rollback"
    )
    version = (await delivery.get(scenario["change_set_id"])).version
    second = await service.request(
        scenario["delivery_id"], command, idempotency_key="console-rollback"
    )

    assert second.replayed is True
    assert second.recovery_plan.id == first.recovery_plan.id
    assert (await delivery.get(scenario["change_set_id"])).version == version
    assert len(scenario["audit"].events) == 1
    assert len((await delivery.get(scenario["change_set_id"])).recovery_plans) == 1


@pytest.mark.asyncio
async def test_a_running_recovery_plan_refuses_a_second_rollback_without_writing() -> None:
    scenario = await rollback_scenario()
    service: DeliveryRollbackService = scenario["service"]
    delivery: DeliveryService = scenario["delivery"]
    await service.request(
        scenario["delivery_id"],
        _command(scenario["change_set_id"], scenario["org_leader"].id),
        idempotency_key="console-rollback",
    )
    version = (await delivery.get(scenario["change_set_id"])).version

    with pytest.raises(DeliveryConflict, match="already running"):
        await service.request(
            scenario["delivery_id"],
            _command(
                scenario["change_set_id"],
                scenario["org_leader"].id,
                reason="changed my mind about the reason",
            ),
            idempotency_key="console-rollback-2",
        )

    # The refusal must be total: a 409 that had already written half the
    # decisions would leave the gate closed by a rollback nobody planned.
    assert (await delivery.get(scenario["change_set_id"])).version == version
    assert len(scenario["audit"].events) == 1


@pytest.mark.asyncio
async def test_a_finished_recovery_plan_allows_another_rollback() -> None:
    scenario = await rollback_scenario()
    service: DeliveryRollbackService = scenario["service"]
    delivery: DeliveryService = scenario["delivery"]
    first = await service.request(
        scenario["delivery_id"],
        _command(scenario["change_set_id"], scenario["org_leader"].id),
        idempotency_key="console-rollback",
    )
    for action in first.recovery_plan.actions:
        await delivery.record_recovery_action(
            RecordRecoveryActionCommand(
                change_set_id=scenario["change_set_id"],
                recovery_plan_id=first.recovery_plan.id,
                action_id=action.id,
                status=RecoveryActionStatus.SUCCEEDED,
                detail="done",
            )
        )

    second = await service.request(
        scenario["delivery_id"],
        _command(scenario["change_set_id"], scenario["org_leader"].id, reason="second attempt"),
        idempotency_key="console-rollback-2",
    )
    assert second.recovery_plan.id != first.recovery_plan.id
    assert second.replayed is False


@pytest.mark.asyncio
async def test_only_an_active_organization_leader_may_roll_back_the_whole_set() -> None:
    scenario = await rollback_scenario()
    service: DeliveryRollbackService = scenario["service"]

    for denied in ("repo_leader", "worker", "foreign_leader", "disabled_leader"):
        with pytest.raises(DeliveryDenied):
            await service.request(
                scenario["delivery_id"],
                _command(scenario["change_set_id"], scenario[denied].id),
                idempotency_key=f"denied-{denied}",
            )

    with pytest.raises(DeliveryDenied):
        await service.request(
            scenario["delivery_id"],
            _command(scenario["change_set_id"], uuid4()),
            idempotency_key="denied-unknown",
        )


@pytest.mark.asyncio
async def test_unknown_delivery_change_set_and_blank_input_are_rejected() -> None:
    scenario = await rollback_scenario()
    service: DeliveryRollbackService = scenario["service"]

    with pytest.raises(DeliveryNotFound):
        await service.request(
            uuid4(),
            _command(scenario["change_set_id"], scenario["org_leader"].id),
            idempotency_key="unknown-delivery",
        )
    with pytest.raises(DeliveryConflict, match="does not belong"):
        await service.request(
            scenario["delivery_id"],
            _command(uuid4(), scenario["org_leader"].id),
            idempotency_key="wrong-change-set",
        )
    with pytest.raises(ValueError, match="idempotency_key"):
        await service.request(
            scenario["delivery_id"],
            _command(scenario["change_set_id"], scenario["org_leader"].id),
            idempotency_key="  ",
        )
    with pytest.raises(ValueError, match="reason"):
        await service.request(
            scenario["delivery_id"],
            _command(scenario["change_set_id"], scenario["org_leader"].id, reason="   "),
            idempotency_key="blank-reason",
        )


def test_rollback_endpoint_and_scope_projection_over_http(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", "internal-secret")
    get_settings.cache_clear()
    try:
        organization_id = uuid4()
        project_id = uuid4()
        merged_repository = uuid4()
        open_repository = uuid4()
        plan = _plan(
            organization_id=organization_id,
            project_id=project_id,
            repository_id=merged_repository,
            status=ExecutionPlanStatus.IN_PROGRESS,
        )
        # A second delivery that never prepared a ChangeSet: the dialog has to
        # be able to say "nothing published" without the endpoint 404-ing.
        bare_plan = _plan(
            organization_id=organization_id,
            project_id=uuid4(),
            repository_id=uuid4(),
            status=ExecutionPlanStatus.IN_PROGRESS,
        )
        # A third one that prepared candidates but never opened a PR: rows
        # exist, yet there is still nothing to undo.
        unpublished_plan = _plan(
            organization_id=organization_id,
            project_id=uuid4(),
            repository_id=uuid4(),
            status=ExecutionPlanStatus.IN_PROGRESS,
        )
        delivery = application_container.delivery_service()

        async def seed():
            from repomesh.modules.agent_directory.application import (
                CreateAgent,
                CreateAgentRequest,
            )

            created = await CreateAgent(application_container.agent_directory).execute(
                CreateAgentRequest(
                    organization_id=organization_id,
                    role=AgentRole.ORGANIZATION_LEADER,
                    agentteams_resource_name="rollback-org-leader",
                ),
                idempotency_key="rollback-org-leader",
            )
            await application_container.execution_plan_store().add(
                plan, idempotency_key="rollback-plan"
            )
            change_set = await _two_repository_change_set(
                delivery,
                delivery_id=plan.id,
                organization_id=organization_id,
                project_id=project_id,
                first=merged_repository,
                second=open_repository,
            )
            await delivery.observe_pull_request(
                PullRequestObservationCommand(
                    change_set.id,
                    merged_repository,
                    1,
                    "https://example.test/pr/1",
                    FIRST_HEAD,
                )
            )
            await delivery.observe_ci(
                CIObservationCommand(change_set.id, merged_repository, True, "ci-1", "ok")
            )
            await delivery.record_merge_requested(
                RecordMergeRequestedCommand(change_set.id, merged_repository, FIRST_HEAD)
            )
            await delivery.observe_merge(
                MergeObservationCommand(change_set.id, merged_repository, MERGE_SHA)
            )
            await delivery.observe_pull_request(
                PullRequestObservationCommand(
                    change_set.id,
                    open_repository,
                    2,
                    "https://example.test/pr/2",
                    SECOND_HEAD,
                )
            )
            await application_container.execution_plan_store().add(
                bare_plan, idempotency_key="rollback-bare-plan"
            )
            await application_container.execution_plan_store().add(
                unpublished_plan, idempotency_key="rollback-unpublished-plan"
            )
            await _two_repository_change_set(
                delivery,
                delivery_id=unpublished_plan.id,
                organization_id=organization_id,
                project_id=unpublished_plan.project_id,
                first=uuid4(),
                second=uuid4(),
            )
            return created.principal.id, change_set.id

        leader_id, change_set_id = asyncio.run(seed())
        headers = {"Authorization": "Bearer internal-secret"}
        with TestClient(create_app(application_container)) as client:
            scope = client.get(
                f"/api/v1/deliveries/{plan.id}/rollback-scope", headers=headers
            )
            assert scope.status_code == 200
            body = scope.json()
            assert body["available"] is True
            assert body["recovery_in_progress"] is False
            rows = {row["repository_id"]: row for row in body["repositories"]}
            assert rows[str(merged_repository)]["state"] == "merged"
            assert rows[str(merged_repository)]["action"] == "revert_pull_request"
            assert rows[str(merged_repository)]["merge_sha"] == MERGE_SHA
            # Reverse merge order: the later repository is step 1.
            assert rows[str(open_repository)]["state"] == "unmerged"
            assert rows[str(open_repository)]["step"] == 1
            assert rows[str(merged_repository)]["step"] == 2

            payload = {
                "change_set_id": str(change_set_id),
                "reason": "bad data migration shipped",
                "requested_by_agent_id": str(leader_id),
                "idempotency_key": "console-rollback",
            }
            assert (
                client.post(f"/api/v1/deliveries/{plan.id}/rollback", json=payload).status_code
                == 401
            )
            assert (
                client.get(f"/api/v1/deliveries/{plan.id}/rollback-scope").status_code == 401
            )

            accepted = client.post(
                f"/api/v1/deliveries/{plan.id}/rollback", json=payload, headers=headers
            )
            assert accepted.status_code == 200
            assert accepted.json()["replayed"] is False
            assert accepted.json()["recovery_plan"]["trigger"] == "operator_requested"
            assert len(accepted.json()["decisions"]) == 2

            replay = client.post(
                f"/api/v1/deliveries/{plan.id}/rollback", json=payload, headers=headers
            )
            assert replay.status_code == 200
            assert replay.json()["replayed"] is True

            conflicting = client.post(
                f"/api/v1/deliveries/{plan.id}/rollback",
                json={**payload, "reason": "another reason", "idempotency_key": "second"},
                headers=headers,
            )
            assert conflicting.status_code == 409

            after = client.get(
                f"/api/v1/deliveries/{plan.id}/rollback-scope", headers=headers
            ).json()
            assert after["recovery_in_progress"] is True

            missing = client.post(
                f"/api/v1/deliveries/{uuid4()}/rollback", json=payload, headers=headers
            )
            assert missing.status_code == 404

            bare = client.get(
                f"/api/v1/deliveries/{bare_plan.id}/rollback-scope", headers=headers
            )
            assert bare.status_code == 200
            assert bare.json()["available"] is False
            assert bare.json()["unavailable_reason"] == "no_change_set"
            assert bare.json()["repositories"] == []

            unpublished = client.get(
                f"/api/v1/deliveries/{unpublished_plan.id}/rollback-scope", headers=headers
            ).json()
            assert unpublished["available"] is False
            assert unpublished["unavailable_reason"] == "nothing_delivered"
            assert [row["action"] for row in unpublished["repositories"]] == ["none", "none"]

            unknown = client.get(
                f"/api/v1/deliveries/{uuid4()}/rollback-scope", headers=headers
            )
            assert unknown.status_code == 404
    finally:
        get_settings.cache_clear()
