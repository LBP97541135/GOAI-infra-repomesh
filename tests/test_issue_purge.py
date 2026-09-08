"""彻底清除（POST /api/v1/issues/{issue_id}/purge，2026-09-08 用户裁决）。

Behavioral coverage: purge requires the archive tombstone (it is the
irreversible *second* step, not a bigger archive), hard-deletes snapshots and
decision-chain nodes before touching the audit log (a crash can only leave
"not fully purged", never "purged without a trace"), and leaves exactly one
``IssuePurged`` audit event so the deletion itself stays traceable.
"""

import asyncio
import contextlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

from repomesh.modules.repository_intelligence.application import (
    IssuePurgeNotArchived,
    IssuePurgeService,
)
from repomesh.modules.repository_intelligence.contracts import IssueArchiveView
from repomesh.modules.repository_intelligence.infrastructure import (
    InMemoryIssueArchiveStore,
)
from repomesh.shared.events import EventEnvelope

ISSUE_ID = uuid4()


class _FakeSnapshots:
    def __init__(self, log: list[tuple[str, UUID]]):
        self.log = log

    async def delete_for_project(self, project_id: UUID) -> int:
        self.log.append(("snapshots", project_id))
        return 3


class _FakeDecisionChain:
    def __init__(self, log: list[tuple[str, UUID]]):
        self.log = log

    async def purge_for_project(self, project_id: UUID) -> int:
        self.log.append(("decision_chain", project_id))
        return 2


class _FakeAudit:
    def __init__(self, log: list[tuple[str, UUID]]):
        self.log = log
        self.kept: list[EventEnvelope] = []

    async def purge_for_project(self, project_id: UUID, keep: EventEnvelope) -> int:
        self.log.append(("audit", project_id))
        self.kept.append(keep)
        return 7


def _archived_store() -> InMemoryIssueArchiveStore:
    store = InMemoryIssueArchiveStore()
    asyncio.run(
        store.add(IssueArchiveView(issue_id=ISSUE_ID, archived_at=datetime.now(UTC)))
    )
    return store


def test_purge_requires_the_archive_tombstone() -> None:
    """清除是归档之后的第二步：没有墓碑就拒绝，不能当「更大的归档」用。"""

    log: list[tuple[str, UUID]] = []
    audit = _FakeAudit(log)
    service = IssuePurgeService(
        InMemoryIssueArchiveStore(),
        _FakeSnapshots(log),
        _FakeDecisionChain(log),
        audit,
    )
    try:
        asyncio.run(service.purge(ISSUE_ID))
    except IssuePurgeNotArchived as error:
        assert "archived" in str(error)
    else:  # pragma: no cover
        raise AssertionError("expected IssuePurgeNotArchived")


def test_purge_deletes_in_order_and_keeps_one_traceable_event() -> None:
    log: list[tuple[str, UUID]] = []
    audit = _FakeAudit(log)
    service = IssuePurgeService(
        _archived_store(),
        _FakeSnapshots(log),
        _FakeDecisionChain(log),
        audit,
    )

    receipt = asyncio.run(service.purge(ISSUE_ID))

    # Business facts go first; the audit purge runs last, so a crash can only
    # ever leave "not fully purged", never "purged without a trace". Every
    # delete is scoped to the issue (issue == project_id), nothing else.
    assert [kind for kind, _ in log] == ["snapshots", "decision_chain", "audit"]
    assert all(pid == ISSUE_ID for _, pid in log)
    assert receipt == {"snapshots": 3, "decision_chain_nodes": 2, "audit_events": 7}

    (kept,) = audit.kept
    assert kept.event_type == "IssuePurged"
    assert kept.project_id == ISSUE_ID
    assert kept.payload["snapshotCount"] == 3
    assert kept.payload["decisionChainNodeCount"] == 2


def test_purge_of_an_unarchived_issue_leaves_no_trace() -> None:
    """Refused purges must not delete anything nor write audit events."""

    log: list[tuple[str, UUID]] = []
    audit = _FakeAudit(log)
    service = IssuePurgeService(
        InMemoryIssueArchiveStore(),
        _FakeSnapshots(log),
        _FakeDecisionChain(log),
        audit,
    )
    with contextlib.suppress(IssuePurgeNotArchived):
        asyncio.run(service.purge(ISSUE_ID))
    assert log == []
    assert audit.kept == []
