import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from repomesh.api.platform_setup import (
    RepositoryOnboardingJobRecord,
    recover_interrupted_onboarding_jobs,
)
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


def _create_org(client: TestClient, headers: dict[str, str]) -> str:
    created = client.post(
        "/api/v1/organizations",
        headers=headers,
        json={"name": "Acme Corp", "scm_provider": "github"},
    )
    assert created.status_code == 201
    return created.json()["id"]


def _insert_running_job(container: ApplicationContainer, organization_id: str) -> None:
    async def _run() -> None:
        async with container.database.transaction() as session:
            now = datetime.now(UTC)
            session.add(
                RepositoryOnboardingJobRecord(
                    id=uuid4(),
                    organization_id=UUID(organization_id),
                    org_url="https://github.com/acme",
                    status="running",
                    phase="scanning",
                    scan_workers=5,
                    default_worker_count=1,
                    requires_auth=False,
                    results=[],
                    error=None,
                    created_at=now,
                    updated_at=now,
                )
            )

    asyncio.run(_run())


def test_recover_marks_orphaned_jobs_interrupted(
    application_container: ApplicationContainer,
) -> None:
    with TestClient(create_app(application_container)) as client:
        headers = _admin_headers(client)
        org_id = _create_org(client, headers)
        _insert_running_job(application_container, org_id)

        recovered = asyncio.run(
            recover_interrupted_onboarding_jobs(application_container.database)
        )
        assert recovered == 1

        jobs = client.get("/api/v1/setup/repositories/onboarding-jobs", headers=headers).json()
        assert len(jobs) == 1
        assert jobs[0]["status"] == "interrupted"
        assert jobs[0]["error"]

        # A second sweep is a no-op — already-terminal jobs are left untouched.
        again = asyncio.run(
            recover_interrupted_onboarding_jobs(application_container.database)
        )
        assert again == 0


def test_duplicate_in_progress_onboarding_is_rejected(
    application_container: ApplicationContainer,
) -> None:
    with TestClient(create_app(application_container)) as client:
        headers = _admin_headers(client)
        org_id = _create_org(client, headers)
        _insert_running_job(application_container, org_id)

        conflict = client.post(
            "/api/v1/setup/repositories/onboarding-jobs",
            headers=headers,
            json={"organization_id": org_id, "org_url": "https://github.com/acme"},
        )
        assert conflict.status_code == 409
