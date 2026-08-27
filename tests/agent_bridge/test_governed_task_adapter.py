"""The start-action adapter, against ``httpx.MockTransport``.

No server is started: the adapter is a translation table and one deliberate
absence — there is no retry loop — and both are fully determined by the answer
it is handed. The request shape asserted here is the one
``src/repomesh/modules/agent_runtime/api/router.py`` accepts and the status codes
are the ones it returns, so the two sides break together if the route moves.

The split under test is "did RepoMesh decide anything", not HTTP taxonomy: a
409 saying this worker is not the assignee is a refusal with words worth
repeating in a room, and a 503 is a control plane that answered nothing.
"""

import json
from dataclasses import fields
from uuid import UUID, uuid4

import httpx
import pytest

from repomesh_agent_bridge.adapters.governed_task import (
    START_TASK_PATH,
    RepoMeshGovernedTaskAdapter,
)
from repomesh_agent_bridge.ports import GovernedTaskRefused, GovernedTaskUnavailable

from .conftest import REPOMESH_ENDPOINT, REPOMESH_TOKEN_VALUE

TASK_ID = UUID("11111111-2222-3333-4444-555555555555")
RUN_ID = UUID("99999999-8888-7777-6666-555555555555")
WORKER_ID = UUID("00000000-0000-0000-0000-000000000002")
START_URL = f"{REPOMESH_ENDPOINT}{START_TASK_PATH}"

ACCEPTED = {
    "task_id": str(TASK_ID),
    "run_id": str(RUN_ID),
    "status": "in_progress",
    "workspace_id": "8f2c",
    "workspace_path": "/home/operator/.repomesh/workspaces/8f2c",
    "base_sha": "3f7a1c2",
}
"""RepoMesh's real answer, workspace and all.

Spelled in full rather than trimmed to the two fields the adapter keeps: the
point of several of these tests is what does *not* come out of the call, and a
fixture that had already dropped the path could not show it.
"""


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


class Credential:
    """A resolver that counts, so "resolved per call" is observable."""

    def __init__(self) -> None:
        self.resolutions = 0

    def __call__(self) -> str:
        self.resolutions += 1
        return REPOMESH_TOKEN_VALUE


def _adapter(
    calls: Calls, credential: Credential | None = None
) -> RepoMeshGovernedTaskAdapter:
    return RepoMeshGovernedTaskAdapter(
        endpoint=REPOMESH_ENDPOINT,
        credential=credential or Credential(),
        adapter_id="codex",
        transport=calls.transport(),
    )


async def test_an_accepted_start_becomes_a_receipt_of_two_ids() -> None:
    """202 with the action's answer, and the request RepoMesh actually accepts.

    ``adapter_id`` is in the body because the route requires it, and it is the
    enrollment's coding profile carried down by whoever wired this adapter — a
    room message names a task and nothing else.
    """

    calls = Calls(httpx.Response(202, json=ACCEPTED))
    credential = Credential()

    receipt = await _adapter(calls, credential).start_task(
        task_id=TASK_ID, worker_agent_id=WORKER_ID
    )

    assert (receipt.task_id, receipt.run_id) == (TASK_ID, RUN_ID)
    request = calls.requests[0]
    assert request.method == "POST"
    assert str(request.url) == START_URL
    assert request.headers["Authorization"] == f"Bearer {REPOMESH_TOKEN_VALUE}"
    assert json.loads(request.content) == {
        "task_id": str(TASK_ID),
        "worker_agent_id": str(WORKER_ID),
        "adapter_id": "codex",
    }
    assert credential.resolutions == 1, "the secret's lifetime is the call's"


async def test_the_workspace_the_answer_names_does_not_leave_the_adapter() -> None:
    """A path on the machine holding the worktree has no way through this seam.

    The receipt has two fields and neither is a string, so the sentence a room
    is later given cannot contain that path even by accident.
    """

    calls = Calls(httpx.Response(202, json=ACCEPTED))

    receipt = await _adapter(calls).start_task(task_id=TASK_ID, worker_agent_id=WORKER_ID)

    assert [field.name for field in fields(receipt)] == ["run_id", "task_id"]
    assert ACCEPTED["workspace_path"] not in repr(receipt)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 301, 302])
async def test_a_decision_against_the_start_is_a_refusal_in_repomesh_s_own_words(
    status: int,
) -> None:
    """Every 4xx, and every 3xx because redirects are disabled.

    The ``detail`` is carried out verbatim: it is the control plane's sentence
    about a decision it made, and it is the only thing that tells the person who
    asked whether the task is missing or whether it is not theirs.
    """

    calls = Calls(httpx.Response(status, json={"detail": "worker is not assigned to this task"}))

    with pytest.raises(GovernedTaskRefused, match="worker is not assigned to this task"):
        await _adapter(calls).start_task(task_id=TASK_ID, worker_agent_id=WORKER_ID)

    assert len(calls.requests) == 1, "a refusal is never asked twice"


async def test_a_refusal_without_a_sentence_is_reported_as_its_status() -> None:
    """422 puts a list of validation objects in ``detail``.

    Rendering that into a room would be pasting an internal error structure in
    front of whoever asked, so anything that is not a plain string becomes the
    status code and nothing else.
    """

    calls = Calls(httpx.Response(422, json={"detail": [{"loc": ["body", "task_id"]}]}))

    with pytest.raises(GovernedTaskRefused, match="422"):
        await _adapter(calls).start_task(task_id=TASK_ID, worker_agent_id=WORKER_ID)


async def test_an_accepted_start_without_a_run_id_is_refused_rather_than_invented() -> None:
    """A receipt this process cannot read is not a receipt.

    Inventing a run id here would anchor a room conversation to a run that does
    not exist, and the narration would then be waiting for something nobody is
    performing.
    """

    calls = Calls(httpx.Response(202, json={"task_id": str(TASK_ID), "status": "in_progress"}))

    with pytest.raises(GovernedTaskRefused, match="run id"):
        await _adapter(calls).start_task(task_id=TASK_ID, worker_agent_id=WORKER_ID)


@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_a_control_plane_that_decided_nothing_is_unavailable(status: int) -> None:
    calls = Calls(httpx.Response(status, json={"detail": "busy"}))

    with pytest.raises(GovernedTaskUnavailable):
        await _adapter(calls).start_task(task_id=TASK_ID, worker_agent_id=WORKER_ID)

    assert len(calls.requests) == 1


async def test_a_start_that_may_have_arrived_is_never_sent_a_second_time() -> None:
    """The whole reason this adapter has no retry policy.

    Its sibling next door retries three times, and may: that call is a GET made
    once per process. This one starts work. A connection that dropped after the
    request left is indistinguishable from one that dropped before, so a second
    attempt is a coin flip on whether a task gets two runs — and RepoMesh's own
    in-flight reuse means a *person* asking again is the safe recovery.
    """

    calls = Calls(httpx.ConnectError("connection refused"))

    with pytest.raises(GovernedTaskUnavailable, match="ConnectError"):
        await _adapter(calls).start_task(task_id=TASK_ID, worker_agent_id=WORKER_ID)

    assert len(calls.requests) == 1


async def test_closing_an_adapter_that_never_called_anything_is_safe() -> None:
    calls = Calls(httpx.Response(202, json=ACCEPTED))
    credential = Credential()
    adapter = _adapter(calls, credential)

    await adapter.close()
    await adapter.close()

    assert calls.requests == []
    assert credential.resolutions == 0, "nothing resolved a secret it never needed"


async def test_the_endpoint_may_carry_a_trailing_slash() -> None:
    calls = Calls(httpx.Response(202, json=ACCEPTED))
    adapter = RepoMeshGovernedTaskAdapter(
        endpoint=f"{REPOMESH_ENDPOINT}/",
        credential=Credential(),
        adapter_id="codex",
        transport=calls.transport(),
    )

    await adapter.start_task(task_id=uuid4(), worker_agent_id=WORKER_ID)

    assert str(calls.requests[0].url) == START_URL
