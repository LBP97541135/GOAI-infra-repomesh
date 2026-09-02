import asyncio
from uuid import uuid4

from fastapi.testclient import TestClient

from repomesh.bootstrap.app import create_app
from repomesh.modules.observability.operations import OperationalAction
from repomesh.settings import get_settings

TOKEN = "operations-test-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def client(application_container, monkeypatch) -> TestClient:
    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", TOKEN)
    monkeypatch.setenv("REPOMESH_OPERATIONS_BACKUP_CONFIGURED", "false")
    get_settings.cache_clear()
    return TestClient(create_app(application_container))


def test_operations_endpoints_require_auth(application_container, monkeypatch) -> None:
    api = client(application_container, monkeypatch)
    assert api.get("/api/v1/observe/operations/status").status_code == 401
    assert api.post("/api/v1/observe/operations/retention/run").status_code == 401


def test_status_reports_unconfigured_disaster_recovery_as_blocked(
    application_container, monkeypatch
) -> None:
    api = client(application_container, monkeypatch)
    response = api.get("/api/v1/observe/operations/status", headers=HEADERS)

    assert response.status_code == 200
    checks = {item["name"]: item for item in response.json()["checks"]}
    assert checks["alembic_single_head"]["state"] == "passed"
    assert checks["database_backup"]["state"] == "blocked_external"
    assert checks["restore_drill"]["state"] == "blocked_external"


def test_empty_correlation_is_honest_about_trace_approximation(
    application_container, monkeypatch
) -> None:
    api = client(application_container, monkeypatch)
    response = api.get(
        "/api/v1/observe/operations/correlation",
        params={"issue_id": str(uuid4())},
        headers=HEADERS,
    )

    assert response.status_code == 200
    sources = {item["source"]: item for item in response.json()["sources"]}
    assert sources["metrics"] == {
        "source": "metrics", "count": 0, "attribution": "exact"
    }
    assert sources["logs"]["attribution"] == "exact"
    assert sources["traces"]["attribution"] == "approximate"


def test_retention_endpoint_returns_bounded_counts(application_container, monkeypatch) -> None:
    api = client(application_container, monkeypatch)
    response = api.post(
        "/api/v1/observe/operations/retention/run", headers=HEADERS
    )
    assert response.status_code == 200
    assert response.json() == {
        "usage_deleted": 0,
        "logs_deleted": 0,
        "trace_sessions_deleted": 0,
    }


def test_pause_intake_action_blocks_new_issue_with_retry_after(
    application_container, monkeypatch
) -> None:
    api = client(application_container, monkeypatch)
    alert_id = uuid4()
    asyncio.run(
        application_container.operational_gate().apply(
            alert_id, OperationalAction.PAUSE_INTAKE
        )
    )

    response = api.post(
        "/api/v1/issues",
        headers=HEADERS,
        json={
            "requirement_text": "new work during saturation",
            "created_by_agent_id": str(uuid4()),
            "idempotency_key": "paused-intake-test",
        },
    )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "30"
    assert response.json()["detail"]["code"] == "intake_paused"
