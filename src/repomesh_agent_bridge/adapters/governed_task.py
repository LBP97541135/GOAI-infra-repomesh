"""``GovernedTaskPort`` over RepoMesh's start-worker-task action.

One POST, and everything interesting is what the adapter refuses to do around
it.

**It does not retry.** Its sibling next door — the preflight read — retries three
times, and the reason it may is written into what it is: a GET, made once per
process, where a second attempt asks the same question again. This is a write.
A request RepoMesh answered slowly, or answered into a connection that had
already dropped, is a request that *started work*, and repeating it is asking
for work to be started twice from one sentence somebody typed in a room. What
makes that recoverable rather than merely refused is RepoMesh's own in-flight
reuse: a start for a task whose run has not finished returns that run's receipt
instead of dispatching a second one, so a human who reads "I could not reach
RepoMesh" and mentions the worker again lands on the original run if it did in
fact start. That is a judgement only the control plane can make, so the adapter
does not make it.

**It keeps almost nothing of the answer.** The action replies with the workspace
it prepared, including an absolute path on the machine that holds it. Two ids
come out of here and the rest stops at this function, because the caller's next
move is to put a message in a room.

Only the method, the path and the status code are logged. Never the response
body, never the credential, and never a workspace path.
"""

import logging
from collections.abc import Callable
from uuid import UUID

import httpx

from ..ports import (
    GovernedStartReceipt,
    GovernedTaskRefused,
    GovernedTaskUnavailable,
)

__all__ = ["START_TASK_PATH", "RepoMeshGovernedTaskAdapter"]

_logger = logging.getLogger(__name__)

START_TASK_PATH = "/api/v1/agent-actions/start-worker-task"
"""Where RepoMesh serves the one action a Bridge may take.

The ``/api/v1`` prefix belongs to the path rather than to the enrollment's
``repomeshEndpoint``, which the schema describes as the control plane's base
URL: each caller under that base owns its own path, exactly as the preflight
adapter does.
"""

DEFAULT_TIMEOUT_SECONDS = 30.0
"""Longer than the preflight read's ten, because this call does work.

Starting a task prepares a worktree and publishes a context bundle before it
answers. A timeout here is reported as unavailable and never retried, so the
number only decides how long the room waits to be told nothing happened.
"""

CredentialProvider = Callable[[], str]
"""``resolve() -> secret``, called per request.

Injected rather than held as a value for the reason every other credential in
this package is resolved late: the secret's lifetime is the call's, and a
process that keeps one in an attribute keeps it in every heap dump for as long
as it lives.
"""


class RepoMeshGovernedTaskAdapter:
    """Production :class:`~repomesh_agent_bridge.ports.GovernedTaskPort`.

    Holds one client for the process rather than opening one per call, which is
    the difference between this and the preflight adapter: that one runs once at
    startup, this one runs whenever somebody in a room asks for a run.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        credential: CredentialProvider,
        adapter_id: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._credential = credential
        self._adapter_id = adapter_id
        self._client = httpx.AsyncClient(
            base_url=endpoint.rstrip("/"),
            headers={"Accept": "application/json"},
            timeout=timeout,
            transport=transport,
            # A redirect on an authenticated write would send the credential —
            # and the request to start work — somewhere the enrollment never
            # named. (httpx already defaults to False; it is spelled out because
            # it is a security property, not a preference.)
            follow_redirects=False,
        )

    async def start_task(
        self, *, task_id: UUID, worker_agent_id: UUID
    ) -> GovernedStartReceipt:
        body = {
            "task_id": str(task_id),
            "worker_agent_id": str(worker_agent_id),
            # Which runtime RepoMesh should dispatch to. It is the enrollment's
            # ``codingProfile`` carried down by whoever wired this adapter, not
            # anything the room said: a room message names a task, and every
            # other fact about the run belongs to the two parties that already
            # agreed on it.
            "adapter_id": self._adapter_id,
        }
        try:
            response = await self._client.post(
                START_TASK_PATH,
                json=body,
                headers={"Authorization": f"Bearer {self._credential()}"},
            )
        except httpx.HTTPError as unreachable:
            _logger.warning(
                "POST %s failed with %s", START_TASK_PATH, type(unreachable).__name__
            )
            raise GovernedTaskUnavailable(
                f"RepoMesh could not be reached: {type(unreachable).__name__}"
            ) from unreachable
        _logger.info("POST %s -> %d", START_TASK_PATH, response.status_code)
        return _translate(response)

    async def close(self) -> None:
        """Release the client. Safe on an adapter that never made a call."""

        await self._client.aclose()


def _translate(response: httpx.Response) -> GovernedStartReceipt:
    """The one place HTTP becomes the port's two-word vocabulary.

    429 and every 5xx are unavailable — RepoMesh did not decide anything, it was
    busy or broken. Every 4xx is a refusal, and so is every 3xx: redirects are
    disabled, so one means the control plane is not where the enrollment says,
    which no retry fixes. A 2xx whose body is not the action's answer is a
    refusal too, because a receipt this process cannot read is not a receipt.
    """

    status = response.status_code
    if status == 429 or status >= 500:
        raise GovernedTaskUnavailable(f"RepoMesh answered {status} for the start action")
    if status >= 300:
        raise GovernedTaskRefused(_refusal(response))
    try:
        payload = response.json()
    except ValueError as unreadable:
        raise GovernedTaskRefused(
            f"RepoMesh answered {status} with a body that is not JSON"
        ) from unreadable
    if not isinstance(payload, dict):
        raise GovernedTaskRefused(f"RepoMesh answered {status} with a body that is not an object")
    try:
        return GovernedStartReceipt(
            run_id=UUID(str(payload["run_id"])), task_id=UUID(str(payload["task_id"]))
        )
    except (KeyError, ValueError) as unreadable:
        raise GovernedTaskRefused(
            "RepoMesh accepted the task but its answer carries no run id"
        ) from unreadable


def _refusal(response: httpx.Response) -> str:
    """RepoMesh's own words about a decision it made, or the status if it had none.

    FastAPI puts an explanation in ``detail`` — "worker is not assigned to this
    task" is the one an operator most needs to read — and that sentence is what
    the room gets. A validation error puts a *list* there instead, and a body
    that is not JSON at all is a proxy answering rather than RepoMesh, so
    anything that is not a plain string is reported as the status code alone
    rather than rendered into a room.
    """

    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict) and isinstance(payload.get("detail"), str):
        return payload["detail"]
    return f"RepoMesh refused the start action with {response.status_code}"
