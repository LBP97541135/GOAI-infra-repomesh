from fastapi.testclient import TestClient

from repomesh.bootstrap import create_app
from repomesh.bootstrap.container import ApplicationContainer


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

