"""Governance write and archive endpoints for the delivery console (CONS-02)."""

import asyncio
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from repomesh.bootstrap import create_app
from repomesh.bootstrap.container import ApplicationContainer
from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalStatus,
    AgentPrincipalView,
    AgentRole,
)
from repomesh.modules.delivery import (
    DeliveryArchiveService,
    DeliveryConflict,
    DeliveryDenied,
    DeliveryGovernanceService,
    DeliveryNotFound,
    DeliveryService,
    InMemoryChangeSetStore,
    InMemoryDeliveryArchiveStore,
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
    RecordGovernanceDecisionCommand,
    RecordMergeRequestedCommand,
    RecordRecoveryActionCommand,
    RecoveryActionStatus,
    RecoveryTrigger,
    RepositoryCandidateInput,
)
from repomesh.modules.task_orchestration.contracts import ExecutionPlanStatus
from repomesh.modules.task_orchestration.domain import ExecutionPlan, PlannedRepositoryTask
from repomesh.settings import get_settings

HEAD = "a" * 40
BASE = "b" * 40


class StubDirectory:
    def __init__(self, *principals: AgentPrincipalView) -> None:
        self.principals = {principal.id: principal for principal in principals}

    async def get_view(self, agent_id: UUID) -> AgentPrincipalView | None:
        return self.principals.get(agent_id)


class StubPlanReader:
    def __init__(self, *plans: ExecutionPlan) -> None:
        self.plans = {plan.id: plan for plan in plans}

    async def get_view(self, plan_id: UUID):
        plan = self.plans.get(plan_id)
        return plan.to_view() if plan is not None else None


def _principal(
    *,
    organization_id: UUID,
    role: AgentRole,
    repository_id: UUID | None = None,
    status: AgentPrincipalStatus = AgentPrincipalStatus.ACTIVE,
) -> AgentPrincipalView:
    return AgentPrincipalView(
        id=uuid4(),
        organization_id=organization_id,
        role=role,
        leader_agent_id=None if role is AgentRole.ORGANIZATION_LEADER else uuid4(),
        repository_id=repository_id,
        responsibility_paths=(),
        agentteams_resource_name=f"console-{uuid4().hex[:8]}",
        status=status,
    )


def _plan(
    *,
    organization_id: UUID,
    project_id: UUID,
    repository_id: UUID,
    status: ExecutionPlanStatus,
) -> ExecutionPlan:
    return ExecutionPlan(
        organization_id=organization_id,
        project_id=project_id,
        created_by_agent_id=uuid4(),
        status=status,
        batches=(
            (
                PlannedRepositoryTask(
                    repository_id=repository_id,
                    title="Deliver the approved scope",
                    instruction="Implement the approved scope.",
                    acceptance=("Tests pass",),
                ),
            ),
        ),
    )


async def _prepare_change_set(
    delivery: DeliveryService,
    *,
    delivery_id: UUID,
    organization_id: UUID,
    project_id: UUID,
    repository_id: UUID,
):
    return await delivery.prepare(
        PrepareChangeSetCommand(
            organization_id=organization_id,
            project_id=project_id,
            created_by_agent_id=uuid4(),
            title="Console delivery",
            validation_snapshot_id=uuid4(),
            candidates=(
                RepositoryCandidateInput(
                    repository_id=repository_id,
                    task_id=uuid4(),
                    commit_sha=HEAD,
                    base_sha=BASE,
                    branch_name="repomesh/console",
                    required_approvals=0,
                ),
            ),
        ),
        idempotency_key=delivery_change_set_key(delivery_id),
    )


async def governance_scenario():
    organization_id = uuid4()
    project_id = uuid4()
    repository_id = uuid4()
    delivery_id = uuid4()
    org_leader = _principal(organization_id=organization_id, role=AgentRole.ORGANIZATION_LEADER)
    repo_leader = _principal(
        organization_id=organization_id,
        role=AgentRole.REPOSITORY_LEADER,
        repository_id=repository_id,
    )
    other_repo_leader = _principal(
        organization_id=organization_id,
        role=AgentRole.REPOSITORY_LEADER,
        repository_id=uuid4(),
    )
    worker = _principal(
        organization_id=organization_id, role=AgentRole.WORKER, repository_id=repository_id
    )
    foreign_leader = _principal(organization_id=uuid4(), role=AgentRole.ORGANIZATION_LEADER)
    delivery = DeliveryService(InMemoryChangeSetStore())
    change_set = await _prepare_change_set(
        delivery,
        delivery_id=delivery_id,
        organization_id=organization_id,
        project_id=project_id,
        repository_id=repository_id,
    )
    audit = InMemoryDeliveryAuditLog()
    service = DeliveryGovernanceService(
        delivery,
        StubDirectory(org_leader, repo_leader, other_repo_leader, worker, foreign_leader),
        audit,
    )
    return (
        service,
        delivery,
        audit,
        delivery_id,
        change_set,
        repository_id,
        org_leader,
        repo_leader,
        other_repo_leader,
        worker,
        foreign_leader,
    )


def _decision_command(
    change_set_id: UUID,
    repository_id: UUID,
    agent_id: UUID,
    *,
    head_sha: str = HEAD,
    decision: GovernanceDecisionKind = GovernanceDecisionKind.READY,
    reason: str = "release approved",
) -> RecordGovernanceDecisionCommand:
    return RecordGovernanceDecisionCommand(
        change_set_id=change_set_id,
        repository_id=repository_id,
        head_sha=head_sha,
        decision=decision,
        decided_by_agent_id=agent_id,
        reason=reason,
    )


@pytest.mark.asyncio
async def test_leaders_record_head_bound_decisions_and_audit_events() -> None:
    (
        service,
        _,
        audit,
        delivery_id,
        change_set,
        repository_id,
        org_leader,
        repo_leader,
        *_,
    ) = await governance_scenario()

    recorded = await service.record(
        delivery_id,
        _decision_command(change_set.id, repository_id, org_leader.id),
        idempotency_key="governance-1",
    )

    assert recorded.decision is GovernanceDecisionKind.READY
    assert recorded.head_sha == HEAD
    assert [event.event_type for event in audit.events] == [
        "DeliveryGovernanceDecisionRecorded"
    ]
    assert audit.events[0].actor_id == str(org_leader.id)

    matching_repo = await service.record(
        delivery_id,
        _decision_command(
            change_set.id,
            repository_id,
            repo_leader.id,
            decision=GovernanceDecisionKind.BLOCKED,
            reason="hold for review",
        ),
        idempotency_key="governance-2",
    )
    assert matching_repo.decision is GovernanceDecisionKind.BLOCKED


@pytest.mark.asyncio
async def test_replaying_the_same_decision_is_idempotent() -> None:
    service, delivery, audit, delivery_id, change_set, repository_id, org_leader, *_ = (
        await governance_scenario()
    )
    command = _decision_command(change_set.id, repository_id, org_leader.id)

    first = await service.record(delivery_id, command, idempotency_key="replay")
    version_after_first = (await delivery.get(change_set.id)).version
    second = await service.record(delivery_id, command, idempotency_key="replay")

    assert second.id == first.id
    assert (await delivery.get(change_set.id)).version == version_after_first
    assert len(audit.events) == 1


@pytest.mark.asyncio
async def test_head_sha_drift_and_wrong_change_set_conflict() -> None:
    service, _, _, delivery_id, change_set, repository_id, org_leader, *_ = (
        await governance_scenario()
    )

    with pytest.raises(DeliveryConflict, match="candidate head"):
        await service.record(
            delivery_id,
            _decision_command(change_set.id, repository_id, org_leader.id, head_sha="c" * 40),
            idempotency_key="drift",
        )

    with pytest.raises(DeliveryConflict, match="does not belong"):
        await service.record(
            delivery_id,
            _decision_command(uuid4(), repository_id, org_leader.id),
            idempotency_key="wrong-change-set",
        )

    with pytest.raises(DeliveryNotFound):
        await service.record(
            uuid4(),
            _decision_command(change_set.id, repository_id, org_leader.id),
            idempotency_key="unknown-delivery",
        )


@pytest.mark.asyncio
async def test_only_governing_leaders_may_decide() -> None:
    (
        service,
        _,
        _,
        delivery_id,
        change_set,
        repository_id,
        _,
        _,
        other_repo_leader,
        worker,
        foreign_leader,
    ) = await governance_scenario()

    for denied_agent in (other_repo_leader, worker, foreign_leader, _principal(
        organization_id=uuid4(), role=AgentRole.WORKER
    )):
        with pytest.raises((DeliveryDenied,)):
            await service.record(
                delivery_id,
                _decision_command(change_set.id, repository_id, denied_agent.id),
                idempotency_key="denied",
            )


async def archive_scenario(
    *,
    plan_status: ExecutionPlanStatus,
    with_change_set: bool = True,
):
    organization_id = uuid4()
    project_id = uuid4()
    repository_id = uuid4()
    plan = _plan(
        organization_id=organization_id,
        project_id=project_id,
        repository_id=repository_id,
        status=plan_status,
    )
    delivery = DeliveryService(InMemoryChangeSetStore())
    change_set = None
    if with_change_set:
        change_set = await _prepare_change_set(
            delivery,
            delivery_id=plan.id,
            organization_id=organization_id,
            project_id=project_id,
            repository_id=repository_id,
        )
    service = DeliveryArchiveService(
        InMemoryDeliveryArchiveStore(),
        delivery,
        StubPlanReader(plan),
        InMemoryDeliveryAuditLog(),
    )
    return service, delivery, plan, change_set, repository_id


async def _deliver(delivery: DeliveryService, change_set_id: UUID, repository_id: UUID) -> None:
    await delivery.observe_pull_request(
        PullRequestObservationCommand(
            change_set_id, repository_id, 7, "https://example.test/pr/7", HEAD
        )
    )
    await delivery.observe_ci(
        CIObservationCommand(change_set_id, repository_id, True, "ci-7", "passed")
    )
    await delivery.record_merge_requested(
        RecordMergeRequestedCommand(change_set_id, repository_id, HEAD)
    )
    await delivery.observe_merge(
        MergeObservationCommand(change_set_id, repository_id, "d" * 40)
    )


@pytest.mark.asyncio
async def test_delivered_change_set_archives_idempotently() -> None:
    service, delivery, plan, change_set, repository_id = await archive_scenario(
        plan_status=ExecutionPlanStatus.COMPLETED
    )
    await _deliver(delivery, change_set.id, repository_id)
    assert (await delivery.get(change_set.id)).status is ChangeSetStatus.DELIVERED

    first = await service.archive(plan.id)
    second = await service.archive(plan.id)

    assert first.delivery_id == plan.id
    assert second == first


@pytest.mark.asyncio
async def test_active_deliveries_refuse_archive() -> None:
    in_progress, _, active_plan, _, _ = await archive_scenario(
        plan_status=ExecutionPlanStatus.IN_PROGRESS
    )
    with pytest.raises(DeliveryConflict, match="in-progress"):
        await in_progress.archive(active_plan.id)

    open_delivery, _, plan, _, _ = await archive_scenario(
        plan_status=ExecutionPlanStatus.COMPLETED
    )
    with pytest.raises(DeliveryConflict, match="cannot be archived"):
        await open_delivery.archive(plan.id)

    service, _, _, _, _ = await archive_scenario(plan_status=ExecutionPlanStatus.COMPLETED)
    with pytest.raises(DeliveryNotFound):
        await service.archive(uuid4())


@pytest.mark.asyncio
async def test_plan_without_change_set_archives_after_terminal_state() -> None:
    service, _, plan, _, _ = await archive_scenario(
        plan_status=ExecutionPlanStatus.COMPLETED, with_change_set=False
    )
    archived = await service.archive(plan.id)
    assert archived.delivery_id == plan.id


@pytest.mark.asyncio
async def test_failed_delivery_archives_only_after_recovery_finishes() -> None:
    service, delivery, plan, change_set, repository_id = await archive_scenario(
        plan_status=ExecutionPlanStatus.FAILED
    )
    updated = await delivery.plan_recovery(
        PlanRecoveryCommand(
            change_set_id=change_set.id,
            trigger=RecoveryTrigger.RUNNER_FAILED,
            reason="runner crashed",
            repository_id=repository_id,
        )
    )
    with pytest.raises(DeliveryConflict):
        await service.archive(plan.id)

    recovery = updated.recovery_plans[-1]
    for action in recovery.actions:
        await delivery.record_recovery_action(
            RecordRecoveryActionCommand(
                change_set_id=change_set.id,
                recovery_plan_id=recovery.id,
                action_id=action.id,
                status=RecoveryActionStatus.SUCCEEDED,
                detail="done",
            )
        )
    archived = await service.archive(plan.id)
    assert archived.delivery_id == plan.id


def test_delivery_console_endpoints_over_http(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", "internal-secret")
    get_settings.cache_clear()
    try:
        organization_id = uuid4()
        project_id = uuid4()
        repository_id = uuid4()
        plan = _plan(
            organization_id=organization_id,
            project_id=project_id,
            repository_id=repository_id,
            status=ExecutionPlanStatus.COMPLETED,
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
                    agentteams_resource_name="console-org-leader",
                ),
                idempotency_key="console-org-leader",
            )
            await application_container.execution_plan_store().add(
                plan, idempotency_key="console-plan"
            )
            change_set = await _prepare_change_set(
                delivery,
                delivery_id=plan.id,
                organization_id=organization_id,
                project_id=project_id,
                repository_id=repository_id,
            )
            return created.principal.id, change_set.id

        leader_id, change_set_id = asyncio.run(seed())
        headers = {"Authorization": "Bearer internal-secret"}
        with TestClient(create_app(application_container)) as client:
            payload = {
                "change_set_id": str(change_set_id),
                "repository_id": str(repository_id),
                "head_sha": HEAD,
                "decision": "ready",
                "reason": "release approved",
                "idempotency_key": "console-governance",
                "decided_by_agent_id": str(leader_id),
            }
            unauthorized = client.post(
                f"/api/v1/deliveries/{plan.id}/governance-decisions", json=payload
            )
            assert unauthorized.status_code == 401

            recorded = client.post(
                f"/api/v1/deliveries/{plan.id}/governance-decisions",
                json=payload,
                headers=headers,
            )
            assert recorded.status_code == 200
            assert recorded.json()["decision"] == "ready"
            assert recorded.json()["head_sha"] == HEAD

            drifted = client.post(
                f"/api/v1/deliveries/{plan.id}/governance-decisions",
                json={**payload, "head_sha": "c" * 40, "idempotency_key": "drift"},
                headers=headers,
            )
            assert drifted.status_code == 409

            blocked_archive = client.post(
                f"/api/v1/deliveries/{plan.id}/archive", headers=headers
            )
            assert blocked_archive.status_code == 409

        asyncio.run(_deliver(delivery, change_set_id, repository_id))
        with TestClient(create_app(application_container)) as client:
            archived = client.post(
                f"/api/v1/deliveries/{plan.id}/archive", headers=headers
            )
            assert archived.status_code == 200
            assert archived.json()["delivery_id"] == str(plan.id)

            replay = client.post(
                f"/api/v1/deliveries/{plan.id}/archive", headers=headers
            )
            assert replay.status_code == 200
            assert replay.json() == archived.json()
    finally:
        get_settings.cache_clear()
