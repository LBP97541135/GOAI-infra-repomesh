import asyncio

import httpx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select

from repomesh.bootstrap import create_app
from repomesh.bootstrap.container import ApplicationContainer
from repomesh.modules.platform_config import (
    ALLOWED_KEYS,
    MANIFEST_STATE_CREATE,
    MODEL_API_KEY,
    GitHubAppManifestStateRecord,
    PostgresGitHubAppManifestStateStore,
    PostgresPlatformCredentialStore,
)
from repomesh.modules.platform_config.store import PlatformCredentialRecord

PEM = "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----"


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


def _fernet(monkeypatch) -> None:
    monkeypatch.setattr(
        "repomesh.modules.platform_config.store.get_credentials_fernet",
        lambda: Fernet(Fernet.generate_key()),
    )


def _stub_exchange(monkeypatch, payload: dict, calls: list[str] | None = None):
    async def fake_exchange(code: str) -> dict:
        if calls is not None:
            calls.append(code)
        return payload

    monkeypatch.setattr(
        "repomesh.api.platform_credentials._exchange_manifest_code", fake_exchange
    )


def _capture_restarts(monkeypatch) -> list[str]:
    """Record every request that asked for a process restart.

    Patched rather than driven through `settings.supervised` because what the
    tests actually need to pin down is *which hop* schedules the restart — the
    install return hop lands seconds later and must not find a container that is
    still running `alembic upgrade head`.
    """
    scheduled: list[str] = []

    def fake_schedule(background_tasks) -> bool:
        scheduled.append("restart")
        return False

    monkeypatch.setattr(
        "repomesh.api.platform_credentials._schedule_restart", fake_schedule
    )
    return scheduled


def _create_app_via_manifest(
    client: TestClient, headers: dict[str, str], *, replace: bool = False, **extra
) -> tuple[dict, httpx.Response]:
    """Run the create half of the flow and stop on the redirect to install."""
    issued = client.post(
        "/api/v1/setup/credentials/github-app/manifest",
        headers=headers,
        json={"origin": "http://127.0.0.1:8100", "replace": replace, **extra},
    )
    assert issued.status_code == 200, issued.text
    body = issued.json()
    callback = client.get(
        "/api/v1/setup/credentials/github-app/manifest-callback",
        params={"state": body["state"], "code": "the-code"},
        follow_redirects=False,
    )
    return body, callback


# ---------------------------------------------------------------------------
# Credential store
# ---------------------------------------------------------------------------


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
    _fernet(monkeypatch)
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

        deleted = client.delete("/api/v1/setup/credentials/model.api_key", headers=headers)
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


def test_allowed_keys_and_credential_shape_are_stable(
    application_container: ApplicationContainer,
    monkeypatch,
) -> None:
    """The registration fields are plaintext and must stay out of ALLOWED_KEYS.

    Putting `slug` or `installation_id` in the encrypted table would drag the
    unauthenticated status endpoint behind the encryption key, and would let
    `DELETE /setup/credentials/{key}` silently restart the process to erase a
    plaintext string. This pins that decision.
    """
    assert len(ALLOWED_KEYS) == 6
    _fernet(monkeypatch)
    with TestClient(create_app(application_container)) as client:
        headers = _admin_headers(client)
        body = client.get("/api/v1/setup/credentials", headers=headers).json()
        assert set(body) == {"model", "github_app", "github_app_registration"}
        assert set(body["model"]) == {"api_key", "base_url", "model"}
        assert set(body["github_app"]) == {"app_id", "private_key", "webhook_secret"}
        assert body["github_app_registration"] is None


# ---------------------------------------------------------------------------
# Manifest state machine
# ---------------------------------------------------------------------------


def test_manifest_state_is_hashed_and_claimable_once(
    application_container: ApplicationContainer,
) -> None:
    store = PostgresGitHubAppManifestStateStore(application_container.database)
    token = asyncio.run(
        store.issue(
            kind=MANIFEST_STATE_CREATE,
            requested_name="RepoMesh Delivery abc123",
            requested_origin="http://127.0.0.1:8100",
        )
    )

    async def stored_hashes() -> list[str]:
        async with application_container.database.transaction() as session:
            rows = await session.scalars(select(GitHubAppManifestStateRecord))
            return [row.state_hash for row in rows]

    hashes = asyncio.run(stored_hashes())
    assert hashes and token not in hashes

    first = asyncio.run(store.claim(token, kind=MANIFEST_STATE_CREATE))
    assert first is not None
    assert first.requested_name == "RepoMesh Delivery abc123"
    # The second caller — a browser refresh, a link prefetch, a TLS proxy
    # re-fetching the redirect — must not reach the exchange with the same code.
    assert asyncio.run(store.claim(token, kind=MANIFEST_STATE_CREATE)) is None
    # Nor may a create state be redeemed as an install state.
    other = asyncio.run(
        store.issue(kind=MANIFEST_STATE_CREATE, requested_name="x", requested_origin="y")
    )
    assert asyncio.run(store.claim(other, kind="install")) is None


def test_expired_manifest_state_cannot_be_claimed(
    application_container: ApplicationContainer,
) -> None:
    store = PostgresGitHubAppManifestStateStore(application_container.database)
    token = asyncio.run(
        store.issue(
            kind=MANIFEST_STATE_CREATE,
            requested_name="stale",
            requested_origin="http://127.0.0.1:8100",
            ttl_seconds=-1,
        )
    )
    assert asyncio.run(store.claim(token, kind=MANIFEST_STATE_CREATE)) is None


def test_released_state_survives_a_transport_failure(
    application_container: ApplicationContainer,
    monkeypatch,
) -> None:
    """The regression test for the bug that lost people their App.

    A timeout most likely means GitHub never saw the code, so the state has to
    come back — otherwise one network hiccup burns an App whose private key
    GitHub will never issue again.
    """
    _fernet(monkeypatch)
    attempts: list[str] = []

    async def flaky_exchange(code: str) -> dict:
        attempts.append(code)
        if len(attempts) == 1:
            raise httpx.TimeoutException("timed out")
        return {
            "id": 424242,
            "slug": "repomesh-retry",
            "pem": PEM,
            "owner": {"login": "octocat", "type": "User"},
        }

    monkeypatch.setattr(
        "repomesh.api.platform_credentials._exchange_manifest_code", flaky_exchange
    )
    with TestClient(create_app(application_container)) as client:
        headers = _admin_headers(client)
        issued = client.post(
            "/api/v1/setup/credentials/github-app/manifest", headers=headers, json={}
        )
        state = issued.json()["state"]
        first = client.get(
            "/api/v1/setup/credentials/github-app/manifest-callback",
            params={"state": state, "code": "the-code"},
        )
        assert first.status_code == 200
        assert "联系不上 GitHub" in first.text
        # Same URL, same state — reloading the page has to work.
        second = client.get(
            "/api/v1/setup/credentials/github-app/manifest-callback",
            params={"state": state, "code": "the-code"},
            follow_redirects=False,
        )
        assert second.status_code == 302
        assert len(attempts) == 2
        status = client.get("/api/v1/setup/credentials", headers=headers).json()
        assert status["github_app"]["app_id"]["set"] is True


# ---------------------------------------------------------------------------
# Creating the App
# ---------------------------------------------------------------------------


def test_manifest_rejects_a_foreign_origin(
    application_container: ApplicationContainer,
    monkeypatch,
) -> None:
    """`redirect_url` is absolute and client-supplied — an unchecked one is a
    stored open redirect carrying a code that converts into a private key."""
    _fernet(monkeypatch)
    with TestClient(create_app(application_container)) as client:
        headers = _admin_headers(client)
        for origin in ("https://evil.test", "http://testserver/../x", "ftp://testserver"):
            refused = client.post(
                "/api/v1/setup/credentials/github-app/manifest",
                headers=headers,
                json={"origin": origin},
            )
            assert refused.status_code == 400, origin
        # A different port on the same host is the entire point of the field:
        # nginx's `proxy_set_header Host $host` drops the published port.
        allowed = client.post(
            "/api/v1/setup/credentials/github-app/manifest",
            headers=headers,
            json={"origin": "http://testserver:8100"},
        )
        assert allowed.status_code == 200
        assert allowed.json()["manifest"]["redirect_url"].startswith("http://testserver:8100/")


def test_owner_login_is_validated_and_picks_the_organization_form(
    application_container: ApplicationContainer,
    monkeypatch,
) -> None:
    _fernet(monkeypatch)
    with TestClient(create_app(application_container)) as client:
        headers = _admin_headers(client)
        for bad in ("foo/bar?x=1", "-bad", "bad-", "a" * 40):
            refused = client.post(
                "/api/v1/setup/credentials/github-app/manifest",
                headers=headers,
                json={"owner_login": bad},
            )
            assert refused.status_code == 422, bad
        issued = client.post(
            "/api/v1/setup/credentials/github-app/manifest",
            headers=headers,
            json={"owner_login": "acme-corp"},
        )
        assert issued.status_code == 200
        assert (
            issued.json()["github_url"]
            == "https://github.com/organizations/acme-corp/settings/apps/new"
        )
        assert issued.json()["manifest"]["public"] is False


def test_app_name_is_generated_per_attempt_and_length_capped(
    application_container: ApplicationContainer,
    monkeypatch,
) -> None:
    """App names are globally unique on GitHub, so a fixed one can fail on the
    very first attempt — and GitHub reports that on its own page without ever
    calling back, leaving the server unaware."""
    _fernet(monkeypatch)
    with TestClient(create_app(application_container)) as client:
        headers = _admin_headers(client)
        names = set()
        for _ in range(3):
            issued = client.post(
                "/api/v1/setup/credentials/github-app/manifest", headers=headers, json={}
            )
            assert issued.status_code == 200
            name = issued.json()["app_name"]
            assert len(name) <= 34
            names.add(name)
        assert len(names) == 3
        too_long = client.post(
            "/api/v1/setup/credentials/github-app/manifest",
            headers=headers,
            json={"display_name": "R" * 60},
        )
        assert too_long.status_code == 422


def test_manifest_flow_round_trips_credentials_then_redirects_to_install(
    application_container: ApplicationContainer,
    monkeypatch,
) -> None:
    _fernet(monkeypatch)
    calls: list[str] = []
    _stub_exchange(
        monkeypatch,
        {
            "id": 123456,
            "slug": "repomesh-delivery-test",
            "pem": PEM,
            "webhook_secret": "whsec-123",
            "owner": {"login": "octocat", "type": "User"},
        },
        calls,
    )
    restarts = _capture_restarts(monkeypatch)
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
        manifest = body["manifest"]
        assert manifest["redirect_url"] == (
            "http://127.0.0.1:8100/api/v1/setup/credentials/github-app/manifest-callback"
        )
        assert manifest["setup_url"] == (
            "http://127.0.0.1:8100/api/v1/setup/credentials/github-app/installed"
        )
        assert manifest["name"] == "RepoMesh Delivery (test)"
        assert manifest["hook_attributes"]["active"] is False
        # The token mint sends the provider's permissions verbatim and GitHub
        # 422s anything the App was never granted, so the manifest has to be a
        # superset of them.
        assert set(manifest["default_permissions"]) >= {"contents", "pull_requests", "checks"}

        # An unknown state must not reach the exchange.
        rejected = client.get(
            "/api/v1/setup/credentials/github-app/manifest-callback",
            params={"state": "bogus", "code": "the-code"},
        )
        assert rejected.status_code == 200
        assert "失效" in rejected.text
        assert calls == []

        callback = client.get(
            "/api/v1/setup/credentials/github-app/manifest-callback",
            params={"state": body["state"], "code": "the-code"},
            follow_redirects=False,
        )
        assert callback.status_code == 302
        assert callback.headers["location"].startswith(
            "https://github.com/apps/repomesh-delivery-test/installations/new?state="
        )
        # Not on this hop: the user returns to `setup_url` within seconds, while
        # a restart has to run `alembic upgrade head` before uvicorn comes back.
        assert restarts == []

        # Replaying the state must fail without touching GitHub again.
        replayed = client.get(
            "/api/v1/setup/credentials/github-app/manifest-callback",
            params={"state": body["state"], "code": "the-code"},
            follow_redirects=False,
        )
        assert "失效" in replayed.text
        assert calls == ["the-code"]

        status = client.get("/api/v1/setup/credentials", headers=headers).json()
        assert status["github_app"]["app_id"]["masked"] == "****3456"
        assert status["github_app"]["private_key"]["set"] is True
        assert status["github_app"]["webhook_secret"]["set"] is True
        registration = status["github_app_registration"]
        assert registration["slug"] == "repomesh-delivery-test"
        assert registration["owner_login"] == "octocat"
        assert registration["installed"] is False
        assert registration["install_url"] == (
            "https://github.com/apps/repomesh-delivery-test/installations/new"
        )

        setup = client.get("/api/v1/setup/status").json()
        assert setup["checks"]["github_app"] is True
        # Created is not installed, and neither is loaded into this process.
        assert setup["checks"]["github_app_installed"] is False
        assert setup["checks"]["github_app_active"] is False
        dependencies = {item["id"]: item for item in setup["dependencies"]}
        assert dependencies["github_app_installed"]["required"] is False
        assert dependencies["github_app_active"]["required"] is False
        assert setup["ready_for_project_creation"] is False


def test_callback_pages_use_relative_links(
    application_container: ApplicationContainer,
    monkeypatch,
) -> None:
    """The browser is already on the correct external origin when this renders;
    the absolute form used to lose the published port to nginx."""
    _fernet(monkeypatch)
    _stub_exchange(
        monkeypatch,
        {
            "id": 1,
            "slug": "s",
            "pem": PEM,
            "owner": {"login": "acme", "type": "Organization"},
        },
    )
    with TestClient(create_app(application_container)) as client:
        headers = _admin_headers(client)
        _, callback = _create_app_via_manifest(client, headers, owner_login="other-org")
        assert callback.status_code == 200
        assert 'href="/"' in callback.text
        assert "127.0.0.1" not in callback.text
        assert "testserver" not in callback.text


def test_owner_mismatch_keeps_the_credentials_and_says_so(
    application_container: ApplicationContainer,
    monkeypatch,
) -> None:
    """A private App can only be installed on its owner's account, so landing
    under the wrong one is a dead end the user has to hear about now."""
    _fernet(monkeypatch)
    _stub_exchange(
        monkeypatch,
        {
            "id": 777,
            "slug": "wrong-owner",
            "pem": PEM,
            "owner": {"login": "someone-else", "type": "User"},
        },
    )
    restarts = _capture_restarts(monkeypatch)
    with TestClient(create_app(application_container)) as client:
        headers = _admin_headers(client)
        _, callback = _create_app_via_manifest(client, headers, owner_login="acme")
        assert callback.status_code == 200
        assert "someone-else" in callback.text
        assert "acme" in callback.text
        # The credentials are valid, so they stay — and the process still has to
        # restart to pick them up.
        assert restarts == ["restart"]
        status = client.get("/api/v1/setup/credentials", headers=headers).json()
        assert status["github_app"]["app_id"]["set"] is True
        assert status["github_app_registration"]["owner_login"] == "someone-else"
        assert status["github_app_registration"]["requested_owner_login"] == "acme"


def test_existing_installation_skips_the_install_hop(
    application_container: ApplicationContainer,
    monkeypatch,
) -> None:
    _fernet(monkeypatch)
    _stub_exchange(
        monkeypatch,
        {
            "id": 999,
            "slug": "already-installed",
            "pem": PEM,
            "owner": {"login": "octocat", "type": "User"},
            "installations_count": 1,
        },
    )

    async def fake_list(app_id, private_key, **kwargs):
        assert app_id == 999
        return [{"id": 4242, "account": {"login": "octocat"}}]

    monkeypatch.setattr(
        "repomesh.api.platform_credentials.list_app_installations", fake_list
    )
    restarts = _capture_restarts(monkeypatch)
    with TestClient(create_app(application_container)) as client:
        headers = _admin_headers(client)
        _, callback = _create_app_via_manifest(client, headers)
        assert callback.status_code == 200
        assert "已就绪" in callback.text
        assert restarts == ["restart"]
        setup = client.get("/api/v1/setup/status").json()
        assert setup["checks"]["github_app_installed"] is True


def test_second_app_needs_an_explicit_replace(
    application_container: ApplicationContainer,
    monkeypatch,
) -> None:
    """Overwriting the credentials silently abandons a working App on GitHub
    while every status row stays green."""
    _fernet(monkeypatch)
    _stub_exchange(
        monkeypatch,
        {
            "id": 111,
            "slug": "first-app",
            "pem": PEM,
            "owner": {"login": "octocat", "type": "User"},
        },
    )
    with TestClient(create_app(application_container)) as client:
        headers = _admin_headers(client)
        _, callback = _create_app_via_manifest(client, headers)
        assert callback.status_code == 302

        refused = client.post(
            "/api/v1/setup/credentials/github-app/manifest", headers=headers, json={}
        )
        assert refused.status_code == 409
        assert "first-app" in refused.json()["detail"]

        accepted = client.post(
            "/api/v1/setup/credentials/github-app/manifest",
            headers=headers,
            json={"replace": True},
        )
        assert accepted.status_code == 200


# ---------------------------------------------------------------------------
# Installing the App
# ---------------------------------------------------------------------------


def test_install_callback_records_the_installation_without_state(
    application_container: ApplicationContainer,
    monkeypatch,
) -> None:
    """GitHub documents `state` round-tripping on the OAuth callback, not on
    `setup_url`, so the flow must not depend on getting it back."""
    _fernet(monkeypatch)
    _stub_exchange(
        monkeypatch,
        {
            "id": 5150,
            "slug": "repomesh-install",
            "pem": PEM,
            "owner": {"login": "octocat", "type": "User"},
        },
    )

    async def fake_fetch(app_id, private_key, installation_id, **kwargs):
        assert app_id == 5150
        assert private_key == PEM.encode("utf-8") + b"\n"
        return {
            "id": installation_id,
            "app_id": 5150,
            "account": {"login": "octocat", "type": "User"},
        }

    monkeypatch.setattr(
        "repomesh.api.platform_credentials.fetch_app_installation", fake_fetch
    )
    restarts = _capture_restarts(monkeypatch)
    with TestClient(create_app(application_container)) as client:
        headers = _admin_headers(client)
        _, callback = _create_app_via_manifest(client, headers)
        assert callback.status_code == 302
        assert restarts == []

        installed = client.get(
            "/api/v1/setup/credentials/github-app/installed",
            params={"installation_id": 8899},
        )
        assert installed.status_code == 200
        assert "已安装完成" in installed.text
        # The restart belongs to this hop and no earlier one.
        assert restarts == ["restart"]

        status = client.get("/api/v1/setup/credentials", headers=headers).json()
        registration = status["github_app_registration"]
        assert registration["installed"] is True
        assert registration["installation_id"] == 8899
        assert registration["installation_account_login"] == "octocat"

        setup = client.get("/api/v1/setup/status").json()
        assert setup["checks"]["github_app_installed"] is True
        # Saved is not loaded: the token provider is built once, at boot.
        assert setup["checks"]["github_app_active"] is False


def test_forged_or_foreign_installation_ids_are_rejected(
    application_container: ApplicationContainer,
    monkeypatch,
) -> None:
    """GitHub's own docs warn the `installation_id` on `setup_url` can be
    spoofed; the App JWT lookup is what closes that."""
    _fernet(monkeypatch)
    _stub_exchange(
        monkeypatch,
        {
            "id": 6001,
            "slug": "repomesh-forged",
            "pem": PEM,
            "owner": {"login": "octocat", "type": "User"},
        },
    )
    answers: list[dict | None] = [None, {"id": 1, "app_id": 424242, "account": {}}]

    async def fake_fetch(app_id, private_key, installation_id, **kwargs):
        return answers.pop(0)

    monkeypatch.setattr(
        "repomesh.api.platform_credentials.fetch_app_installation", fake_fetch
    )
    with TestClient(create_app(application_container)) as client:
        headers = _admin_headers(client)
        _create_app_via_manifest(client, headers)

        for _ in range(2):
            refused = client.get(
                "/api/v1/setup/credentials/github-app/installed",
                params={"installation_id": 13},
            )
            assert refused.status_code == 200
            assert "无法确认" in refused.text
            registration = client.get(
                "/api/v1/setup/credentials", headers=headers
            ).json()["github_app_registration"]
            assert registration["installed"] is False


def test_verify_installation_is_the_manual_way_back(
    application_container: ApplicationContainer,
    monkeypatch,
) -> None:
    """GitHub redirects to `setup_url` at install time only, so anyone who
    installed from GitHub's own UI never triggers the callback — and
    `setup_url` is a fossil of the origin the App was created on."""
    _fernet(monkeypatch)
    _stub_exchange(
        monkeypatch,
        {
            "id": 7002,
            "slug": "repomesh-verify",
            "pem": PEM,
            "owner": {"login": "acme", "type": "Organization"},
        },
    )

    async def fake_list(app_id, private_key, **kwargs):
        return [
            {"id": 11, "account": {"login": "somebody-else"}},
            {"id": 22, "account": {"login": "ACME"}},
        ]

    monkeypatch.setattr(
        "repomesh.api.platform_credentials.list_app_installations", fake_list
    )
    with TestClient(create_app(application_container)) as client:
        # Before signing in — TestClient keeps the session cookie afterwards, so
        # this has to come first to mean anything.
        assert (
            client.post("/api/v1/setup/credentials/github-app/verify-installation").status_code
            == 401
        )
        headers = _admin_headers(client)
        missing = client.post(
            "/api/v1/setup/credentials/github-app/verify-installation", headers=headers
        )
        assert missing.status_code == 404

        _create_app_via_manifest(client, headers, owner_login="acme")
        verified = client.post(
            "/api/v1/setup/credentials/github-app/verify-installation", headers=headers
        )
        assert verified.status_code == 200
        # GitHub logins are case-insensitive but case-preserving, so the match
        # has to fold rather than compare raw.
        assert verified.json()["installation_id"] == 22
        assert verified.json()["account"] == "ACME"

        setup = client.get("/api/v1/setup/status").json()
        assert setup["checks"]["github_app_installed"] is True


# ---------------------------------------------------------------------------
# Status endpoint
# ---------------------------------------------------------------------------


def test_status_degrades_instead_of_failing_when_credentials_cannot_be_read(
    application_container: ApplicationContainer,
    monkeypatch,
) -> None:
    """This endpoint takes no authentication and is the wizard's only window
    into what is wrong. Rotating the encryption key must not turn it into a
    500 that cannot explain itself."""
    _fernet(monkeypatch)
    with TestClient(create_app(application_container)) as client:
        store = application_container.platform_credential_store()

        async def boom(keys):
            raise ValueError("InvalidToken")

        monkeypatch.setattr(store, "get_many", boom)
        status = client.get("/api/v1/setup/status")
        assert status.status_code == 200
        checks = status.json()["checks"]
        assert checks["model"] is False
        assert checks["github_app"] is False
        # Unrelated checks still answer truthfully.
        assert checks["database"] is True


def test_manifest_callback_reports_exchange_failure_with_a_rescue_route(
    application_container: ApplicationContainer,
    monkeypatch,
) -> None:
    """When the exchange fails we hold neither the App ID nor the slug, so the
    server-generated name is the user's only thread back to the orphan."""
    _fernet(monkeypatch)

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
            "/api/v1/setup/credentials/github-app/manifest",
            headers=headers,
            json={"owner_login": "acme"},
        )
        state = issued.json()["state"]
        app_name = issued.json()["app_name"]
        callback = client.get(
            "/api/v1/setup/credentials/github-app/manifest-callback",
            params={"state": state, "code": "the-code"},
        )
        assert callback.status_code == 200
        assert "交换失败" in callback.text
        assert app_name in callback.text
        # The organization settings page, not the personal one.
        assert "https://github.com/organizations/acme/settings/apps" in callback.text
        # The code is spent, so the state must not be handed back for a retry
        # that cannot possibly succeed.
        retried = client.get(
            "/api/v1/setup/credentials/github-app/manifest-callback",
            params={"state": state, "code": "the-code"},
        )
        assert "失效" in retried.text

        status = client.get("/api/v1/setup/credentials", headers=headers).json()
        assert status["github_app"]["app_id"]["set"] is False
        assert status["github_app_registration"] is None
