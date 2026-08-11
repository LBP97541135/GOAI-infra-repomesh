import asyncio
from uuid import uuid4

from fastapi.testclient import TestClient

from repomesh.bootstrap import create_app
from repomesh.bootstrap.container import ApplicationContainer
from repomesh.modules.agent_directory.application import (
    CreateAgent,
    CreateAgentRequest,
    CreateRepositoryAgentTeam,
    CreateRepositoryAgentTeamRequest,
)
from repomesh.modules.agent_directory.contracts import AgentRole
from repomesh.settings import get_settings


def test_health(application_container: ApplicationContainer) -> None:
    with TestClient(create_app(application_container)) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        assert client.get("/health/ready").json() == {"status": "ready"}


def test_local_account_bootstrap_login_and_session_authentication(
    application_container: ApplicationContainer,
) -> None:
    with TestClient(create_app(application_container)) as client:
        created = client.post(
            "/api/v1/auth/bootstrap",
            json={
                "username": "admin",
                "password": "strong-password-123",
                "display_name": "Administrator",
            },
        )
        assert created.status_code == 201
        assert created.json()["is_admin"] is True

        duplicate = client.post(
            "/api/v1/auth/bootstrap",
            json={
                "username": "other",
                "password": "strong-password-456",
                "display_name": "Other",
            },
        )
        assert duplicate.status_code == 409

        login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "strong-password-123"},
        )
        assert login.status_code == 200
        token = login.json()["access_token"]
        authenticated = client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert authenticated.status_code == 200
        assert authenticated.json()["username"] == "admin"
        assert client.get("/api/v1/auth/me").status_code == 200

        logged_out = client.post(
            "/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"}
        )
        assert logged_out.status_code == 204
        assert client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        ).status_code == 401


def test_authenticated_project_mode_and_checkpoint_decision_api(
    application_container: ApplicationContainer,
) -> None:
    organization_id = uuid4()
    repository_id = uuid4()
    project_id = uuid4()

    async def agents():
        leader = await CreateAgent(application_container.agent_directory).execute(
            CreateAgentRequest(
                organization_id=organization_id,
                role=AgentRole.ORGANIZATION_LEADER,
                agentteams_resource_name="human-api-org-leader",
            ),
            idempotency_key="human-api-org-leader",
        )
        team = await CreateRepositoryAgentTeam(
            application_container.agent_directory
        ).execute(
            CreateRepositoryAgentTeamRequest(
                organization_id=organization_id,
                organization_leader_id=leader.principal.id,
                repository_id=repository_id,
                leader_agentteams_resource_name="human-api-repo-leader",
                worker_agentteams_resource_names=("human-api-worker",),
            ),
            idempotency_key="human-api-repo-team",
        )
        return leader.principal, team

    organization_leader, team = asyncio.run(agents())
    with TestClient(create_app(application_container)) as client:
        client.post(
            "/api/v1/auth/bootstrap",
            json={
                "username": "admin",
                "password": "strong-password-123",
                "display_name": "Administrator",
            },
        )
        admin_login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "strong-password-123"},
        ).json()
        admin_headers = {"Authorization": f"Bearer {admin_login['access_token']}"}
        reviewer = client.post(
            "/api/v1/auth/accounts",
            headers=admin_headers,
            json={
                "username": "reviewer",
                "password": "reviewer-password-123",
                "display_name": "Reviewer",
            },
        ).json()
        created = client.post(
            "/api/v1/projects/topologies",
            headers=admin_headers,
            json={
                "organization_id": str(organization_id),
                "project_id": str(project_id),
                "organization_leader_id": str(organization_leader.id),
                "repository_teams": [
                    {
                        "repository_id": str(repository_id),
                        "leader_agent_id": str(team.leader.id),
                        "worker_agent_ids": [str(team.workers[0].id)],
                    }
                ],
                "execution_mode": "supervised",
                "required_checkpoints": ["execution"],
                "human_grants": [
                    {
                        "human_principal_id": reviewer["id"],
                            "role": "project_supervisor",
                        "code_access": "read",
                        "control_actions": [
                            "approve_checkpoint",
                            "request_changes",
                            "pause_project",
                            "resume_project",
                            "cancel_project",
                        ],
                            "repository_id": None,
                            "path_patterns": [],
                    }
                ],
                "idempotency_key": "human-api-project",
            },
        )
        assert created.status_code == 201
        assert created.json()["execution_mode"] == "supervised"

        reviewer_login = client.post(
            "/api/v1/auth/login",
            json={"username": "reviewer", "password": "reviewer-password-123"},
        ).json()
        pending_gate = client.get(
            f"/api/v1/projects/{project_id}/checkpoint-gate",
            headers=admin_headers,
            params={
                "checkpoint": "execution",
                "evidence_version": "task:example:v1",
                "repository_id": str(repository_id),
            },
        )
        assert pending_gate.json()["reason"] == "human_checkpoint_pending"
        reviewer_headers = {
            "Authorization": f"Bearer {reviewer_login['access_token']}"
        }
        agents_response = client.get("/api/v1/agents", headers=reviewer_headers)
        assert agents_response.status_code == 200
        assert {item["id"] for item in agents_response.json()} == {
            str(organization_leader.id),
            str(team.leader.id),
            str(team.workers[0].id),
        }
        inbox = client.get(
            "/api/v1/review-requests?status=pending", headers=reviewer_headers
        )
        assert inbox.status_code == 200
        assert inbox.json()[0]["evidence_version"] == "task:example:v1"
        decision_payload = {
            "review_request_id": inbox.json()[0]["id"],
            "decision": "approved",
            "reason": "task is safe to execute",
        }
        approved = client.post(
            f"/api/v1/projects/{project_id}/checkpoint-decisions",
            headers=reviewer_headers,
            json=decision_payload,
        )
        assert approved.status_code == 200
        assert approved.json()["human_principal_id"] == reviewer["id"]
        assert client.get(
            "/api/v1/review-requests?status=pending", headers=reviewer_headers
        ).json() == []
        duplicate_decision = client.post(
            f"/api/v1/projects/{project_id}/checkpoint-decisions",
            headers=reviewer_headers,
            json=decision_payload,
        )
        assert duplicate_decision.status_code == 409

        for action, expected_status in (
            ("pause_project", "paused"),
            ("resume_project", "active"),
            ("cancel_project", "cancelled"),
        ):
            controlled = client.post(
                f"/api/v1/projects/{project_id}/control",
                headers=reviewer_headers,
                json={"action": action},
            )
            assert controlled.status_code == 200
            assert controlled.json()["operational_status"] == expected_status
        cannot_resume = client.post(
            f"/api/v1/projects/{project_id}/control",
            headers=reviewer_headers,
            json={"action": "resume_project"},
        )
        assert cannot_resume.status_code == 403


def test_register_and_discover_repository(application_container: ApplicationContainer) -> None:
    with TestClient(create_app(application_container)) as client:
        created = client.post(
            "/api/v1/repositories",
            json={
                "name": "billing-api",
                "url": "https://github.com/example/billing",
                "description": "Invoice and payment service",
                "topics": ["billing", "payment"],
                "languages": ["python"],
            },
        )
        assert created.status_code == 201

        discovered = client.post(
            "/api/v1/discovery", json={"requirement": "Add payment invoice support"}
        )
        assert discovered.status_code == 200
        assert discovered.json()[0]["repository_name"] == "billing-api"
        assert discovered.json()[0]["matched_terms"] == ["invoice", "payment"]


def test_worker_start_action_requires_internal_token(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", "internal-secret")
    get_settings.cache_clear()
    try:
        with TestClient(create_app(application_container)) as client:
            response = client.post(
                "/api/v1/agent-actions/start-worker-task",
                json={
                    "task_id": str(uuid4()),
                    "worker_agent_id": str(uuid4()),
                    "adapter_id": "claude-code",
                },
            )
        assert response.status_code == 401
    finally:
        get_settings.cache_clear()


def test_delivery_reconciliation_requires_token_and_configured_scm(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", "internal-secret")
    get_settings.cache_clear()
    try:
        with TestClient(create_app(application_container)) as client:
            unauthorized = client.post(
                f"/api/v1/delivery/change-sets/{uuid4()}/reconcile"
            )
            unavailable = client.post(
                f"/api/v1/delivery/change-sets/{uuid4()}/reconcile",
                headers={"Authorization": "Bearer internal-secret"},
            )
        assert unauthorized.status_code == 401
        assert unavailable.status_code == 503
        assert unavailable.json()["detail"] == "SCM adapter is not configured"
    finally:
        get_settings.cache_clear()


def test_worker_mcp_initializes_with_gateway_token(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    monkeypatch.setenv("REPOMESH_MCP_GATEWAY_TOKEN", "gateway-secret")
    get_settings.cache_clear()
    try:
        with TestClient(create_app(application_container)) as client:
            response = client.post(
                "/api/v1/mcp/worker",
                headers={"X-RepoMesh-Gateway-Token": "gateway-secret"},
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            )
        assert response.status_code == 200
        assert response.json()["result"]["serverInfo"]["name"] == (
            "repomesh-task-control"
        )
    finally:
        get_settings.cache_clear()


def test_worker_mcp_accepts_agentteams_bearer_gateway_token(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    monkeypatch.setenv("REPOMESH_MCP_GATEWAY_TOKEN", "agentteams-worker-key")
    get_settings.cache_clear()
    try:
        with TestClient(create_app(application_container)) as client:
            response = client.post(
                "/api/v1/mcp/worker",
                headers={"Authorization": "Bearer agentteams-worker-key"},
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            )
        assert response.status_code == 200
    finally:
        get_settings.cache_clear()


def test_worker_mcp_accepts_any_configured_agentteams_gateway_token(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    monkeypatch.delenv("REPOMESH_MCP_GATEWAY_TOKEN", raising=False)
    monkeypatch.setenv(
        "REPOMESH_MCP_GATEWAY_TOKENS",
        '["api-worker-key", "client-worker-key"]',
    )
    get_settings.cache_clear()
    try:
        with TestClient(create_app(application_container)) as client:
            response = client.post(
                "/api/v1/mcp/worker",
                headers={"Authorization": "Bearer client-worker-key"},
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            )
        assert response.status_code == 200
    finally:
        get_settings.cache_clear()


def test_worker_mcp_can_be_explicitly_enabled_for_local_direct_mode(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    monkeypatch.setenv("REPOMESH_ENVIRONMENT", "test")
    monkeypatch.setenv("REPOMESH_DIRECT_WORKER_MCP_ENABLED", "true")
    get_settings.cache_clear()
    try:
        with TestClient(create_app(application_container)) as client:
            response = client.post(
                "/api/v1/mcp/worker",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )
        assert response.status_code == 200
        assert response.json()["result"]["tools"][0]["name"] == "start_assigned_task"
    finally:
        get_settings.cache_clear()


def test_worker_mcp_direct_mode_is_forbidden_in_production(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    monkeypatch.setenv("REPOMESH_ENVIRONMENT", "production")
    monkeypatch.setenv("REPOMESH_DIRECT_WORKER_MCP_ENABLED", "true")
    get_settings.cache_clear()
    try:
        with TestClient(create_app(application_container)) as client:
            response = client.post(
                "/api/v1/mcp/worker",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )
        assert response.status_code == 503
    finally:
        get_settings.cache_clear()
