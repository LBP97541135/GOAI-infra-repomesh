"""Issue archive endpoint (POST /api/v1/issues/{issue_id}/archive) over HTTP.

Behavioral coverage: archiving hides the issue from the default listing and
both tab counts, ``include_archived`` re-admits it with the tombstone fields,
a replay returns the same ``archived_at`` without a second audit row, an
unknown issue 404s, and an issue with an in-progress round is refused —
archiving must never hide live work.
"""

import asyncio
from datetime import datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from repomesh.bootstrap.app import create_app
from repomesh.bootstrap.container import ApplicationContainer
from repomesh.modules.agent_directory.contracts import AgentRole
from repomesh.modules.repository_intelligence.application import IssueArchiveService
from repomesh.modules.repository_intelligence.infrastructure import (
    InMemoryIssueArchiveStore,
    IssueArchiveConflict,
    IssueArchiveNotFound,
)
from repomesh.modules.task_orchestration.contracts import ExecutionPlanStatus
from repomesh.settings import get_settings

HEADERS = {"Authorization": "Bearer internal-secret"}


def _seed_leader(container: ApplicationContainer):
    from repomesh.modules.agent_directory.application import (
        CreateAgent,
        CreateAgentRequest,
    )

    organization_id = uuid4()

    async def seed():
        creator = CreateAgent(container.agent_directory)
        leader = await creator.execute(
            CreateAgentRequest(
                organization_id=organization_id,
                role=AgentRole.ORGANIZATION_LEADER,
                agentteams_resource_name="archive-org-leader",
            ),
            idempotency_key="archive-org-leader",
        )
        return organization_id, leader.principal.id

    return asyncio.run(seed())


def _create_issue(client: TestClient, leader_id) -> dict:
    created = client.post(
        "/api/v1/issues",
        json={
            "requirement_text": "结算页支持满额免运费门槛",
            "created_by_agent_id": str(leader_id),
            "idempotency_key": f"archive-test-key-{uuid4()}",
        },
        headers=HEADERS,
    )
    assert created.status_code == 201
    return created.json()


def test_issue_archive_over_http(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", "internal-secret")
    get_settings.cache_clear()
    try:
        _, leader_id = _seed_leader(application_container)
        with TestClient(create_app(application_container)) as client:
            issue = _create_issue(client, leader_id)

            # Before archiving the default listing shows the issue, with the
            # archive fields present and false (contract increments never
            # leave fields absent — the client renders without branching).
            listing = client.get("/api/v1/issues?state=all", headers=HEADERS).json()
            row = next(r for r in listing["issues"] if r["issue_id"] == issue["issue_id"])
            assert row["archived"] is False
            assert row["archived_at"] is None
            assert listing["open_count"] == 1

            # Unknown issue → 404.
            assert (
                client.post(f"/api/v1/issues/{uuid4()}/archive", headers=HEADERS).status_code
                == 404
            )

            archived = client.post(
                f"/api/v1/issues/{issue['issue_id']}/archive", headers=HEADERS
            )
            assert archived.status_code == 200
            body = archived.json()
            assert body["issue_id"] == issue["issue_id"]
            assert body["archived_at"]

            # The default listing AND both tab counts exclude the tombstone;
            # the counts answer "what still needs someone", and an archived
            # issue does not.
            default = client.get("/api/v1/issues?state=all", headers=HEADERS).json()
            assert default["issues"] == []
            assert default["open_count"] == 0
            assert default["closed_count"] == 0

            admitted = client.get(
                "/api/v1/issues?state=all&include_archived=true", headers=HEADERS
            ).json()
            assert len(admitted["issues"]) == 1
            assert admitted["issues"][0]["archived"] is True
            # Serializers across the two endpoints may spell UTC differently
            # (Z vs +00:00); compare the instants, not the strings.
            assert datetime.fromisoformat(admitted["issues"][0]["archived_at"]) == (
                datetime.fromisoformat(body["archived_at"])
            )
            # A draft issue is still open (§2.1 rule 4) — archiving is list
            # hygiene and must not rewrite its state.
            assert admitted["issues"][0]["state"] == "open"
            assert admitted["open_count"] == 1

            # Idempotent replay: same tombstone, no second archive fact.
            replay = client.post(
                f"/api/v1/issues/{issue['issue_id']}/archive", headers=HEADERS
            )
            assert replay.status_code == 200
            assert replay.json()["archived_at"] == body["archived_at"]
    finally:
        get_settings.cache_clear()


class _FakeSnapshots:
    def __init__(self, created_by_agent_id=None):
        self.created_by_agent_id = created_by_agent_id

    async def list_all(self, project_id):
        if project_id != ISSUE_ID:
            return ()
        return (type("Snapshot", (), {"created_by_agent_id": self.created_by_agent_id})(),)


class _FakePlans:
    def __init__(self, *plans):
        self.plans = plans

    async def list_all(self):
        return self.plans


class _FakeDirectory:
    def __init__(self, organization_id=None):
        self.organization_id = organization_id

    async def get_view(self, agent_id):
        return type(
            "Principal", (), {"organization_id": self.organization_id}
        )()


class _FakeAudit:
    def __init__(self):
        self.events = []

    async def append(self, event):
        self.events.append(event)


ISSUE_ID = uuid4()


def _service(plans=None, directory=None, audit=None):
    return IssueArchiveService(
        InMemoryIssueArchiveStore(),
        _FakeSnapshots(),
        plans if plans is not None else _FakePlans(),
        directory if directory is not None else _FakeDirectory(),
        audit if audit is not None else _FakeAudit(),
    )


def test_archive_refuses_an_in_progress_round() -> None:
    """A running round is live work; hiding it would orphan the console."""

    plan = type("Plan", (), {"project_id": ISSUE_ID, "status": ExecutionPlanStatus.IN_PROGRESS})()
    service = _service(plans=_FakePlans(plan))
    try:
        asyncio.run(service.archive(ISSUE_ID))
    except IssueArchiveConflict as error:
        assert "in-progress" in str(error)
    else:  # pragma: no cover
        raise AssertionError("expected IssueArchiveConflict")


def test_archive_records_an_attribution_and_a_single_event() -> None:
    audit = _FakeAudit()
    organization_id = uuid4()
    creator_id = uuid4()
    service = IssueArchiveService(
        InMemoryIssueArchiveStore(),
        _FakeSnapshots(created_by_agent_id=creator_id),
        _FakePlans(),
        _FakeDirectory(organization_id=organization_id),
        audit,
    )

    archive = asyncio.run(service.archive(ISSUE_ID))
    assert archive.issue_id == ISSUE_ID
    # The event names the issue's workspace from its creator, not from a caller.
    assert audit.events[0].organization_id == organization_id
    assert audit.events[0].event_type == "IssueArchived"

    # Replay: same view, no second audit row.
    again = asyncio.run(service.archive(ISSUE_ID))
    assert again == archive
    assert len(audit.events) == 1


def test_archive_of_an_unknown_issue_is_not_found() -> None:
    service = _service()
    try:
        asyncio.run(service.archive(uuid4()))
    except IssueArchiveNotFound as error:
        assert "not found" in str(error)
    else:  # pragma: no cover
        raise AssertionError("expected IssueArchiveNotFound")
