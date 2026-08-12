"""The console's face on the repository scanners.

Same work as the native RI endpoints, different rules about what the caller is
allowed to say. These tests are mostly about the difference.
"""

from fastapi.testclient import TestClient

from repomesh.bootstrap import create_app
from repomesh.bootstrap.container import ApplicationContainer
from repomesh.modules.repository_intelligence.application import scan_remote
from repomesh.modules.repository_intelligence.domain import AutoCard, RepositoryProfile
from repomesh.settings import get_settings

HEADERS = {"Authorization": "Bearer internal-secret"}


def _configure(monkeypatch) -> None:
    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", "internal-secret")
    monkeypatch.setenv("REPOMESH_REPOSITORY_SCAN_ALLOWED_HOSTS", "github.com")
    get_settings.cache_clear()


async def _one_repo(url: str, fetcher: object) -> RepositoryProfile:
    return RepositoryProfile(
        name="order-service",
        url=url,
        auto_card=AutoCard(top_dirs=("src",), recent_commits=("add wechat pay",)),
    )


async def _two_repos(url: str, fetcher: object, **kwargs: object) -> list[RepositoryProfile]:
    return [
        RepositoryProfile(name="order-service", url=f"{url}/order-service"),
        RepositoryProfile(name="payment-service", url=f"{url}/payment-service"),
    ]


def test_console_scan_paths_do_not_collide_with_the_console_read_model(
    application_container: ApplicationContainer,
) -> None:
    """``/console/repositories`` is a GET on the grid router and a POST here.

    Three routers share the ``/console`` prefix. This pins that the console
    write face was added without shadowing the read the console already uses.
    """

    with TestClient(create_app(application_container)) as client:
        paths = {
            (path, method)
            for path, spec in client.get("/openapi.json").json()["paths"].items()
            for method in spec
            if path.startswith("/api/v1/console/repositories")
        }

    assert ("/api/v1/console/repositories", "get") in paths
    assert ("/api/v1/console/repositories/scan-org", "post") in paths
    assert ("/api/v1/console/repositories/scan-repo", "post") in paths


def test_console_scan_requires_the_action_token(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """Both console writes take the same credential ``POST /issues`` takes."""

    _configure(monkeypatch)
    try:
        with TestClient(create_app(application_container)) as client:
            statuses = {
                path: client.post(path, json={}).status_code
                for path in (
                    "/api/v1/console/repositories/scan-org",
                    "/api/v1/console/repositories/scan-repo",
                )
            }
            wrong = client.post(
                "/api/v1/console/repositories/scan-org",
                headers={"Authorization": "Bearer wrong"},
                json={"org_url": "https://github.com/acme"},
            )
    finally:
        get_settings.cache_clear()

    assert set(statuses.values()) == {401}, statuses
    assert wrong.status_code == 401


def test_console_bodies_refuse_credential_fields(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """The bypass the native endpoints keep open is closed on this face.

    A token in a console body is rejected outright rather than ignored: if it
    were dropped silently the operator would think the console had their
    credential and read the empty result as "the private repos are not there".
    """

    _configure(monkeypatch)
    monkeypatch.setattr(scan_remote, "scan_org", _two_repos)
    try:
        with TestClient(create_app(application_container)) as client:
            with_token = client.post(
                "/api/v1/console/repositories/scan-org",
                headers=HEADERS,
                json={"org_url": "https://github.com/acme", "github_token": "ghp_smuggled"},
            )
            repo_with_token = client.post(
                "/api/v1/console/repositories/scan-repo",
                headers=HEADERS,
                json={"repo_url": "https://github.com/acme/orders", "gitlab_token": "glpat_x"},
            )
    finally:
        get_settings.cache_clear()

    assert with_token.status_code == 422
    assert repo_with_token.status_code == 422
    # The refusal names the field, so the caller learns why rather than guessing.
    assert "github_token" in with_token.text
    assert "gitlab_token" in repo_with_token.text


def test_console_scan_uses_the_server_env_credentials(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """Credentials come from env and reach the fetcher.

    Without this the "token not in the GUI" decision would quietly mean "no
    token at all", and every private repository would scan as missing.
    """

    _configure(monkeypatch)
    monkeypatch.setenv("REPOMESH_REPOSITORY_SCAN_GITHUB_TOKEN", "ghp_from_env")
    get_settings.cache_clear()
    seen: dict[str, object] = {}

    from repomesh.modules.repository_intelligence.infrastructure import platform as platform_module

    real_make_fetcher = platform_module.make_fetcher

    def _spy(platform, **kwargs):  # noqa: ANN001, ANN202
        seen.update(kwargs)
        return real_make_fetcher(platform, **kwargs)

    monkeypatch.setattr(platform_module, "make_fetcher", _spy)
    monkeypatch.setattr(scan_remote, "scan_org", _two_repos)
    try:
        with TestClient(create_app(application_container)) as client:
            response = client.post(
                "/api/v1/console/repositories/scan-org",
                headers=HEADERS,
                json={"org_url": "https://github.com/acme"},
            )
    finally:
        get_settings.cache_clear()

    assert response.status_code == 200
    assert seen["github_token"] == "ghp_from_env"


def test_console_org_scan_registers_and_reports_counts(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(scan_remote, "scan_org", _two_repos)
    try:
        with TestClient(create_app(application_container)) as client:
            first = client.post(
                "/api/v1/console/repositories/scan-org",
                headers=HEADERS,
                json={"org_url": "https://github.com/acme"},
            )
            again = client.post(
                "/api/v1/console/repositories/scan-org",
                headers=HEADERS,
                json={"org_url": "https://github.com/acme"},
            )
    finally:
        get_settings.cache_clear()

    body = first.json()
    assert body["org_url"] == "https://github.com/acme"
    assert (body["total_scanned"], body["registered"], body["skipped"], body["failed"]) == (
        2,
        2,
        0,
        0,
    )
    assert {r["name"] for r in body["repositories"]} == {"order-service", "payment-service"}
    # Whole re-scan is the only retry the console offers, so it must be safe.
    assert again.json()["registered"] == 0
    assert again.json()["skipped"] == 2


def test_console_repo_scan_registers_a_single_repository(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(scan_remote, "scan_single_repo", _one_repo)
    try:
        with TestClient(create_app(application_container)) as client:
            response = client.post(
                "/api/v1/console/repositories/scan-repo",
                headers=HEADERS,
                json={"repo_url": "https://github.com/acme/order-service"},
            )
    finally:
        get_settings.cache_clear()

    body = response.json()
    assert body["repo_url"] == "https://github.com/acme/order-service"
    assert (body["registered"], body["skipped"], body["failed"]) == (1, 0, 0)
    assert body["repositories"][0]["auto_card"]["recent_commits"] == ["add wechat pay"]


def test_console_scan_keeps_the_native_refusals(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """The console face reuses the guards; it does not re-implement them.

    A host off the allowlist, a group URL sent to the single-repo scan, and a
    failure that must not echo what the outbound request saw.
    """

    _configure(monkeypatch)

    async def _explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("connect to 10.0.0.7:5432 refused")

    monkeypatch.setattr(scan_remote, "scan_org", _explode)
    try:
        with TestClient(create_app(application_container)) as client:
            internal = client.post(
                "/api/v1/console/repositories/scan-org",
                headers=HEADERS,
                json={"org_url": "https://gitlab.internal.example/acme"},
            )
            group_as_repo = client.post(
                "/api/v1/console/repositories/scan-repo",
                headers=HEADERS,
                json={"repo_url": "https://github.com/acme"},
            )
            failed = client.post(
                "/api/v1/console/repositories/scan-org",
                headers=HEADERS,
                json={"org_url": "https://github.com/acme"},
            )
    finally:
        get_settings.cache_clear()

    assert internal.status_code == 400
    assert "allowlist" in internal.json()["detail"]
    assert group_as_repo.status_code == 400
    assert failed.status_code == 502
    assert failed.json()["detail"] == "organization scan failed"
    assert "10.0.0.7" not in failed.text
