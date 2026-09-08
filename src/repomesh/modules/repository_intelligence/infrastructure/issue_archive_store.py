"""Issue archive stores (issue-grain tombstones, delivery ``delivery_archives`` style).

An archive row is a marker, not a delete: every fact tied to the project —
plan snapshots, decision-chain nodes, checkpoint decisions, audit events —
stays exactly where it is. The primary key is the idempotency marker, so a
repeated archive conflicts at the store layer and folds into a no-op at the
service layer.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from repomesh.persistence import Database
from repomesh.shared.domain import DomainError

from ..contracts import IssueArchiveView
from .models import IssueArchiveRecord


class IssueArchiveConflict(DomainError):
    """The issue already carries an archive row (store-level race guard)."""


class IssueArchiveNotFound(DomainError):
    """No plan snapshot evidences the issue — nothing exists to archive."""


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class InMemoryIssueArchiveStore:
    def __init__(self) -> None:
        self.items: dict[UUID, IssueArchiveView] = {}

    async def add(self, archive: IssueArchiveView) -> None:
        if archive.issue_id in self.items:
            raise IssueArchiveConflict("issue is already archived")
        self.items[archive.issue_id] = archive

    async def get(self, issue_id: UUID) -> IssueArchiveView | None:
        return self.items.get(issue_id)

    async def list_all(self) -> tuple[IssueArchiveView, ...]:
        return tuple(self.items.values())


class PostgresIssueArchiveStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def add(self, archive: IssueArchiveView) -> None:
        try:
            async with self._database.transaction() as session:
                session.add(
                    IssueArchiveRecord(
                        issue_id=archive.issue_id,
                        archived_at=archive.archived_at,
                    )
                )
        except IntegrityError as error:
            raise IssueArchiveConflict("issue is already archived") from error

    async def get(self, issue_id: UUID) -> IssueArchiveView | None:
        async with self._database.transaction() as session:
            record = await session.get(IssueArchiveRecord, issue_id)
        if record is None:
            return None
        return IssueArchiveView(
            issue_id=record.issue_id,
            archived_at=_aware(record.archived_at),
        )

    async def list_all(self) -> tuple[IssueArchiveView, ...]:
        async with self._database.transaction() as session:
            rows = (
                await session.execute(
                    select(IssueArchiveRecord.issue_id, IssueArchiveRecord.archived_at)
                )
            ).all()
        return tuple(
            IssueArchiveView(issue_id=issue_id, archived_at=_aware(archived_at))
            for issue_id, archived_at in rows
        )
