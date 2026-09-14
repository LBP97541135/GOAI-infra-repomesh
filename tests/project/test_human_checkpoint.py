from uuid import uuid4

import pytest

from repomesh.modules.project import (
    CheckpointDecisionKind,
    CodeAccessLevel,
    HumanControlAction,
    HumanProjectRole,
    ProjectCheckpoint,
    ProjectCheckpointService,
    ProjectExecutionMode,
    RecordCheckpointDecisionCommand,
)
from repomesh.modules.project.contracts import (
    HumanProjectGrantView,
    HumanReviewStatus,
    ProjectAgentTopologyView,
)
from repomesh.modules.project.domain import (
    HumanReviewRequest,
    ProjectTopologyError,
    ProjectTopologyViolation,
)
from repomesh.modules.project.infrastructure import (
    InMemoryHumanReviewRequestStore,
    InMemoryProjectCheckpointDecisionStore,
)


class TopologyReader:
    def __init__(self, topology: ProjectAgentTopologyView) -> None:
        self.topology = topology

    async def get_view(self, project_id):
        return self.topology if self.topology.project_id == project_id else None


class RecordingNotifier:
    def __init__(self) -> None:
        self.notifications = []

    async def notify(self, topology, review, decision) -> None:
        self.notifications.append((topology, review, decision))


def topology(*, code_access=CodeAccessLevel.READ) -> ProjectAgentTopologyView:
    human_id = uuid4()
    return ProjectAgentTopologyView(
        id=uuid4(),
        organization_id=uuid4(),
        project_id=uuid4(),
        organization_leader_id=uuid4(),
        repository_teams=(),
        execution_mode=ProjectExecutionMode.SUPERVISED,
        required_checkpoints=frozenset({ProjectCheckpoint.SPECIFICATION}),
        human_grants=(
            HumanProjectGrantView(
                human_principal_id=human_id,
                role=HumanProjectRole.PROJECT_SUPERVISOR,
                code_access=code_access,
                control_actions=frozenset(
                    {
                        HumanControlAction.APPROVE_CHECKPOINT,
                        HumanControlAction.REQUEST_CHANGES,
                    }
                ),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_checkpoint_is_pending_then_approved_for_exact_evidence() -> None:
    project = topology()
    reviews = InMemoryHumanReviewRequestStore()
    service = ProjectCheckpointService(
        TopologyReader(project),
        InMemoryProjectCheckpointDecisionStore(),
        reviews,
    )
    pending = await service.evaluate(
        project.project_id, ProjectCheckpoint.SPECIFICATION, "spec-v1"
    )
    assert not pending.allowed
    assert pending.reason == "human_checkpoint_pending"
    pending_reviews = await reviews.list_all(status=HumanReviewStatus.PENDING)
    assert len(pending_reviews) == 1
    assert pending_reviews[0].evidence_version == "spec-v1"

    await service.record(
        RecordCheckpointDecisionCommand(
            project_id=project.project_id,
            review_request_id=pending_reviews[0].id,
            human_principal_id=project.human_grants[0].human_principal_id,
            decision=CheckpointDecisionKind.APPROVED,
            reason="spec is complete",
        )
    )

    assert (
        await service.evaluate(
            project.project_id, ProjectCheckpoint.SPECIFICATION, "spec-v1"
        )
    ).allowed
    resolved = await reviews.list_all(status=HumanReviewStatus.APPROVED)
    assert len(resolved) == 1
    assert resolved[0].resolved_by_human_id == project.human_grants[0].human_principal_id
    stale = await service.evaluate(
        project.project_id, ProjectCheckpoint.SPECIFICATION, "spec-v2"
    )
    assert stale.reason == "human_checkpoint_evidence_stale"


@pytest.mark.asyncio
async def test_unconfigured_human_cannot_approve() -> None:
    project = topology()
    reviews = InMemoryHumanReviewRequestStore()
    service = ProjectCheckpointService(
        TopologyReader(project),
        InMemoryProjectCheckpointDecisionStore(),
        reviews,
    )
    await service.evaluate(project.project_id, ProjectCheckpoint.SPECIFICATION, "spec-v1")
    review = (await reviews.list_all(status=HumanReviewStatus.PENDING))[0]
    with pytest.raises(ProjectTopologyViolation, match="membership_denied"):
        await service.record(
            RecordCheckpointDecisionCommand(
                project_id=project.project_id,
                review_request_id=review.id,
                human_principal_id=uuid4(),
                decision=CheckpointDecisionKind.APPROVED,
                reason="unauthorized",
            )
        )


@pytest.mark.asyncio
async def test_exception_escalation_is_mandatory_for_non_automatic_projects() -> None:
    project = topology()
    reviews = InMemoryHumanReviewRequestStore()
    service = ProjectCheckpointService(
        TopologyReader(project),
        InMemoryProjectCheckpointDecisionStore(),
        reviews,
    )
    gate = await service.evaluate(
        project.project_id,
        ProjectCheckpoint.EXCEPTION_ESCALATION,
        "task:repo-task:v2",
        repository_id=uuid4(),
    )
    assert gate.reason == "human_checkpoint_pending"
    pending = await reviews.list_all(status=HumanReviewStatus.PENDING)
    assert pending[0].checkpoint is ProjectCheckpoint.EXCEPTION_ESCALATION


@pytest.mark.asyncio
async def test_exception_human_decision_is_returned_to_the_agent_coordination_path() -> None:
    project = topology()
    repository_id = uuid4()
    requester_id = uuid4()
    reviews = InMemoryHumanReviewRequestStore()
    notifier = RecordingNotifier()
    service = ProjectCheckpointService(
        TopologyReader(project),
        InMemoryProjectCheckpointDecisionStore(),
        reviews,
        notifier,
    )
    await service.evaluate(
        project.project_id,
        ProjectCheckpoint.EXCEPTION_ESCALATION,
        "task:repo-task:v2",
        repository_id=repository_id,
        requested_by_agent_id=requester_id,
        title="Pricing task blocked",
        summary="Contract ownership is ambiguous.",
    )
    decision = await service.record(
        RecordCheckpointDecisionCommand(
            project_id=project.project_id,
            review_request_id=(
                await reviews.list_all(status=HumanReviewStatus.PENDING)
            )[0].id,
            human_principal_id=project.human_grants[0].human_principal_id,
            decision=CheckpointDecisionKind.APPROVED,
            reason="Leader may revise the contract owner.",
        )
    )
    assert len(notifier.notifications) == 1
    _, review, notified_decision = notifier.notifications[0]
    assert review.requested_by_agent_id == requester_id
    assert notified_decision == decision


@pytest.mark.asyncio
async def test_decision_requires_an_existing_pending_review() -> None:
    project = topology()
    service = ProjectCheckpointService(
        TopologyReader(project),
        InMemoryProjectCheckpointDecisionStore(),
        InMemoryHumanReviewRequestStore(),
    )
    with pytest.raises(ProjectTopologyViolation, match="review request does not exist"):
        await service.record(
            RecordCheckpointDecisionCommand(
                project_id=project.project_id,
                review_request_id=uuid4(),
                human_principal_id=project.human_grants[0].human_principal_id,
                decision=CheckpointDecisionKind.APPROVED,
                reason="cannot pre-approve guessed evidence",
            )
        )


@pytest.mark.asyncio
async def test_review_request_can_only_be_decided_once() -> None:
    project = topology()
    reviews = InMemoryHumanReviewRequestStore()
    service = ProjectCheckpointService(
        TopologyReader(project), InMemoryProjectCheckpointDecisionStore(), reviews
    )
    await service.evaluate(project.project_id, ProjectCheckpoint.SPECIFICATION, "spec-v1")
    review = (await reviews.list_all(status=HumanReviewStatus.PENDING))[0]
    command = RecordCheckpointDecisionCommand(
        project_id=project.project_id,
        review_request_id=review.id,
        human_principal_id=project.human_grants[0].human_principal_id,
        decision=CheckpointDecisionKind.APPROVED,
        reason="first decision wins",
    )
    await service.record(command)
    with pytest.raises(ProjectTopologyError, match="already decided"):
        await service.record(command)


def _drifted_auto_topology() -> ProjectAgentTopologyView:
    """政策漂移后的档案：单据开立时卡点启用，其后项目重建为 auto、
    卡点清空、授权人清空（2026-09-14 生产实证：26 张 pending 全部锁死）。"""
    return ProjectAgentTopologyView(
        id=uuid4(),
        organization_id=uuid4(),
        project_id=uuid4(),
        organization_leader_id=uuid4(),
        repository_teams=(),
        execution_mode=ProjectExecutionMode.AUTO,
        required_checkpoints=frozenset(),
        human_grants=(),
    )


async def _stale_pending_request(service, project):
    request = await service._reviews.ensure(
        HumanReviewRequest(
            project_id=project.project_id,
            checkpoint=ProjectCheckpoint.EXCEPTION_ESCALATION,
            evidence_version="esc-v1",
            title="异常升级",
            summary="Agent 已到达人工控制检查点，后续受控操作已暂停。",
        )
    )
    return request


@pytest.mark.asyncio
async def test_stale_policy_request_is_resolvable_by_admin() -> None:
    project = _drifted_auto_topology()
    reviews = InMemoryHumanReviewRequestStore()
    service = ProjectCheckpointService(
        TopologyReader(project), InMemoryProjectCheckpointDecisionStore(), reviews
    )
    stale = await _stale_pending_request(service, project)
    admin_id = uuid4()

    decision = await service.record(
        RecordCheckpointDecisionCommand(
            project_id=project.project_id,
            review_request_id=stale.id,
            human_principal_id=admin_id,
            decision=CheckpointDecisionKind.APPROVED,
            reason="历史旁路清偿",
            actor_is_admin=True,
        )
    )

    assert decision.decision is CheckpointDecisionKind.APPROVED
    resolved = await reviews.list_all(status=HumanReviewStatus.APPROVED)
    assert len(resolved) == 1
    assert resolved[0].resolved_by_human_id == admin_id


@pytest.mark.asyncio
async def test_stale_policy_request_stays_blocked_for_non_admin() -> None:
    project = _drifted_auto_topology()
    reviews = InMemoryHumanReviewRequestStore()
    service = ProjectCheckpointService(
        TopologyReader(project), InMemoryProjectCheckpointDecisionStore(), reviews
    )
    stale = await _stale_pending_request(service, project)

    with pytest.raises(ProjectTopologyViolation, match="checkpoint is not enabled"):
        await service.record(
            RecordCheckpointDecisionCommand(
                project_id=project.project_id,
                review_request_id=stale.id,
                human_principal_id=uuid4(),
                decision=CheckpointDecisionKind.APPROVED,
                reason="非管理员不应清偿漂移单据",
                actor_is_admin=False,
            )
        )
    assert await reviews.list_all(status=HumanReviewStatus.PENDING)
