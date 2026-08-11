"""Workspace registry endpoints (contract v0.3 §2): /console/organizations.

Covers auth, creation with the auto-registered ORGANIZATION_LEADER, idempotent
replay, name conflict under a different key, listing with agent_count, and the
closure that motivated B-2: the auto-created leader can immediately open an
issue via the intake endpoint (v0.3 §1).
"""

import asyncio
from uuid import UUID

from fastapi.testclient import TestClient

from repomesh.bootstrap.app import create_app
from repomesh.bootstrap.container import ApplicationContainer
from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalStatus,
    AgentRole,
)
from repomesh.settings import get_settings

HEADERS = {"Authorization": "Bearer internal-secret"}


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
            assert leader.agentteams_resource_name == "rm-org-leader-acme-delivery"

            # Idempotent replay: 200, same ids.
            replay = client.post(
                "/api/v1/console/organizations", json=payload, headers=HEADERS
            )
            assert replay.status_code == 200
            assert replay.json()["organization_id"] == body["organization_id"]
            assert replay.json()["leader_agent_id"] == body["leader_agent_id"]

            # Same name under a different key is a conflict, not a replay.
            conflict = client.post(
                "/api/v1/console/organizations",
                json={"name": "acme-delivery", "idempotency_key": "ws-key-2"},
                headers=HEADERS,
            )
            assert conflict.status_code == 409

            # Listing shows the workspace with its active-agent count.
            listed = client.get("/api/v1/console/organizations", headers=HEADERS).json()
            rows = {row["name"]: row for row in listed["organizations"]}
            assert rows["acme-delivery"]["organization_id"] == body["organization_id"]
            assert rows["acme-delivery"]["agent_count"] == 1

            # Closure check (why B-2 exists): the auto-created leader can open
            # an issue right away through the intake endpoint.
            intake = client.post(
                "/api/v1/issues",
                json={
                    "requirement_text": "新工作区首个需求",
                    "created_by_agent_id": str(leader_id),
                    "idempotency_key": "ws-first-issue",
                },
                headers=HEADERS,
            )
            assert intake.status_code == 201
            assert intake.json()["organization_id"] == str(organization_id)
    finally:
        get_settings.cache_clear()
