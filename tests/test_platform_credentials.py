import asyncio

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select

from repomesh.bootstrap import create_app
from repomesh.bootstrap.container import ApplicationContainer
from repomesh.modules.platform_config import MODEL_API_KEY, PostgresPlatformCredentialStore
from repomesh.modules.platform_config.store import PlatformCredentialRecord


def _admin_headers(client: TestClient) -> dict[str, str]:
    created = client.post(
        "/api/v1/auth/bootstrap",
        json={
            "username": "admin",
            "password": "strong-password-123",
            "display_name": "Administrator",
        },
    )
    assert created.status_code == 201
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "strong-password-123"},
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_store_encrypts_at_rest_and_round_trips(
    application_container: ApplicationContainer,
) -> None:
    fernet = Fernet(Fernet.generate_key())
    store = PostgresPlatformCredentialStore(application_container.database, fernet)
    asyncio.run(store.put_many({MODEL_API_KEY: "sk-secret-1234"}, updated_by=None))

    async def raw_value() -> bytes:
        async with application_container.database.transaction() as session:
            record = await session.scalar(
                select(PlatformCredentialRecord).where(
                    PlatformCredentialRecord.key == MODEL_API_KEY
                )
            )
            assert record is not None
            return record.value_encrypted

    encrypted = asyncio.run(raw_value())
    assert encrypted != b"sk-secret-1234"
    assert b"sk-secret-1234" not in encrypted
    assert asyncio.run(store.get(MODEL_API_KEY)).value == "sk-secret-1234"


def test_admin_api_masks_and_deletes_credentials(
    application_container: ApplicationContainer,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "repomesh.modules.platform_config.store.get_credentials_fernet",
        lambda: Fernet(Fernet.generate_key()),
    )
    with TestClient(create_app(application_container)) as client:
        assert client.get("/api/v1/setup/credentials").status_code == 401
        assert client.get("/api/v1/setup/bootstrap").status_code == 401
        headers = _admin_headers(client)
        saved = client.put(
            "/api/v1/setup/credentials/model",
            headers=headers,
            json={
                "api_key": "sk-secret-1234",
                "base_url": "https://example.test/v1",
                "model": "test-model",
            },
        )
        assert saved.status_code == 200
        assert saved.json() == {
            "saved": True,
            "restarting": False,
            "restart_required": True,
        }
        bootstrap = client.get("/api/v1/setup/bootstrap", headers=headers)
        assert bootstrap.status_code == 200
        assert bootstrap.json()["state"] == "pending"
        assert bootstrap.json()["phase"] == "installing_agentteams"
        operation_id = bootstrap.json()["operation_id"]
        assert operation_id is not None

        replayed = client.put(
            "/api/v1/setup/credentials/model",
            headers=headers,
            json={"api_key": "sk-secret-5678", "model": "test-model"},
        )
        assert replayed.status_code == 200
        assert (
            client.get("/api/v1/setup/bootstrap", headers=headers).json()["operation_id"]
            == operation_id
        )
        setup = client.get("/api/v1/setup/status").json()
        dependencies = {item["id"]: item for item in setup["dependencies"]}
        assert dependencies["agentteams"]["state"] == "repairing"
        assert dependencies["matrix"]["state"] == "repairing"
        refused_retry = client.post("/api/v1/setup/bootstrap/retry", headers=headers)
        assert refused_retry.status_code == 409

        status = client.get("/api/v1/setup/credentials", headers=headers)
        assert status.status_code == 200
        body = status.json()
        assert body["model"]["api_key"]["set"] is True
        assert body["model"]["api_key"]["masked"] == "****5678"
        assert "sk-secret" not in status.text

        deleted = client.delete(
            "/api/v1/setup/credentials/model.api_key", headers=headers
        )
        assert deleted.status_code == 204
        status = client.get("/api/v1/setup/credentials", headers=headers).json()
        assert status["model"]["api_key"]["set"] is False


def test_setup_status_prefers_stored_model_credential(
    application_container: ApplicationContainer,
) -> None:
    fernet = Fernet(Fernet.generate_key())
    store = PostgresPlatformCredentialStore(application_container.database, fernet)
    asyncio.run(store.put_many({MODEL_API_KEY: "stored-key"}, updated_by=None))
    application_container._service_cache["platform_credential_store"] = store

    with TestClient(create_app(application_container)) as client:
        status = client.get("/api/v1/setup/status")
        assert status.status_code == 200
        assert status.json()["checks"]["model"] is True
