"""``AgentTeamsMatrixClient.send_approval``: the one shape copaw accepts (spec §8.10).

Body exactly ``/approve``, the worker named only in ``m.mentions.user_ids``,
delivered as an idempotent ``PUT`` under the caller's transaction id. Pinned
over ``httpx.MockTransport`` so the wire shape is what is asserted.
"""

from __future__ import annotations

import json

import httpx
import pytest

from repomesh.integrations.agentteams.control_plane import (
    AgentTeamsResponseError,
    AgentTeamsUnavailable,
)
from repomesh.integrations.agentteams.matrix import AgentTeamsMatrixClient

ROOM = "!room:hs"
WORKER = "@agt-worker-x:hs"
TRANSACTION = "hosted-native-approve-1"
EXPECTED_PATH = "/_matrix/client/v3/rooms/%21room%3Ahs/send/m.room.message/hosted-native-approve-1"


class Recorder:
    """A homeserver stand-in that records the request and answers as told."""

    def __init__(self, response: httpx.Response | Exception) -> None:
        self.response = response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def client(recorder: Recorder) -> AgentTeamsMatrixClient:
    return AgentTeamsMatrixClient("http://hs", "token", transport=httpx.MockTransport(recorder))


async def test_send_approval_puts_exactly_the_shape_copaw_accepts() -> None:
    recorder = Recorder(httpx.Response(200, json={"event_id": "$approved"}))

    event_id = await client(recorder).send_approval(ROOM, WORKER, transaction_id=TRANSACTION)

    assert event_id == "$approved"
    assert len(recorder.requests) == 1
    request = recorder.requests[0]
    assert request.method == "PUT"
    assert request.url.raw_path.decode() == EXPECTED_PATH
    assert request.url.host == "hs"
    assert request.headers["Authorization"] == "Bearer token"
    assert json.loads(request.content) == {
        "msgtype": "m.text",
        "body": "/approve",
        "m.mentions": {"user_ids": [WORKER]},
    }


async def test_send_approval_strips_its_arguments_before_use() -> None:
    recorder = Recorder(httpx.Response(200, json={"event_id": "$approved"}))

    await client(recorder).send_approval(
        f"  {ROOM}  ", f" {WORKER} ", transaction_id=f" {TRANSACTION}\n"
    )

    request = recorder.requests[0]
    assert request.url.raw_path.decode() == EXPECTED_PATH
    assert json.loads(request.content)["m.mentions"] == {"user_ids": [WORKER]}


@pytest.mark.parametrize("status", [401, 403, 429, 500])
async def test_send_approval_surfaces_a_non_200_as_a_response_error(status: int) -> None:
    recorder = Recorder(httpx.Response(status, json={"errcode": "M_FORBIDDEN"}))

    with pytest.raises(AgentTeamsResponseError) as raised:
        await client(recorder).send_approval(ROOM, WORKER, transaction_id=TRANSACTION)

    assert raised.value.status_code == status


@pytest.mark.parametrize(
    "response",
    [
        pytest.param(httpx.Response(200, content=b"not json"), id="invalid-json"),
        pytest.param(httpx.Response(200, json={}), id="missing-event-id"),
        pytest.param(httpx.Response(200, json={"event_id": ""}), id="blank-event-id"),
        pytest.param(httpx.Response(200, json=["$x"]), id="not-an-object"),
    ],
)
async def test_send_approval_rejects_a_malformed_200(response: httpx.Response) -> None:
    with pytest.raises(AgentTeamsResponseError):
        await client(Recorder(response)).send_approval(ROOM, WORKER, transaction_id=TRANSACTION)


@pytest.mark.parametrize(
    ("room", "worker", "transaction", "message"),
    [
        ("", WORKER, TRANSACTION, "room_id is required"),
        ("   ", WORKER, TRANSACTION, "room_id is required"),
        (ROOM, "", TRANSACTION, "worker_matrix_user_id is required"),
        (ROOM, WORKER, "", "transaction_id is required"),
        (ROOM, WORKER, "  ", "transaction_id is required"),
    ],
)
async def test_send_approval_refuses_blank_arguments(
    room: str, worker: str, transaction: str, message: str
) -> None:
    recorder = Recorder(httpx.Response(200, json={"event_id": "$approved"}))

    with pytest.raises(ValueError, match=message):
        await client(recorder).send_approval(room, worker, transaction_id=transaction)

    assert recorder.requests == []


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(httpx.ConnectError("refused"), id="connect"),
        pytest.param(httpx.ReadTimeout("slow"), id="timeout"),
    ],
)
async def test_send_approval_turns_a_transport_error_into_unavailable(
    error: Exception,
) -> None:
    with pytest.raises(AgentTeamsUnavailable):
        await client(Recorder(error)).send_approval(ROOM, WORKER, transaction_id=TRANSACTION)
