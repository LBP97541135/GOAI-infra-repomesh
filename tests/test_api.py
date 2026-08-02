from fastapi.testclient import TestClient

from repomesh.main import create_app


def test_health() -> None:
    with TestClient(create_app()) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        assert client.get("/health/ready").json() == {"status": "ready"}


def test_register_and_discover_repository() -> None:
    with TestClient(create_app()) as client:
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

