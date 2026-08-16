"""Workspace registry endpoints (contract v0.3 §2): /console/organizations.

Covers auth, creation with the auto-registered ORGANIZATION_LEADER, idempotent
replay, name conflict under a different key, listing with agent_count, and the
closure that motivated B-2: the auto-created leader can immediately open an
issue via the intake endpoint (v0.3 §1). The §6 security revision adds: the
caller-scoped list filter and audit attribution (S-6), insert-first conflict
arbitration (S-7), and the repairable leader-name-conflict path with its
exactly-once audit (S-8).
"""

import asyncio
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from repomesh.bootstrap.app import create_app
from repomesh.bootstrap.container import ApplicationContainer
from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalStatus,
    AgentRole,
)
from repomesh.persistence.models.platform import AuditEventRecord
from repomesh.settings import get_settings

HEADERS = {"Authorization": "Bearer internal-secret"}


def _registration_audits(
    container: ApplicationContainer, organization_id: UUID
) -> list[AuditEventRecord]:
    async def rows():
        async with container.database.transaction() as session:
            result = await session.scalars(
                select(AuditEventRecord).where(
                    AuditEventRecord.event_type == "OrganizationRegistered",
                    AuditEventRecord.aggregate_id == organization_id,
                )
            )
            return list(result.all())

    return asyncio.run(rows())


def test_organization_registry_over_http(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", "internal-secret")
    get_settings.cache_clear()
    try:
        with TestClient(create_app(application_container)) as client:
            payload = {"name": "acme-delivery", "idempotency_key": "ws-key-1"}

            # Auth on both methods.
            assert client.get("/api/v1/console/organizations").status_code == 401
            assert client.post("/api/v1/console/organizations", json=payload).status_code == 401

            # S-5-style hardening applies here too: short keys are rejected.
            short = {"name": "short-key-ws", "idempotency_key": "1"}
            assert (
                client.post(
                    "/api/v1/console/organizations", json=short, headers=HEADERS
                ).status_code
                == 422
            )

            # First creation: 201, organization + leader registered together.
            created = client.post(
                "/api/v1/console/organizations", json=payload, headers=HEADERS
            )
            assert created.status_code == 201
            body = created.json()
            organization_id = UUID(body["organization_id"])
            leader_id = UUID(body["leader_agent_id"])
            assert body["name"] == "acme-delivery"

            leader = asyncio.run(
                application_container.agent_directory.get_view(leader_id)
            )
            assert leader is not None
            assert leader.role is AgentRole.ORGANIZATION_LEADER
            assert leader.status is AgentPrincipalStatus.ACTIVE
            assert leader.organization_id == organization_id
            # S-8: auto-derived names carry the organization suffix so similar
            # workspace names can never collide on the platform-wide binding.
            assert (
                leader.agentteams_resource_name
                == f"rm-org-leader-acme-delivery-{organization_id.hex[:8]}"
            )

            # S-6: the audit row is attributed to a distinguishable credential
            # (token fingerprint here — no human session in this client), not
            # a hardcoded label.
            audits = _registration_audits(application_container, organization_id)
            assert len(audits) == 1
            assert audits[0].actor_id.startswith("action-token:")

            # Idempotent replay: 200, same ids.
            replay = client.post(
                "/api/v1/console/organizations", json=payload, headers=HEADERS
            )
            assert replay.status_code == 200
            assert replay.json()["organization_id"] == body["organization_id"]
            assert replay.json()["leader_agent_id"] == body["leader_agent_id"]

            # S-8 resilience: a replay that derives (or passes) a different
            # leader name converges on the workspace's existing leader instead
            # of tripping the directory's idempotency fingerprint.
            renamed_replay = client.post(
                "/api/v1/console/organizations",
                json={**payload, "leader_resource_name": "acme-renamed-leader"},
                headers=HEADERS,
            )
            assert renamed_replay.status_code == 200
            assert renamed_replay.json()["leader_agent_id"] == body["leader_agent_id"]
            assert len(_registration_audits(application_container, organization_id)) == 1

            # Same name under a different key is a conflict, not a replay
            # (S-7: arbitrated by the unique constraint after insert, so the
            # concurrent loser gets this same 409 instead of a 500).
            conflict = client.post(
                "/api/v1/console/organizations",
                json={"name": "acme-delivery", "idempotency_key": "ws-key-2"},
                headers=HEADERS,
            )
            assert conflict.status_code == 409

            # S-8: an explicit leader name held by another workspace is a 409
            # — and the state it leaves is repairable, not an orphan: the same
            # idempotency_key with a different name completes the
            # registration, and exactly one audit row marks the completion.
            beta = {
                "name": "beta-ws",
                "idempotency_key": "ws-key-beta-1",
                "leader_resource_name": leader.agentteams_resource_name,
            }
            blocked = client.post(
                "/api/v1/console/organizations", json=beta, headers=HEADERS
            )
            assert blocked.status_code == 409
            repaired = client.post(
                "/api/v1/console/organizations",
                json={**beta, "leader_resource_name": "rm-org-leader-beta-repaired"},
                headers=HEADERS,
            )
            assert repaired.status_code == 200
            beta_org = UUID(repaired.json()["organization_id"])
            beta_leader = asyncio.run(
                application_container.agent_directory.get_view(
                    UUID(repaired.json()["leader_agent_id"])
                )
            )
            assert beta_leader is not None
            assert beta_leader.organization_id == beta_org
            assert len(_registration_audits(application_container, beta_org)) == 1

            # Listing shows the workspaces with their active-agent counts.
            listed = client.get("/api/v1/console/organizations", headers=HEADERS).json()
            rows = {row["name"]: row for row in listed["organizations"]}
            assert rows["acme-delivery"]["organization_id"] == body["organization_id"]
            assert rows["acme-delivery"]["agent_count"] == 1
            assert rows["beta-ws"]["agent_count"] == 1

            # S-6: the caller-scoped filter narrows the list to one workspace;
            # an unknown id filters to an honest empty set.
            scoped = client.get(
                f"/api/v1/console/organizations?organization_id={organization_id}",
                headers=HEADERS,
            ).json()
            assert [row["name"] for row in scoped["organizations"]] == ["acme-delivery"]
            empty = client.get(
                f"/api/v1/console/organizations?organization_id={uuid4()}",
                headers=HEADERS,
            ).json()
            assert empty["organizations"] == []

            # Closure check (why B-2 exists): the auto-created leader can open
            # an issue right away through the intake endpoint.
            intake = client.post(
                "/api/v1/issues",
                json={
                    "requirement_text": "新工作区首个需求",
                    "created_by_agent_id": str(leader_id),
                    "idempotency_key": "ws-first-issue",
                    "organization_id": str(organization_id),
                },
                headers=HEADERS,
            )
            assert intake.status_code == 201
            assert intake.json()["organization_id"] == str(organization_id)
    finally:
        get_settings.cache_clear()
