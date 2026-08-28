"""``LeaderActionPort`` over RepoMesh's leader decision surface.

One GET and two POSTs, on the same ``agent-actions`` face and with the same
credential mechanism as ``start-worker-task`` (adjudication D-1). Everything
interesting is, as next door, what the adapter refuses to do around them.

**It does not retry — not even the read.** The preflight adapter retries its GET
because that call happens once per process and a second attempt asks the same
question of a control plane the Bridge has not started serving without. These
calls happen mid-session, in a lane where a read is followed immediately by a
write, and the two writes are idempotent *at the server*: the leader task id
keys a plan, the pair (leader task id, review revision) keys a verdict, and an
identical resubmission returns the original receipt. That is what makes a replay
safe, and it is a judgement only RepoMesh can make. An adapter that retried on
its own would be making it here, out of a timeout it cannot interpret. When a
call cannot be completed the leader lane is told so and asks again — deliberately
the same recovery the governed start action offers.

**It translates, and decides nothing.** Every non-2xx body is the frozen
structured error, so a refusal arrives with the server's own code beside the
server's own sentence and both travel intact to whoever has to act on them. A
2xx whose body is not the document the contract names is a refusal too, on the
governed adapter's reasoning: a receipt this process cannot read is not a
receipt.

Only the method, the path and the status code are logged. Never a request body
(it is the leader's product), never a response body, and never the credential.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from uuid import UUID

import httpx

from ..contracts import (
    LeaderDocumentInvalid,
    PlanReceipt,
    RepositoryAssignmentPackage,
    RepositoryPlanDecision,
    RepositoryReviewDecision,
    ReviewReceipt,
    read_leader_action_error,
)
from ..ports import LeaderActionRefused, LeaderActionUnavailable

__all__ = [
    "ASSIGNMENT_PATH",
    "PLAN_PATH",
    "REVIEW_PATH",
    "RepoMeshLeaderActionAdapter",
]

_logger = logging.getLogger(__name__)

ASSIGNMENT_PATH = "/api/v1/agent-actions/leader/assignments/{task_id}"
PLAN_PATH = "/api/v1/agent-actions/leader/assignments/{task_id}/plan"
REVIEW_PATH = "/api/v1/agent-actions/leader/assignments/{task_id}/review"
"""Where RepoMesh serves the three leader endpoints.

The ``/api/v1`` prefix belongs to the path rather than to the enrollment's
``repomeshEndpoint``, which the schema describes as the control plane's base
URL: each caller under that base owns its own paths, exactly as the preflight
and governed adapters do. The leader task id appears only here — the frozen
bodies deliberately do not repeat it, because it is the idempotency key.
"""

DEFAULT_TIMEOUT_SECONDS = 30.0
"""Matches the governed start action's rather than the preflight read's ten.

Accepting a plan validates a DAG, clamps an envelope and creates worker tasks
before it answers. A timeout is reported as unavailable and never retried, so
the number only decides how long the leader lane waits to be told nothing
happened.
"""

CredentialProvider = Callable[[], str]
"""``resolve() -> secret``, called per request.

The member's own external-member token — the credential its enrollment's
``credentialRefs.repomesh`` references, and under adjudication D-6 an entry in
the map whose environment variable keeps its historical worker-shaped name.
Injected rather than held as a value for the reason every credential in this
package is resolved late: the secret's lifetime is the call's, and a process
that keeps one in an attribute keeps it in every heap dump for as long as it
lives.
"""


class RepoMeshLeaderActionAdapter:
    """Production :class:`~repomesh_agent_bridge.ports.LeaderActionPort`.

    Holds one client for the process, as the governed adapter does and for the
    same reason: these calls happen whenever a round moves, not once at startup.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        credential: CredentialProvider,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._credential = credential
        self._client = httpx.AsyncClient(
            base_url=endpoint.rstrip("/"),
            headers={"Accept": "application/json"},
            timeout=timeout,
            transport=transport,
            # A redirect on an authenticated call would send the credential —
            # and, on the writes, the leader's whole product — somewhere the
            # enrollment never named. (httpx already defaults to False; it is
            # spelled out because it is a security property, not a preference.)
            follow_redirects=False,
        )

    async def fetch_assignment(self, task_id: UUID) -> RepositoryAssignmentPackage:
        response = await self._call("GET", ASSIGNMENT_PATH.format(task_id=task_id))
        return _document(response, RepositoryAssignmentPackage.from_wire, "assignment package")

    async def submit_plan(
        self, task_id: UUID, decision: RepositoryPlanDecision
    ) -> PlanReceipt:
        response = await self._call(
            "POST", PLAN_PATH.format(task_id=task_id), body=decision.to_wire()
        )
        return _document(response, PlanReceipt.from_wire, "plan receipt")

    async def submit_review(
        self, task_id: UUID, decision: RepositoryReviewDecision
    ) -> ReviewReceipt:
        response = await self._call(
            "POST", REVIEW_PATH.format(task_id=task_id), body=decision.to_wire()
        )
        return _document(response, ReviewReceipt.from_wire, "review receipt")

    async def close(self) -> None:
        """Release the client. Safe on an adapter that never made a call.

        The same obligation the governed adapter carries, because the two hold
        the same thing: a connection pool that lives as long as the process
        rather than as long as a call. A composition root that shut a leader
        down without it would leave the sockets to the interpreter's exit.
        """

        await self._client.aclose()

    async def _call(
        self, method: str, path: str, *, body: dict[str, object] | None = None
    ) -> httpx.Response:
        """One request, one attempt, and the status translated into the port's
        two-word vocabulary before any caller sees it."""

        try:
            response = await self._client.request(
                method,
                path,
                json=body,
                headers={"Authorization": f"Bearer {self._credential()}"},
            )
        except httpx.HTTPError as unreachable:
            _logger.warning("%s %s failed with %s", method, path, type(unreachable).__name__)
            raise LeaderActionUnavailable(
                f"RepoMesh could not be reached: {type(unreachable).__name__}"
            ) from unreachable
        _logger.info("%s %s -> %d", method, path, response.status_code)
        _raise_for_status(response, method, path)
        return response


def _raise_for_status(response: httpx.Response, method: str, path: str) -> None:
    """429 and 5xx are unavailable; everything else non-2xx is a refusal.

    3xx included: redirects are disabled, so one means the control plane is not
    where the enrollment says it is, which no retry fixes.
    """

    status = response.status_code
    if status == 429 or status >= 500:
        raise LeaderActionUnavailable(f"RepoMesh answered {status} for {method} {path}")
    if status >= 300:
        code, message = _refusal(response)
        raise LeaderActionRefused(message, code=code)


def _refusal(response: httpx.Response) -> tuple[str | None, str]:
    """The server's own code and sentence, or the status when it gave neither.

    The frozen contract says every non-2xx body is the structured error, and
    when it is, both halves travel intact: the code because the leader lane
    branches on it, the message because it is what a person is shown. A body
    that is not that shape is a proxy answering rather than RepoMesh, so it is
    reported as the status code alone — never rendered, because a body nobody
    can vouch for is not the server's word about anything.
    """

    try:
        code, message = read_leader_action_error(response.json())
    except (ValueError, LeaderDocumentInvalid):
        return None, f"RepoMesh refused the leader action with {response.status_code}"
    return code, message


def _document[Document](
    response: httpx.Response,
    read: Callable[[object], Document],
    what: str,
) -> Document:
    """Parse a 2xx body, or refuse it.

    A document this process cannot read is not a document, and no retry makes
    it one — the same call the governed adapter makes about its receipts. The
    refusal names which document failed and carries the reader's own field-level
    complaint, which is what an operator needs and contains nothing from the
    body itself.
    """

    try:
        payload = response.json()
    except ValueError as unreadable:
        raise LeaderActionRefused(
            f"RepoMesh answered {response.status_code} with a body that is not JSON"
        ) from unreadable
    try:
        return read(payload)
    except LeaderDocumentInvalid as malformed:
        raise LeaderActionRefused(
            f"RepoMesh answered with something that is not a {what}: {malformed}"
        ) from malformed
