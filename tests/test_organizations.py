from uuid import uuid4

from fastapi.testclient import TestClient

from repomesh.bootstrap import create_app
from repomesh.bootstrap.container import ApplicationContainer


def _admin_headers(client: TestClient) -> dict[str, str]:
    client.post(
        "/api/v1/auth/bootstrap",
        json={"username": "admin", "password": "strong-password-123", "display_name": "Admin"},
    )
    token = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "strong-password-123"},
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_organization_crud_lifecycle(application_container: ApplicationContainer) -> None:
    with TestClient(create_app(application_container)) as client:
        headers = _admin_headers(client)

        created = client.post(
            "/api/v1/organizations",
            headers=headers,
            json={
                "name": "Acme Corp",
                "scm_provider": "github",
                "scm_organization_url": "https://github.com/acme",
                "default_worker_count": 3,
            },
        )
        assert created.status_code == 201
        body = created.json()
        assert body["slug"] == "acme-corp"
        assert body["scm_provider"] == "github"
        assert body["default_worker_count"] == 3
        organization_id = body["id"]

        listed = client.get("/api/v1/organizations", headers=headers)
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()] == [organization_id]

        fetched = client.get(f"/api/v1/organizations/{organization_id}", headers=headers)
        assert fetched.status_code == 200
        assert fetched.json()["name"] == "Acme Corp"

        updated = client.patch(
            f"/api/v1/organizations/{organization_id}",
            headers=headers,
            json={"default_worker_count": 8, "default_model": "deepseek-chat"},
        )
        assert updated.status_code == 200
        assert updated.json()["default_worker_count"] == 8
        assert updated.json()["default_model"] == "deepseek-chat"

        missing = client.get(f"/api/v1/organizations/{uuid4()}", headers=headers)
        assert missing.status_code == 404


def test_duplicate_slug_is_rejected(application_container: ApplicationContainer) -> None:
    with TestClient(create_app(application_container)) as client:
        headers = _admin_headers(client)
        payload = {"name": "First", "slug": "shared", "scm_provider": "gitlab"}
        first = client.post("/api/v1/organizations", headers=headers, json=payload)
        assert first.status_code == 201
        conflict = client.post(
            "/api/v1/organizations",
            headers=headers,
            json={"name": "Second", "slug": "shared", "scm_provider": "github"},
        )
        assert conflict.status_code == 409


def test_create_organization_requires_admin(application_container: ApplicationContainer) -> None:
    with TestClient(create_app(application_container)) as client:
        admin_headers = _admin_headers(client)
        reviewer = client.post(
            "/api/v1/auth/accounts",
            headers=admin_headers,
            json={
                "username": "reviewer",
                "password": "reviewer-password-123",
                "display_name": "Reviewer",
            },
        )
        assert reviewer.status_code == 201
        reviewer_token = client.post(
            "/api/v1/auth/login",
            json={"username": "reviewer", "password": "reviewer-password-123"},
        ).json()["access_token"]

        forbidden = client.post(
            "/api/v1/organizations",
            headers={"Authorization": f"Bearer {reviewer_token}"},
            json={"name": "Nope", "scm_provider": "github"},
        )
        assert forbidden.status_code == 403

        client.cookies.clear()
        unauthenticated = client.post(
            "/api/v1/organizations",
            json={"name": "Nope", "scm_provider": "github"},
        )
        assert unauthenticated.status_code == 401


def test_onboarding_requires_existing_organization(
    application_container: ApplicationContainer,
) -> None:
    with TestClient(create_app(application_container)) as client:
        headers = _admin_headers(client)
        response = client.post(
            "/api/v1/setup/repositories/onboarding-jobs",
            headers=headers,
            json={
                "organization_id": str(uuid4()),
                "org_url": "https://github.com/acme",
            },
        )
        assert response.status_code == 404
