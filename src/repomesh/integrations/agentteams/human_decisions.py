import json
import logging

from repomesh.modules.collaboration.contracts import (
    CollaborationDeliveryDeferred,
    CollaborationGateway,
    CollaborationMessageKind,
    SendCollaborationMessageCommand,
)
from repomesh.modules.project.checkpoint_control import HumanDecisionNotifier
from repomesh.modules.project.contracts import (
    ProjectAgentTopologyView,
    ProjectCheckpointDecisionView,
)
from repomesh.modules.project.domain import HumanReviewRequest


class HumanDecisionCollaborationNotifier(HumanDecisionNotifier):
    """Return an exception decision through the existing leader coordination route."""

    def __init__(self, collaboration: CollaborationGateway) -> None:
        self._collaboration = collaboration

    async def notify(
        self,
        topology: ProjectAgentTopologyView,
        review: HumanReviewRequest,
        decision: ProjectCheckpointDecisionView,
    ) -> None:
        if review.requested_by_agent_id is None or review.repository_id is None:
            return
        payload = json.dumps(
            {
                "schema": "repomesh.human-decision.v1",
                "review_request_id": str(review.id),
                "project_id": str(review.project_id),
                "repository_id": str(review.repository_id),
                "checkpoint": review.checkpoint.value,
                "evidence_version": review.evidence_version,
                "decision": decision.decision.value,
                "reason": decision.reason,
                "human_principal_id": str(decision.human_principal_id),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            await self._collaboration.send(
                SendCollaborationMessageCommand(
                    organization_id=topology.organization_id,
                    project_id=review.project_id,
                    repository_id=review.repository_id,
                    sender_agent_id=review.requested_by_agent_id,
                    recipient_agent_id=topology.organization_leader_id,
                    kind=CollaborationMessageKind.TASK_REPORT,
                    subject=f"人工审核决定：{review.title}",
                    body=payload,
                    correlation_id=review.id,
                ),
                idempotency_key=f"human-decision:{decision.id}",
            )
        except CollaborationDeliveryDeferred as error:
            logging.getLogger(__name__).warning(
                "human decision notification persisted for background retry",
                extra={
                    "review_request_id": str(review.id),
                    "collaboration_message_id": str(error.message_id),
                },
            )
