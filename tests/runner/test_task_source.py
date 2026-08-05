"""Long-poll task source: no real network, no real sleeping."""

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
import pytest

from repomesh_runner import (
    ContextBundleRef,
    RepositoryCheckout,
    RunnerPermissions,
    RunnerTask,
)
from repomesh_runner.task_source import HttpLongPollTaskSource

URL = "https://control.example/tasks/next"
SHA256 = "sha256:" + "a" * 64


def make_task(idempotency_key: str = "run-4-attempt-1") -> RunnerTask:
    return RunnerTask(
        organization_id=UUID("00000000-0000-0000-0000-000000000001"),
        project_id=UUID("00000000-0000-0000-0000-000000000002"),
        task_id=UUID("00000000-0000-0000-0000-000000000003"),
        run_id=UUID("00000000-0000-0000-0000-000000000004"),
        correlation_id=UUID("00000000-0000-0000-0000-000000000005"),
        attempt=1,
        adapter_id="codex",
        instruction="Do the work",
        repository=RepositoryCheckout(
            repository_id=UUID("00000000-0000-0000-0000-000000000006"),
            url="https://github.com/example/service.git",
            base_revision="main",
        ),
        context_bundle=ContextBundleRef(
            bundle_id=UUID("00000000-0000-0000-0000-000000000007"),
            version=1,
            manifest_uri="s3://repomesh-context/manifest.json",
            content_hash=SHA256,
        ),
        permissions=RunnerPermissions(),
        idempotency_key=idempotency_key,
        issued_at=datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
    )


def task_payload() -> dict[str, Any]:
    return make_task().to_wire()


class FakeSleep:
    """Records the delays the source asked for instead of waiting them out."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def build_source(
    handler,
    *,
    sleep: FakeSleep | None = None,
    timeout_seconds: float = 5.0,
    **kwargs: object,
) -> tuple[HttpLongPollTaskSource, FakeSleep, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def recording_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    sleep = sleep or FakeSleep()
    client = httpx.AsyncClient(transport=httpx.MockTransport(recording_handler))
    source = HttpLongPollTaskSource(
        URL,
        timeout_seconds=timeout_seconds,
        client=client,
        sleep=sleep,
        **kwargs,  # type: ignore[arg-type]
    )
    return source, sleep, requests


@pytest.mark.asyncio
async def test_two_hundred_yields_a_parsed_task() -> None:
    source, sleep, requests = build_source(lambda request: httpx.Response(200, json=task_payload()))

    task = await source.next_task()

    assert task is not None
    assert task.idempotency_key == "run-4-attempt-1"
    assert sleep.delays == []
    assert requests[0].url.params["wait"] == "5.0"


@pytest.mark.asyncio
async def test_two_hundred_four_means_no_work_without_local_waiting() -> None:
    source, sleep, _ = build_source(lambda request: httpx.Response(204))

    assert await source.next_task() is None
    assert sleep.delays == []


@pytest.mark.asyncio
async def test_server_error_backs_off_and_yields_no_task() -> None:
    source, sleep, _ = build_source(lambda request: httpx.Response(500))

    assert await source.next_task() is None
    assert sleep.delays == [1.0]


@pytest.mark.asyncio
async def test_backoff_grows_exponentially_and_is_capped() -> None:
    source, sleep, _ = build_source(
        lambda request: httpx.Response(503),
        max_backoff_seconds=8.0,
    )

    for _ in range(6):
        await source.next_task()

    assert sleep.delays == [1.0, 2.0, 4.0, 8.0, 8.0, 8.0]


@pytest.mark.asyncio
async def test_backoff_resets_after_a_successful_poll() -> None:
    responses = [
        httpx.Response(500),
        httpx.Response(500),
        httpx.Response(204),
        httpx.Response(500),
    ]
    source, sleep, _ = build_source(lambda request: responses.pop(0))

    for _ in range(4):
        await source.next_task()

    assert sleep.delays == [1.0, 2.0, 1.0]


@pytest.mark.asyncio
async def test_backoff_resets_after_a_delivered_task() -> None:
    responses = [
        httpx.Response(500),
        httpx.Response(200, json=task_payload()),
        httpx.Response(500),
    ]
    source, sleep, _ = build_source(lambda request: responses.pop(0))

    for _ in range(3):
        await source.next_task()

    assert sleep.delays == [1.0, 1.0]


@pytest.mark.asyncio
async def test_transport_error_does_not_escape_the_source() -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    source, sleep, _ = build_source(explode)

    assert await source.next_task() is None
    assert sleep.delays == [1.0]


@pytest.mark.asyncio
async def test_poison_task_is_dropped_and_backed_off() -> None:
    payload = task_payload()
    payload["schemaVersion"] = "runtime.v2"
    source, sleep, _ = build_source(lambda request: httpx.Response(200, json=payload))

    assert await source.next_task() is None
    assert sleep.delays == [1.0]


@pytest.mark.asyncio
async def test_body_that_is_not_json_is_dropped_and_backed_off() -> None:
    source, sleep, _ = build_source(
        lambda request: httpx.Response(200, content=b"<html>gateway</html>")
    )

    assert await source.next_task() is None
    assert sleep.delays == [1.0]


@pytest.mark.asyncio
async def test_a_poison_task_does_not_stop_later_work() -> None:
    responses = [
        httpx.Response(200, content=json.dumps({"schemaVersion": "runtime.v1"}).encode()),
        httpx.Response(200, json=task_payload()),
    ]
    source, _, _ = build_source(lambda request: responses.pop(0))

    assert await source.next_task() is None
    assert (await source.next_task()) is not None


@pytest.mark.asyncio
async def test_an_injected_client_is_not_closed_by_the_source() -> None:
    source, _, _ = build_source(lambda request: httpx.Response(204))

    await source.aclose()

    assert await source.next_task() is None
