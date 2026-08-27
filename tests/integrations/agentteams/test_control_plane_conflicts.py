"""``containerManaged`` is compared as a JSON boolean, not for equality.

The one field of a worker document whose comparison cannot be loose. Every
other field ``_assert_worker_matches`` checks is a string or a list, where
Python equality says what the operator means; ``containerManaged`` is a
boolean, and Python's ``0 == False`` made a controller answering
``"containerManaged": 0`` *confirm* an external projection instead of refusing
it. Adopting a managed worker as external leaves a container running under an
identity a local process is serving (ADR 0004 decision 2), so the confirmation
has to be the JSON literal and nothing that merely compares equal to it.

The whole file is about that one comparison; the wire payloads and error codes
of the client itself live in ``tests/contracts/test_agentteams_integration.py``.
"""

from typing import Any

import httpx
import pytest

from repomesh.integrations.agentteams.control_plane import (
    AgentTeamsConflict,
    AgentTeamsControlPlaneClient,
)
from repomesh.modules.agent_runtime.ports.agent_team import (
    WorkerProjection,
    WorkerRuntime,
)

WORKER = "repomesh-worker-bridge"
MODEL = "qwen3.6-plus"


def _client(document: dict[str, Any]) -> AgentTeamsControlPlaneClient:
    """A controller holding exactly ``document`` under ``WORKER``.

    ``ensure_worker`` GETs before it creates, so every call in this file takes
    the "already exists" branch and the answer is this document.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"name": WORKER, "phase": "Ready", **document})

    return AgentTeamsControlPlaneClient(
        "http://agentteams:8090", transport=httpx.MockTransport(handler)
    )


async def _ensure(document: dict[str, Any], *, container_managed: bool) -> None:
    client = _client(document)
    try:
        await client.ensure_worker(
            WorkerProjection(
                WORKER,
                MODEL,
                WorkerRuntime.HERMES,
                container_managed=container_managed,
            ),
            idempotency_key="container-managed-comparison",
        )
    finally:
        await client.close()


_SPEC: dict[str, Any] = {"model": MODEL, "runtime": "hermes"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("observed", "label"),
    [
        (0, "the JSON number zero"),
        ("false", "the string 'false'"),
        (None, "an explicit null"),
    ],
)
async def test_only_a_json_false_confirms_an_external_worker(
    observed: Any, label: str
) -> None:
    """``0`` is the live hole; the other two are the same mistake spelled twice.

    ``0 == False`` is true in Python, so the old comparison read a controller
    that answered ``0`` as one that had confirmed ``containerManaged: false``
    — and the bridge preflight is built on that confirmation being real. A
    string and a null are refused for the reason absence already is: a document
    that does not carry the boolean did not come from a controller that knows
    the field.
    """

    with pytest.raises(AgentTeamsConflict, match="containerManaged"):
        await _ensure({**_SPEC, "containerManaged": observed}, container_managed=False)


@pytest.mark.asyncio
async def test_a_json_false_still_confirms_an_external_worker() -> None:
    """Strict is not the same as refusing everything."""

    await _ensure({**_SPEC, "containerManaged": False}, container_managed=False)


@pytest.mark.asyncio
async def test_a_json_true_still_confirms_a_managed_worker() -> None:
    await _ensure({**_SPEC, "containerManaged": True}, container_managed=True)


@pytest.mark.asyncio
async def test_a_truthy_non_boolean_cannot_confirm_a_managed_worker_either() -> None:
    """The mirror of the hole, closed in the same stroke.

    ``1 == True`` reads as a confirmation just as readily, and the ordinary
    project path is the one that would take it — adopting an external worker
    as managed starts a second body for an identity a local process is already
    serving.
    """

    with pytest.raises(AgentTeamsConflict, match="containerManaged"):
        await _ensure({**_SPEC, "containerManaged": 1}, container_managed=True)


@pytest.mark.asyncio
async def test_the_other_fields_keep_comparing_by_equality() -> None:
    """Only ``containerManaged`` changed comparison; ``runtime`` still refuses
    on a plain string difference, which is what P2's read-before-ensure exists
    to stop materialize from provoking."""

    with pytest.raises(AgentTeamsConflict, match="runtime"):
        await _ensure(
            {"model": MODEL, "runtime": "repomesh-runner", "containerManaged": False},
            container_managed=False,
        )
