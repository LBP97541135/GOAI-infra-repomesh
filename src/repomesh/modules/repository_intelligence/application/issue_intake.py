"""Issue intake (contract v0.3 §1): create an issue as its first draft snapshot.

An issue is a project_id (v0.2 §0 — no Project entity exists). Creating one
means persisting the earliest PlanSnapshot: ``plan_version=1``,
``execution_plan_id=None``, empty DAG/contract fields. Everything the read
model needs (state rule 4 "virtual draft → open", phase rule 3, the
organization chain's third level) derives from that single row.

Idempotency: ``project_id`` is a UUIDv5 of the client-supplied key, so a
replay lands on the same ``(project_id, plan_version=1)`` unique constraint
and returns the existing issue instead of creating a second one. Key
generation responsibility is the client's (fresh random key per logical
create, same key on retry) — see contract §1.3.
"""

from __future__ import annotations

from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalReader,
    AgentPrincipalStatus,
    AgentRole,
)
from repomesh.modules.repository_intelligence.contracts import (
    IssueIntakeCommand,
    IssueIntakeReceipt,
)
from repomesh.modules.repository_intelligence.infrastructure.plan_snapshot_store import (
    PlanSnapshotAlreadyExists,
    PlanSnapshotStore,
)
from repomesh.shared.domain import DomainError, new_id
from repomesh.shared.events import ActorType, EventEnvelope

# Stable namespace for key→project_id derivation. Changing this value would
# re-map every existing idempotency key; treat it as frozen.
ISSUE_INTAKE_NAMESPACE = uuid5(NAMESPACE_URL, "repomesh://issue-intake")


class IssueIntakeActorNotFound(DomainError):
    """The requested created_by_agent_id does not exist."""


class IssueIntakeDenied(DomainError):
    """The actor is not an active ORGANIZATION_LEADER (contract §1.2, Q1)."""


class IssueIntakeAuditLog(Protocol):
    async def append(self, event: EventEnvelope) -> None: ...


class IssueIntakeService:
    """Implements the ``CreateIssueIntake`` contract."""

    def __init__(
        self,
        snapshots: PlanSnapshotStore,
        directory: AgentPrincipalReader,
        audit: IssueIntakeAuditLog,
    ) -> None:
        self._snapshots = snapshots
        self._directory = directory
        self._audit = audit

    async def execute(self, command: IssueIntakeCommand) -> IssueIntakeReceipt:
        text = command.requirement_text.strip()
        if not text:
            raise ValueError("requirement_text is required")
        key = command.idempotency_key.strip()
        if not key:
            raise ValueError("idempotency_key is required")

        actor = await self._directory.get_view(command.created_by_agent_id)
        if actor is None:
            raise IssueIntakeActorNotFound(
                f"agent principal not found: {command.created_by_agent_id}"
            )
        if (
            actor.status is not AgentPrincipalStatus.ACTIVE
            or actor.role is not AgentRole.ORGANIZATION_LEADER
        ):
            raise IssueIntakeDenied(
                "issue intake requires an active organization leader"
            )

        project_id = uuid5(ISSUE_INTAKE_NAMESPACE, key)
        try:
            await self._snapshots.save(
                project_id=project_id,
                plan_version=1,
                engineering_spec="",
                contracts=[],
                task_dag=[],
                execution_batches=[],
                graph_edges=[],
                created_by_agent_id=actor.id,
                execution_plan_id=None,
                requirement_text=text,
            )
        except PlanSnapshotAlreadyExists:
            # Same key replay: the original snapshot (and its text) stands.
            return IssueIntakeReceipt(project_id=project_id, created=False)

        # Audit only the actual creation — a replay is a no-op (§1.5).
        await self._audit.append(
            EventEnvelope(
                event_type="IssueIntakeCreated",
                actor_type=ActorType.AGENT,
                actor_id=str(actor.id),
                aggregate_type="Project",
                aggregate_id=project_id,
                aggregate_version=1,
                correlation_id=new_id(),
                organization_id=actor.organization_id,
                project_id=project_id,
                payload={
                    "requirementText": text,
                    "createdByAgentId": str(actor.id),
                    "idempotencyKey": key,
                },
            )
        )
        return IssueIntakeReceipt(project_id=project_id, created=True)
