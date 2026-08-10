from uuid import uuid4

from fastapi.testclient import TestClient

from repomesh.bootstrap import create_app
from repomesh.bootstrap.container import ApplicationContainer
from repomesh.settings import get_settings


def test_health(application_container: ApplicationContainer) -> None:
    with TestClient(create_app(application_container)) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        assert client.get("/health/ready").json() == {"status": "ready"}


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
