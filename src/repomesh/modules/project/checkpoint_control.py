from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from repomesh.modules.project.contracts import (
    CheckpointDecisionKind,
    CheckpointGateDecision,
    HumanControlAction,
    HumanReviewStatus,
    ProjectCheckpoint,
    ProjectCheckpointDecisionView,
    ProjectOperationalStatus,
    ProjectTopologyReader,
)
from repomesh.modules.project.domain import (
    HumanReviewRequest,
    ProjectCheckpointDecision,
    ProjectTopologyConflict,
    ProjectTopologyViolation,
)
from repomesh.modules.project.human_control import (
    HumanAuthorizationRequest,
    authorize_human,
    requires_human_checkpoint,
)
from repomesh.modules.project.ports import (
    HumanReviewRequestStore,
    ProjectCheckpointDecisionStore,
)


@dataclass(frozen=True, slots=True)
class RecordCheckpointDecisionCommand:
    project_id: UUID
    review_request_id: UUID
    human_principal_id: UUID
    decision: CheckpointDecisionKind
    reason: str


class HumanDecisionNotifier(Protocol):
    async def notify(
        self,
        topology,
        review: HumanReviewRequest,
        decision: ProjectCheckpointDecisionView,
    ) -> None: ...


class ProjectCheckpointService:
    _CHECKPOINT_LABELS = {
        ProjectCheckpoint.REPOSITORY_SCOPE: "仓库修改范围",
        ProjectCheckpoint.SPECIFICATION: "工程 Spec",
        ProjectCheckpoint.EXECUTION: "任务执行",
        ProjectCheckpoint.VALIDATION: "测试验证",
        ProjectCheckpoint.DELIVERY: "PR 交付",
        ProjectCheckpoint.EXCEPTION_ESCALATION: "异常升级",
    }
    def __init__(
        self,
        topologies: ProjectTopologyReader,
        decisions: ProjectCheckpointDecisionStore,
        reviews: HumanReviewRequestStore,
        notifier: HumanDecisionNotifier | None = None,
    ) -> None:
        self._topologies = topologies
        self._decisions = decisions
        self._reviews = reviews
        self._notifier = notifier

    async def record(
        self, command: RecordCheckpointDecisionCommand
    ) -> ProjectCheckpointDecisionView:
        topology = await self._topologies.get_view(command.project_id)
        if topology is None:
            raise ProjectTopologyViolation("project topology does not exist")
        review = await self._reviews.get_by_id(command.review_request_id)
        if review is None or review.project_id != command.project_id:
            raise ProjectTopologyViolation("pending review request does not exist")
        if review.status is not HumanReviewStatus.PENDING:
            raise ProjectTopologyConflict("review request was already decided")
        if not requires_human_checkpoint(topology, review.checkpoint):
            raise ProjectTopologyViolation("checkpoint is not enabled for this project")
        action = (
            HumanControlAction.APPROVE_CHECKPOINT
            if command.decision is CheckpointDecisionKind.APPROVED
            else HumanControlAction.REQUEST_CHANGES
        )
        authorization = authorize_human(
            topology,
            HumanAuthorizationRequest(
                human_principal_id=command.human_principal_id,
                action=action,
                repository_id=review.repository_id,
            ),
        )
        if not authorization.allowed:
            raise ProjectTopologyViolation(authorization.reason)
        decision = ProjectCheckpointDecision(
            review_request_id=review.id,
            project_id=command.project_id,
            checkpoint=review.checkpoint,
            repository_id=review.repository_id,
            human_principal_id=command.human_principal_id,
            decision=command.decision,
            reason=command.reason.strip(),
            evidence_version=review.evidence_version,
        )
        await self._decisions.add(decision)
        resolved = await self._reviews.resolve_pending(
            review.id,
            command.decision,
            command.human_principal_id,
        )
        if not resolved:
            raise ProjectTopologyConflict("review request was already decided")
        view = decision.to_view()
        if (
            self._notifier is not None
            and review is not None
            and review.checkpoint is ProjectCheckpoint.EXCEPTION_ESCALATION
        ):
            await self._notifier.notify(topology, review, view)
        return view

    async def evaluate(
        self,
        project_id: UUID,
        checkpoint: ProjectCheckpoint,
        evidence_version: str,
        *,
        repository_id: UUID | None = None,
        requested_by_agent_id: UUID | None = None,
        title: str | None = None,
        summary: str | None = None,
    ) -> CheckpointGateDecision:
        topology = await self._topologies.get_view(project_id)
        if topology is None:
            return CheckpointGateDecision(False, "project_topology_missing")
        if topology.operational_status is ProjectOperationalStatus.PAUSED:
            return CheckpointGateDecision(False, "project_paused")
        if topology.operational_status is ProjectOperationalStatus.CANCELLED:
            return CheckpointGateDecision(False, "project_cancelled")
        if not requires_human_checkpoint(topology, checkpoint):
            return CheckpointGateDecision(True, "human_checkpoint_not_required")
        decision = await self._decisions.latest(project_id, checkpoint, repository_id)
        if decision is None:
            await self._reviews.ensure(
                HumanReviewRequest(
                    project_id=project_id,
                    checkpoint=checkpoint,
                    repository_id=repository_id,
                    evidence_version=evidence_version.strip(),
                    title=title or f"{self._CHECKPOINT_LABELS[checkpoint]}需要人工确认",
                    summary=summary or "Agent 已到达人工控制检查点，后续受控操作已暂停。",
                    requested_by_agent_id=requested_by_agent_id,
                )
            )
            return CheckpointGateDecision(False, "human_checkpoint_pending")
        if decision.evidence_version != evidence_version.strip():
            await self._reviews.ensure(
                HumanReviewRequest(
                    project_id=project_id,
                    checkpoint=checkpoint,
                    repository_id=repository_id,
                    evidence_version=evidence_version.strip(),
                    title=title or f"{self._CHECKPOINT_LABELS[checkpoint]}证据已更新",
                    summary=summary or "Agent 在上次人工决定后更新了证据，需要重新确认。",
                    requested_by_agent_id=requested_by_agent_id,
                )
            )
            return CheckpointGateDecision(False, "human_checkpoint_evidence_stale")
        if decision.decision is not CheckpointDecisionKind.APPROVED:
            return CheckpointGateDecision(False, f"human_checkpoint_{decision.decision.value}")
        return CheckpointGateDecision(True, "human_checkpoint_approved")

    async def operational_gate(self, project_id: UUID) -> CheckpointGateDecision:
        topology = await self._topologies.get_view(project_id)
        if topology is None:
            return CheckpointGateDecision(False, "project_topology_missing")
        if topology.operational_status is ProjectOperationalStatus.PAUSED:
            return CheckpointGateDecision(False, "project_paused")
        if topology.operational_status is ProjectOperationalStatus.CANCELLED:
            return CheckpointGateDecision(False, "project_cancelled")
        return CheckpointGateDecision(True, "project_active")
