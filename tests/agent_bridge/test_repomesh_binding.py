"""The RepoMesh preflight adapter, against ``httpx.MockTransport``.

No server is started: the adapter's job is a translation table plus a retry
policy, and both are fully determined by the response it is handed. The status
codes exercised here are the ones ``tests/api/test_external_worker_binding.py``
pins on the other side of the wire, so the two suites break together if the
contract moves.

The split under test is "can a retry fix this", not HTTP taxonomy: 503 from an
unconfigured control plane is retried, 409 from a still-managed worker is not.
"""

import logging

import httpx
import pytest

from repomesh_agent_bridge.adapters.repomesh_binding import RepoMeshBindingAdapter
from repomesh_agent_bridge.contracts import (
    BindingRefused,
    BindingUnavailable,
    ExternalWorkerEnrollment,
)

from .conftest import (
    REPOMESH_TOKEN_VALUE,
    TEAM_ROOM,
    WORKER_AGENT_ID,
    WORKER_ROOM,
    binding_wire,
    enrollment_wire,
)

BINDING_URL = (
    f"https://repomesh.example.org/api/v1/runtime/external-workers/{WORKER_AGENT_ID}/binding"
)


class Calls:
    """Records what the adapter sent, and answers with what the test scripted."""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            answer = self._responses[min(len(self.requests) - 1, len(self._responses) - 1)]
            if isinstance(answer, Exception):
                raise answer
            return answer

        return httpx.MockTransport(handle)


class Sleeps:
    def __init__(self) -> None:
        self.seconds: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.seconds.append(seconds)


def _adapter(calls: Calls, sleeps: Sleeps | None = None) -> RepoMeshBindingAdapter:
    return RepoMeshBindingAdapter(transport=calls.transport(), sleep=sleeps or Sleeps())


async def test_a_confirmed_binding_is_parsed_from_the_authenticated_get(
    enrollment: ExternalWorkerEnrollment,
) -> None:
    calls = Calls(httpx.Response(200, json=binding_wire()))

    binding = await _adapter(calls).fetch_binding(enrollment, credential=REPOMESH_TOKEN_VALUE)

    assert binding.worker_name == enrollment.worker_name
    assert binding.allowed_room_ids == (TEAM_ROOM, WORKER_ROOM)
    assert binding.container_managed is False
    request = calls.requests[0]
    assert request.method == "GET"
    assert str(request.url) == BINDING_URL
    assert request.headers["Authorization"] == f"Bearer {REPOMESH_TOKEN_VALUE}"


async def test_the_endpoint_may_carry_a_trailing_slash_or_a_path_prefix() -> None:
    enrollment = ExternalWorkerEnrollment.from_wire(
        enrollment_wire(repomeshEndpoint="https://repomesh.example.org/gateway/")
    )
    calls = Calls(httpx.Response(200, json=binding_wire()))

    await _adapter(calls).fetch_binding(enrollment, credential="t")

    assert str(calls.requests[0].url) == BINDING_URL.replace(
        "example.org/api", "example.org/gateway/api"
    )


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 418, 301, 302])
async def test_a_refusal_is_final_and_is_not_retried(
    enrollment: ExternalWorkerEnrollment, status: int
) -> None:
    """4xx and, with redirects disabled, 3xx: the answer will not improve."""

    calls = Calls(httpx.Response(status, json={"detail": "no"}))
    sleeps = Sleeps()

    with pytest.raises(BindingRefused):
        await _adapter(calls, sleeps).fetch_binding(enrollment, credential="t")

    assert len(calls.requests) == 1
    assert sleeps.seconds == []


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
async def test_a_retryable_answer_is_attempted_three_times_with_backoff(
    enrollment: ExternalWorkerEnrollment, status: int
) -> None:
    calls = Calls(httpx.Response(status, json={"detail": "later"}))
    sleeps = Sleeps()

    with pytest.raises(BindingUnavailable):
        await _adapter(calls, sleeps).fetch_binding(enrollment, credential="t")

    assert len(calls.requests) == 3
    assert sleeps.seconds == [0.5, 1.0], "exponential, and bounded by the attempt count"


async def test_a_transport_failure_is_retryable(enrollment: ExternalWorkerEnrollment) -> None:
    calls = Calls(httpx.ConnectError("no route to host"))
    sleeps = Sleeps()

    with pytest.raises(BindingUnavailable):
        await _adapter(calls, sleeps).fetch_binding(enrollment, credential="t")

    assert len(calls.requests) == 3


async def test_a_retry_that_succeeds_returns_the_binding(
    enrollment: ExternalWorkerEnrollment,
) -> None:
    calls = Calls(httpx.Response(503, json={}), httpx.Response(200, json=binding_wire()))

    binding = await _adapter(calls).fetch_binding(enrollment, credential="t")

    assert binding.worker_agent_id == enrollment.worker_agent_id
    assert len(calls.requests) == 2


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"detail": "not a binding"}, id="wrong-shape"),
        pytest.param(binding_wire(containerManaged=True), id="managed-worker"),
        pytest.param(binding_wire(schemaVersion="repomesh.agent-bridge.binding.v2"), id="v2"),
        pytest.param(binding_wire(allowedRoomIds=[]), id="no-rooms"),
    ],
)
async def test_a_200_that_is_not_a_binding_is_refused(
    enrollment: ExternalWorkerEnrollment, body: dict[str, object]
) -> None:
    calls = Calls(httpx.Response(200, json=body))
    sleeps = Sleeps()

    with pytest.raises(BindingRefused):
        await _adapter(calls, sleeps).fetch_binding(enrollment, credential="t")

    assert len(calls.requests) == 1, "a body that is not a binding will not become one"


async def test_a_200_that_is_not_json_is_refused(enrollment: ExternalWorkerEnrollment) -> None:
    calls = Calls(httpx.Response(200, text="<html>login</html>"))

    with pytest.raises(BindingRefused):
        await _adapter(calls).fetch_binding(enrollment, credential="t")


async def test_the_credential_never_reaches_the_log_or_the_error(
    enrollment: ExternalWorkerEnrollment, caplog
) -> None:
    caplog.set_level(logging.DEBUG, logger="repomesh_agent_bridge.adapters.repomesh_binding")
    calls = Calls(httpx.Response(503, json={}))

    with pytest.raises(BindingUnavailable) as unavailable:
        await _adapter(calls).fetch_binding(enrollment, credential=REPOMESH_TOKEN_VALUE)

    assert REPOMESH_TOKEN_VALUE not in caplog.text
    assert REPOMESH_TOKEN_VALUE not in str(unavailable.value)
    assert "503" in caplog.text, "status code and attempt are exactly what should be logged"


async def test_the_adapter_declares_that_it_authenticates() -> None:
    """Stage 1 reads this to know a missing ``credentialRefs.repomesh`` is fatal."""

    assert RepoMeshBindingAdapter().requires_credential is True
