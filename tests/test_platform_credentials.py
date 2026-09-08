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


def test_manifest_flow_round_trips_github_app_credentials(
    application_container: ApplicationContainer,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "repomesh.modules.platform_config.store.get_credentials_fernet",
        lambda: Fernet(Fernet.generate_key()),
    )

    async def fake_exchange(code: str) -> dict:
        assert code == "the-code"
        return {
            "id": 123456,
            "slug": "repomesh-delivery-test",
            "pem": "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----",
            "webhook_secret": "whsec-123",
        }

    monkeypatch.setattr(
        "repomesh.api.platform_credentials._exchange_manifest_code", fake_exchange
    )
    with TestClient(create_app(application_container)) as client:
        refused = client.post("/api/v1/setup/credentials/github-app/manifest")
        assert refused.status_code == 401
        headers = _admin_headers(client)

        issued = client.post(
            "/api/v1/setup/credentials/github-app/manifest",
            headers=headers,
            json={"display_name": "RepoMesh Delivery (test)", "origin": "http://127.0.0.1:8100"},
        )
        assert issued.status_code == 200
        body = issued.json()
        assert body["github_url"] == "https://github.com/settings/apps/new"
        redirect_url = body["manifest"]["redirect_url"]
        # The wizard's browser origin wins over the proxied Host header
        # (testserver), because the callback must round-trip the same origin.
        assert redirect_url.startswith("http://127.0.0.1:8100/")
        assert redirect_url.endswith(
            "/api/v1/setup/credentials/github-app/manifest-callback"
        )
        assert body["manifest"]["name"] == "RepoMesh Delivery (test)"
        assert body["manifest"]["hook_attributes"]["active"] is False

        # An unknown state must not reach the exchange.
        rejected = client.get(
            "/api/v1/setup/credentials/github-app/manifest-callback",
            params={"state": "bogus", "code": "the-code"},
        )
        assert rejected.status_code == 200
        assert "失效" in rejected.text

        callback = client.get(
            "/api/v1/setup/credentials/github-app/manifest-callback",
            params={"state": body["state"], "code": "the-code"},
        )
        assert callback.status_code == 200
        assert "创建成功" in callback.text
        assert "repomesh-delivery-test" in callback.text
        # The return-to-console link uses the bound browser origin, not the
        # proxied Host header.
        assert 'href="http://127.0.0.1:8100/"' in callback.text

        # The state is single-use: replaying it must fail without side effects.
        replayed = client.get(
            "/api/v1/setup/credentials/github-app/manifest-callback",
            params={"state": body["state"], "code": "the-code"},
        )
        assert "失效" in replayed.text

        status = client.get("/api/v1/setup/credentials", headers=headers).json()
        assert status["github_app"]["app_id"]["set"] is True
        assert status["github_app"]["app_id"]["masked"] == "****3456"
        assert status["github_app"]["private_key"]["set"] is True
        assert status["github_app"]["webhook_secret"]["set"] is True
        setup = client.get("/api/v1/setup/status").json()
        assert setup["checks"]["github_app"] is True


def test_manifest_callback_reports_exchange_failure(
    application_container: ApplicationContainer,
    monkeypatch,
) -> None:
    import httpx

    monkeypatch.setattr(
        "repomesh.modules.platform_config.store.get_credentials_fernet",
        lambda: Fernet(Fernet.generate_key()),
    )

    async def failing_exchange(code: str) -> dict:
        raise httpx.HTTPStatusError(
            "422 Unprocessable Entity",
            request=httpx.Request("POST", "https://api.github.com/app-manifests/x/conversions"),
            response=httpx.Response(422, request=None),
        )

    monkeypatch.setattr(
        "repomesh.api.platform_credentials._exchange_manifest_code", failing_exchange
    )
    with TestClient(create_app(application_container)) as client:
        headers = _admin_headers(client)
        issued = client.post(
            "/api/v1/setup/credentials/github-app/manifest", headers=headers, json={}
        )
        state = issued.json()["state"]
        callback = client.get(
            "/api/v1/setup/credentials/github-app/manifest-callback",
            params={"state": state, "code": "the-code"},
        )
        assert callback.status_code == 200
        assert "交换失败" in callback.text
        # Nothing was stored.
        status = client.get("/api/v1/setup/credentials", headers=headers).json()
        assert status["github_app"]["app_id"]["set"] is False
