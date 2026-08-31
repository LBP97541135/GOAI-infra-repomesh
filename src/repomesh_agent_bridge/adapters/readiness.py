"""``ReadinessReporter`` over RepoMesh's external-member readiness endpoint.

One POST, three kinds, and one integer read back out of the answer. What is
interesting is the two decisions around it.

**It opens a client per report, unlike the two adapters next door.** Those hold
a pool for the life of the process and owe a ``close`` that the composition root
calls; this one is asked to speak on the way *out* as well as while running, and
a goodbye made through a pool something else had already closed would fail for a
reason that has nothing to do with the control plane. A report every renew
period is nowhere near often enough to pay for that hazard, so the client's
lifetime is the call's — the same shape the preflight read uses, and for a
neighbouring reason.

**It does not retry.** The renewal loop above it is the retry: a report that
failed is followed by the next one a period later, and the lease is three
periods long, so a transient failure costs nothing and a second immediate
attempt would buy nothing. The startup report is deliberately the same — a
Bridge that cannot be seen by the platform should refuse quickly and let a
supervisor decide, rather than hold an operator's terminal while it hopes.

Only the method, the path, the kind and the status code are logged. Never the
credential, never the workspace root — that path belongs to the operator's own
machine, and it is in the body because the server checks it, not because
anything may print it.
"""

import logging
from collections.abc import Callable
from uuid import UUID

import httpx

from ..contracts import READINESS_SCHEMA_VERSION, BridgeStartupError
from ..ports import ReadinessRejected

__all__ = ["READINESS_PATH", "RepoMeshReadinessReporter"]

_logger = logging.getLogger(__name__)

READINESS_PATH = "/api/v1/runtime/v1/external-members/{member_agent_id}/readiness"
"""Where RepoMesh takes a member's report about its own process.

The ``/api/v1`` prefix belongs to the path rather than to the enrollment's
``repomeshEndpoint``, exactly as it does for preflight and for the two action
adapters. The ``/runtime/v1`` segment after it is the server's own versioning of
this face and is unrelated to the enrollment's schema version: a v1 and a v2
enrollment report readiness through the same route, because a running process is
a running process whichever document described it.
"""

DEFAULT_TIMEOUT_SECONDS = 10.0
"""The preflight read's ten rather than the actions' thirty.

Writing a lease row is the cheapest thing this control plane does, so a report
that has not been answered in ten seconds is a network problem. At startup that
number is how long an operator waits to be refused; on the loop it is bounded
well inside the renew period, so a slow answer cannot pile renewals up behind it.
"""

CredentialResolver = Callable[[str], str]
"""``resolve(ref) -> secret``, called per request.

The application's own resolver signature, spelled here rather than imported so
an adapter does not depend on the composition root — the same reason the two
action adapters each spell their zero-argument provider locally. This one takes
the reference because the reporter is built from an enrollment and holds the
slot's locator, not the secret: resolution stays late, so the value's lifetime
is the request's and no attribute of this object ever holds one.
"""


class RepoMeshReadinessReporter:
    """Production :class:`~repomesh_agent_bridge.ports.ReadinessReporter`.

    Every fact in the body except ``kind`` is fixed when the composition root
    builds this, because every one of them is a claim about how *this process*
    was assembled: which member it serves, which instance of it this is, which
    role and which lanes it came up with, and which directory its governed runs
    happen under. RepoMesh checks each against what it has on file, so nothing
    here is derived from anything the process learns later — a reporter that
    re-read its own lanes mid-life would be reporting a second, weaker answer to
    a question the composition root already settled.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        member_agent_id: UUID,
        resolve_credential: CredentialResolver,
        credential_ref: str,
        instance_id: UUID,
        role: str,
        leader_lane: bool,
        governed_lane: bool,
        workspace_root: str | None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._path = READINESS_PATH.format(member_agent_id=member_agent_id)
        self._resolve_credential = resolve_credential
        self._credential_ref = credential_ref
        self._facts: dict[str, object] = {
            "schema": READINESS_SCHEMA_VERSION,
            "instanceId": str(instance_id),
            "role": role,
            "leaderLane": leader_lane,
            "governedLane": governed_lane,
            "workspaceRoot": workspace_root,
        }
        self._transport = transport
        self._timeout = timeout

    async def report_startup(self) -> int:
        return await self._post("startup")

    async def report_renew(self) -> int:
        return await self._post("renew")

    async def report_shutdown(self) -> None:
        """The goodbye. Its receipt names a renew period nothing will use."""

        await self._post("shutdown")

    async def _post(self, kind: str) -> int:
        """One report, one attempt, and the answer's renew period."""

        async with httpx.AsyncClient(
            base_url=self._endpoint,
            headers={"Accept": "application/json"},
            timeout=self._timeout,
            transport=self._transport,
            # A redirect on an authenticated write would send the credential
            # somewhere the enrollment never named. (httpx already defaults to
            # False; it is spelled out because it is a security property, not a
            # preference.)
            follow_redirects=False,
        ) as client:
            try:
                response = await client.post(
                    self._path,
                    json={**self._facts, "kind": kind},
                    headers={
                        "Authorization": f"Bearer {self._resolve_credential(self._credential_ref)}"
                    },
                )
            except httpx.HTTPError as unreachable:
                _logger.warning(
                    "POST %s (%s) failed with %s", self._path, kind, type(unreachable).__name__
                )
                raise BridgeStartupError(
                    f"RepoMesh could not be told this member is {kind}: "
                    f"{type(unreachable).__name__}"
                ) from unreachable
        # Taking and releasing a lease are events; keeping one is the weather.
        # A renewal every third of a TTL, forever, at INFO would bury the two
        # lines an operator actually reads under one that never says anything
        # new — and a renewal that *failed* is logged by the loop above as a
        # warning regardless of this level.
        _logger.log(
            logging.DEBUG if kind == "renew" else logging.INFO,
            "POST %s (%s) -> %d",
            self._path,
            kind,
            response.status_code,
        )
        return _renew_after(response, kind, self._path)


def _renew_after(response: httpx.Response, kind: str, path: str) -> int:
    """The period the server chose, or the refusal it made instead.

    The ``stale_instance`` 409 is singled out because it is the single answer
    the caller acts on rather than reports, and it is matched on the server's
    own code rather than on its sentence — which is why the endpoint puts a code
    there at all. Every other non-2xx is the ordinary startup-refusal family:
    a member RepoMesh does not know, a credential that names somebody else, a
    report whose lanes disagree with the directory. None of them is fixed by
    asking again, and the renewal loop grades them all the same way.

    A 2xx whose body is not the receipt is a refusal too, on the reasoning the
    two adapters next door use about their own answers: a receipt this process
    cannot read is not a receipt, and no retry makes it one. It matters more
    here than there, because this exchange is the one an operator meets first —
    a captive portal answering 200 with a login page would otherwise reach the
    CLI as a ``JSONDecodeError`` traceback instead of the single line the exit
    mapping promises.
    """

    status = response.status_code
    if status == 409 and _superseded(response):
        raise ReadinessRejected(
            "a newer Bridge instance holds this member's readiness; this one is superseded"
        )
    if status >= 300:
        # 3xx included: with redirects disabled it means the control plane is
        # somewhere other than where the enrollment says, which no retry fixes.
        raise BridgeStartupError(
            f"RepoMesh refused the {kind} readiness report with {status} for {path}"
        )
    try:
        payload = response.json()
    except ValueError as unreadable:
        raise BridgeStartupError(
            f"RepoMesh answered the {kind} readiness report with {status} for {path} "
            "and a body that is not JSON"
        ) from unreadable
    try:
        return int(payload["renewAfterSeconds"])
    except (KeyError, TypeError, ValueError) as unreadable:
        raise BridgeStartupError(
            f"RepoMesh took the {kind} readiness report but its answer names no renew "
            f"period: {status} for {path}"
        ) from unreadable


def _superseded(response: httpx.Response) -> bool:
    """Whether this 409 is the structured refusal that names a newer instance.

    The endpoint answers 409 for two different things and only puts an object in
    ``detail`` for one of them: a report that does not add up to a readiness
    this member may hold arrives as a plain sentence, and being replaced arrives
    as a code. Reading the shape is how the two are told apart without matching
    on prose.

    A body that is not JSON at all is a proxy answering rather than RepoMesh, so
    it names no instance and is not a takeover — it falls through to the
    ordinary refusal above, which is the honest thing to tell an operator about
    a 409 nobody can vouch for.
    """

    try:
        payload = response.json()
    except ValueError:
        payload = None
    detail = payload.get("detail") if isinstance(payload, dict) else None
    return isinstance(detail, dict) and detail.get("code") == "stale_instance"
