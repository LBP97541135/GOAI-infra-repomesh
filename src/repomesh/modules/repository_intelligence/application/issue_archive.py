"""Issue archive (list hygiene with delivery-archive semantics, v0.2 §0 grain).

An issue *is* a project_id, so archiving one writes a tombstone row plus an
``IssueArchived`` audit event and stops the default issue list from showing
it. Nothing is deleted: snapshots, decision-chain nodes, checkpoint decisions
and delivery facts all remain queryable — the audit trail gains one event
instead of losing history. An issue with an in-progress round is refused:
archiving live work would hide it from the very console that owns it, the
same refusal ``DeliveryArchiveService`` makes for in-progress deliveries.
"""

from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from repomesh.modules.agent_directory.contracts import AgentPrincipalReader
from repomesh.modules.repository_intelligence.contracts import IssueArchiveView
from repomesh.modules.repository_intelligence.infrastructure.issue_archive_store import (
    IssueArchiveConflict,
    IssueArchiveNotFound,
)
from repomesh.modules.task_orchestration.contracts import ExecutionPlanStatus
from repomesh.shared.domain import new_id
from repomesh.shared.events import ActorType, EventEnvelope

__all__ = [
    "IssueArchiveAuditLog",
    "IssueArchiveConflict",
    "IssueArchiveNotFound",
    "IssueArchivePlanReader",
    "IssueArchiveService",
    "IssueArchiveSnapshotReader",
    "IssueArchiveStore",
]


class IssueArchiveAuditLog(Protocol):
    async def append(self, event: EventEnvelope) -> None: ...


class IssueArchiveSnapshotReader(Protocol):
    """The snapshot existence probe: an issue exists iff a snapshot does."""

    async def list_all(self, project_id: UUID) -> tuple[Any, ...]: ...


class IssueArchivePlanReader(Protocol):
    """Execution plans, for the in-progress refusal (same shape the read
    model reads: the store answers ``list_all`` and the caller scopes it)."""

    async def list_all(self) -> tuple[Any, ...]: ...


class IssueArchiveStore(Protocol):
    async def add(self, archive: IssueArchiveView) -> None: ...

    async def get(self, issue_id: UUID) -> IssueArchiveView | None: ...


class IssueArchiveService:
    """Archive an issue; archived issues keep all data."""

    def __init__(
        self,
        archives: IssueArchiveStore,
        snapshots: IssueArchiveSnapshotReader,
        plans: IssueArchivePlanReader,
        directory: AgentPrincipalReader,
        audit: IssueArchiveAuditLog,
    ) -> None:
        self._archives = archives
        self._snapshots = snapshots
        self._plans = plans
        self._directory = directory
        self._audit = audit

    async def archive(self, issue_id: UUID) -> IssueArchiveView:
        existing = await self._archives.get(issue_id)
        if existing is not None:
            return existing
        snapshots = await self._snapshots.list_all(issue_id)
        if not snapshots:
            # An issue is its first snapshot (v0.2 §0); no snapshot, no issue.
            raise IssueArchiveNotFound(f"issue not found: {issue_id}")
        in_progress = any(
            getattr(plan, "project_id", None) == issue_id
            and getattr(plan, "status", None) is ExecutionPlanStatus.IN_PROGRESS
            for plan in await self._plans.list_all()
        )
        if in_progress:
            raise IssueArchiveConflict(
                "an issue with an in-progress round cannot be archived"
            )
        archive = IssueArchiveView(issue_id=issue_id, archived_at=datetime.now(UTC))
        try:
            await self._archives.add(archive)
        except IssueArchiveConflict:
            stored = await self._archives.get(issue_id)
            if stored is not None:
                return stored
            raise
        # Workspace attribution follows the intake event's: the org of the
        # agent that opened the issue, never guessed from the caller.
        organization_id = None
        creator_id = getattr(snapshots[-1], "created_by_agent_id", None)
        if creator_id is not None:
            creator = await self._directory.get_view(creator_id)
            if creator is not None:
                organization_id = creator.organization_id
        await self._audit.append(
            EventEnvelope(
                event_type="IssueArchived",
                actor_type=ActorType.SERVICE,
                actor_id="repomesh-api",
                aggregate_type="Project",
                aggregate_id=issue_id,
                aggregate_version=1,
                correlation_id=new_id(),
                organization_id=organization_id,
                project_id=issue_id,
                payload={"snapshotCount": len(snapshots)},
            )
        )
        return archive
