"""The console's face on the repository scanners.

Same work as the native RI endpoints, two differences: what the caller is
allowed to say, and how long they wait. These tests are mostly about those.
"""

import asyncio
import gc
import threading
import time
from uuid import uuid4

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


def _poll(client: TestClient, task_id: str) -> dict:
    response = client.get(
        f"/api/v1/console/repositories/scan-tasks/{task_id}", headers=HEADERS
    )
    assert response.status_code == 200, response.text
    return response.json()


def _await_task(client: TestClient, task_id: str, *, attempts: int = 200) -> dict:
    """Poll until the scan stops running, the way the console does."""

    body = _poll(client, task_id)
    for _ in range(attempts):
        if body["status"] != "running":
            return body
        time.sleep(0.02)
        body = _poll(client, task_id)
    raise AssertionError(f"scan task never finished: {body}")


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
    assert ("/api/v1/console/repositories/scan-tasks/{task_id}", "get") in paths


def test_console_scan_requires_the_action_token(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """Both writes and the poll take the credential ``POST /issues`` takes.

    The poll is new, so it fails closed. It reports what a caller's scan found,
    which is not something to hand out because the id is hard to guess.
    """

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
            poll = client.get(f"/api/v1/console/repositories/scan-tasks/{uuid4()}")
            wrong = client.post(
                "/api/v1/console/repositories/scan-org",
                headers={"Authorization": "Bearer wrong"},
                json={"org_url": "https://github.com/acme"},
            )
    finally:
        get_settings.cache_clear()

    assert set(statuses.values()) == {401}, statuses
    assert poll.status_code == 401
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
            _await_task(client, response.json()["task_id"])
    finally:
        get_settings.cache_clear()

    assert response.status_code == 202
    assert seen["github_token"] == "ghp_from_env"


def test_console_org_scan_accepts_then_reports_counts(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """202 first, counts on the poll — and re-scanning stays safe."""

    _configure(monkeypatch)
    monkeypatch.setattr(scan_remote, "scan_org", _two_repos)
    try:
        with TestClient(create_app(application_container)) as client:
            accepted = client.post(
                "/api/v1/console/repositories/scan-org",
                headers=HEADERS,
                json={"org_url": "https://github.com/acme"},
            )
            first = _await_task(client, accepted.json()["task_id"])
            repeat = client.post(
                "/api/v1/console/repositories/scan-org",
                headers=HEADERS,
                json={"org_url": "https://github.com/acme"},
            )
            again = _await_task(client, repeat.json()["task_id"])
            listed = client.get("/api/v1/repositories")
    finally:
        get_settings.cache_clear()

    assert accepted.status_code == 202
    accepted_body = accepted.json()
    assert accepted_body["status"] == "running"
    assert accepted_body["kind"] == "organization"
    assert accepted_body["url"] == "https://github.com/acme"
    assert accepted_body["finished_at"] is None

    assert first["status"] == "succeeded"
    assert (first["total"], first["registered"], first["skipped"], first["failed"]) == (2, 2, 0, 0)
    assert first["error"] is None
    assert first["finished_at"] is not None

    # Whole re-scan is the only retry the console offers, so it must be safe.
    assert again["status"] == "succeeded"
    assert (again["registered"], again["skipped"]) == (0, 2)
    assert {r["name"] for r in listed.json()} == {"order-service", "payment-service"}


def test_console_org_scan_shows_partial_progress_while_it_runs(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """The point of 202: the console can see "n of m" before the scan ends.

    The stub blocks halfway, so a poll taken mid-scan is a real observation of
    a running task and not a finished one caught early. It also pins that the
    registration counts stay at zero until the scan is actually done — a
    partial count read as a result is exactly the dishonesty to avoid.
    """

    _configure(monkeypatch)
    release = threading.Event()

    async def _slow_org_scan(url: str, fetcher: object, **kwargs: object):  # noqa: ANN202
        on_progress = kwargs.get("on_progress")
        on_progress(1, 2, "order-service")
        while not release.is_set():
            await asyncio.sleep(0.01)
        on_progress(2, 2, "payment-service")
        return [
            RepositoryProfile(name="order-service", url=f"{url}/order-service"),
            RepositoryProfile(name="payment-service", url=f"{url}/payment-service"),
        ]

    monkeypatch.setattr(scan_remote, "scan_org", _slow_org_scan)
    try:
        with TestClient(create_app(application_container)) as client:
            accepted = client.post(
                "/api/v1/console/repositories/scan-org",
                headers=HEADERS,
                json={"org_url": "https://github.com/acme"},
            )
            task_id = accepted.json()["task_id"]

            mid = _poll(client, task_id)
            for _ in range(200):
                if mid["scanned"]:
                    break
                time.sleep(0.02)
                mid = _poll(client, task_id)

            release.set()
            finished = _await_task(client, task_id)
    finally:
        release.set()
        get_settings.cache_clear()

    assert mid["status"] == "running"
    assert (mid["scanned"], mid["total"]) == (1, 2)
    assert mid["last_scanned_repository"] == "order-service"
    # Nothing is written until every repository has been read.
    assert (mid["registered"], mid["skipped"], mid["failed"]) == (0, 0, 0)

    assert finished["status"] == "succeeded"
    assert (finished["scanned"], finished["registered"]) == (2, 2)


def test_a_running_scan_is_held_by_a_strong_reference(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """Positive control: the failure this guards against cannot be forced.

    ``asyncio`` keeps only a weak reference to a task nobody awaits, so a scan
    whose Task object is dropped may be collected mid-flight — leaving the
    record reading "running" forever and the console polling a scan that no
    longer exists. Dropping the reference does not reliably reproduce that
    within a test; the collector decides when, and it usually never gets the
    chance. Deleting the reference leaves every assertion here green, which is
    exactly why this asserts the mechanism rather than its symptom: while a
    scan is in flight the record holds its Task, and a collection cycle in the
    middle does not kill it.
    """

    from uuid import UUID

    from repomesh.modules.repository_intelligence.api import console

    _configure(monkeypatch)
    release = threading.Event()

    async def _blocked_scan(url: str, fetcher: object, **kwargs: object):  # noqa: ANN202
        while not release.is_set():
            await asyncio.sleep(0.01)
        return [RepositoryProfile(name="order-service", url=url)]

    monkeypatch.setattr(scan_remote, "scan_org", _blocked_scan)
    try:
        with TestClient(create_app(application_container)) as client:
            accepted = client.post(
                "/api/v1/console/repositories/scan-org",
                headers=HEADERS,
                json={"org_url": "https://github.com/acme"},
            )
            task_id = accepted.json()["task_id"]
            record = console._SCAN_TASKS[UUID(task_id)]  # noqa: SLF001

            assert isinstance(record.runner, asyncio.Task)
            assert not record.runner.done()

            gc.collect()
            assert _poll(client, task_id)["status"] == "running"

            release.set()
            finished = _await_task(client, task_id)
    finally:
        release.set()
        get_settings.cache_clear()

    assert finished["status"] == "succeeded"
    assert finished["registered"] == 1


def test_console_repo_scan_accepts_then_registers_one_repository(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(scan_remote, "scan_single_repo", _one_repo)
    try:
        with TestClient(create_app(application_container)) as client:
            accepted = client.post(
                "/api/v1/console/repositories/scan-repo",
                headers=HEADERS,
                json={"repo_url": "https://github.com/acme/order-service"},
            )
            finished = _await_task(client, accepted.json()["task_id"])
            listed = client.get("/api/v1/repositories")
    finally:
        get_settings.cache_clear()

    assert accepted.status_code == 202
    assert accepted.json()["kind"] == "repository"
    # One repository is a known total from the start, unlike an organization.
    assert accepted.json()["total"] == 1
    assert finished["status"] == "succeeded"
    assert (finished["registered"], finished["skipped"], finished["failed"]) == (1, 0, 0)
    assert [r["name"] for r in listed.json()] == ["order-service"]


def test_console_scan_refuses_before_it_accepts(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """What can be refused up front is refused with 400, not with a task.

    A 202 that immediately fails would make the console poll to learn something
    the request itself could have been told, and would leave a failed-task
    record for a request that never should have started.
    """

    _configure(monkeypatch)
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
    finally:
        get_settings.cache_clear()

    assert internal.status_code == 400
    assert "allowlist" in internal.json()["detail"]
    assert group_as_repo.status_code == 400
    assert "single repository" in group_as_repo.json()["detail"]


def test_console_scan_failure_is_a_failed_task_that_says_nothing_useful(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """A scan that dies mid-flight lands in the task, not in a 502.

    The error text stays as generic as the 502 the native endpoint returns:
    moving the report from the response to a task must not become an excuse to
    start echoing what the outbound request saw.
    """

    _configure(monkeypatch)

    async def _explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("connect to 10.0.0.7:5432 refused")

    monkeypatch.setattr(scan_remote, "scan_org", _explode)
    try:
        with TestClient(create_app(application_container)) as client:
            accepted = client.post(
                "/api/v1/console/repositories/scan-org",
                headers=HEADERS,
                json={"org_url": "https://github.com/acme"},
            )
            failed = _await_task(client, accepted.json()["task_id"])
    finally:
        get_settings.cache_clear()

    assert accepted.status_code == 202
    assert failed["status"] == "failed"
    assert failed["error"] == "organization scan failed"
    assert failed["registered"] == 0
    assert "10.0.0.7" not in str(failed)


def test_unknown_scan_task_says_state_is_lost_on_restart(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """404 must not read as "you sent a bad id".

    Task state is an in-process dict. After a restart every id in the browser
    is unknown, and the console needs to tell the user something true and
    actionable: run it again, which is safe.
    """

    _configure(monkeypatch)
    try:
        with TestClient(create_app(application_container)) as client:
            missing = client.get(
                f"/api/v1/console/repositories/scan-tasks/{uuid4()}", headers=HEADERS
            )
    finally:
        get_settings.cache_clear()

    assert missing.status_code == 404
    detail = missing.json()["detail"]
    assert "restart" in detail
    assert "safe" in detail
