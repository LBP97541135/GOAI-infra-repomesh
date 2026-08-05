"""HTTP event delivery: idempotent retries, and rejection as a hard failure."""

import json
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest

from repomesh_runner import (
    ContextBundleRef,
    RepositoryCheckout,
    RunnerEvent,
    RunnerEventType,
    RunnerPermissions,
    RunnerTask,
)
from repomesh_runner.event_sink import EventDeliveryError, HttpEventSink

URL = "https://control.example/events"
SHA256 = "sha256:" + "a" * 64
NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


def make_event() -> RunnerEvent:
    task = RunnerTask(
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
        idempotency_key="run-4-attempt-1",
        issued_at=NOW,
    )
    return RunnerEvent(
        event_id=UUID("00000000-0000-0000-0000-0000000000ff"),
        event_type=RunnerEventType.ACCEPTED,
        task=task,
        sequence=1,
        occurred_at=NOW,
        payload={"adapterId": "codex"},
    )


class FakeSleep:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def build_sink(handler, **kwargs: object) -> tuple[HttpEventSink, FakeSleep, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def recording_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    sleep = FakeSleep()
    client = httpx.AsyncClient(transport=httpx.MockTransport(recording_handler))
    sink = HttpEventSink(URL, client=client, sleep=sleep, **kwargs)  # type: ignore[arg-type]
    return sink, sleep, requests


@pytest.mark.asyncio
async def test_successful_delivery_sends_the_wire_event_once() -> None:
    sink, sleep, requests = build_sink(lambda request: httpx.Response(202))

    await sink.publish(make_event(), idempotency_key="run-4-attempt-1:event:1")

    assert len(requests) == 1
    assert sleep.delays == []


@pytest.mark.asyncio
async def test_the_idempotency_key_travels_as_a_header() -> None:
    sink, _, requests = build_sink(lambda request: httpx.Response(200))

    await sink.publish(make_event(), idempotency_key="run-4-attempt-1:event:2")

    assert requests[0].headers["Idempotency-Key"] == "run-4-attempt-1:event:2"


@pytest.mark.asyncio
async def test_the_body_is_the_wire_event() -> None:
    sink, _, requests = build_sink(lambda request: httpx.Response(200))
    event = make_event()

    await sink.publish(event, idempotency_key="key")

    assert json.loads(requests[0].content) == event.to_wire()


@pytest.mark.asyncio
async def test_server_errors_are_retried_until_they_succeed() -> None:
    responses = [httpx.Response(500), httpx.Response(503), httpx.Response(200)]
    sink, sleep, requests = build_sink(lambda request: responses.pop(0))

    await sink.publish(make_event(), idempotency_key="key")

    assert len(requests) == 3
    assert sleep.delays == [1.0, 2.0]


@pytest.mark.asyncio
async def test_too_many_requests_is_retried() -> None:
    responses = [httpx.Response(429), httpx.Response(204)]
    sink, sleep, requests = build_sink(lambda request: responses.pop(0))

    await sink.publish(make_event(), idempotency_key="key")

    assert len(requests) == 2
    assert sleep.delays == [1.0]


@pytest.mark.asyncio
async def test_transport_errors_are_retried() -> None:
    calls = {"count": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200)

    sink, sleep, _ = build_sink(flaky)

    await sink.publish(make_event(), idempotency_key="key")

    assert calls["count"] == 2
    assert sleep.delays == [1.0]


@pytest.mark.asyncio
async def test_a_rejected_event_is_a_contract_violation_not_a_retry() -> None:
    sink, sleep, requests = build_sink(lambda request: httpx.Response(400))

    with pytest.raises(EventDeliveryError, match="rejected with status 400"):
        await sink.publish(make_event(), idempotency_key="key")

    assert len(requests) == 1
    assert sleep.delays == []


@pytest.mark.asyncio
async def test_exhausted_retries_raise_a_delivery_error() -> None:
    sink, sleep, requests = build_sink(lambda request: httpx.Response(500))

    with pytest.raises(EventDeliveryError, match="after 5 attempts"):
        await sink.publish(make_event(), idempotency_key="key")

    assert len(requests) == 5
    assert sleep.delays == [1.0, 2.0, 4.0, 8.0]


@pytest.mark.asyncio
async def test_backoff_is_capped() -> None:
    sink, sleep, _ = build_sink(
        lambda request: httpx.Response(500),
        max_backoff_seconds=3.0,
    )

    with pytest.raises(EventDeliveryError):
        await sink.publish(make_event(), idempotency_key="key")

    assert sleep.delays == [1.0, 2.0, 3.0, 3.0]


@pytest.mark.asyncio
async def test_an_injected_client_is_not_closed_by_the_sink() -> None:
    sink, _, _ = build_sink(lambda request: httpx.Response(200))

    await sink.aclose()

    await sink.publish(make_event(), idempotency_key="key")
