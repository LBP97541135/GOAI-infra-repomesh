"""``WorkerBindingPort`` over RepoMesh's HTTP preflight endpoint.

One GET, and everything interesting is the translation table around it. The
split between the two failure types is "can a retry fix it", not HTTP semantics:
a 503 from an unconfigured control plane and a 409 from a still-managed worker
are both refusals in HTTP's vocabulary, but only one of them is worth waiting
for. That is why 429 and every 5xx are retried while every 4xx is not.

**Two versions, two paths, and no content negotiation.** Which endpoint this
adapter calls is decided by the enrollment it was handed, because that is the
only honest source: a v1 enrollment gets v1's request and v1's reader, byte for
byte what a deployed Bridge has always sent, and a v2 enrollment gets the
``/runtime/v2/external-members`` sibling with its ``role`` query parameter. The
server made the same call for the same reason (``agent_runtime/api/router.py``):
a deployed v1 consumer must never discover that the endpoint it has always
called started answering a longer document.

The v2 exchange carries ``role`` in both directions and the adapter checks that
they agree. RepoMesh checks it too and answers 409, and the duplication is
deliberate: preflight exists to catch configuration drift between what this
machine believes and what the control plane has on file, and every other
identity field on this answer is already held to the same standard one layer up.

Only the status code, the method, the path and the attempt number are logged.
Never the body (it is the binding), never the enrollment, and never the
credential.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

import httpx

from ..contracts import (
    ENROLLMENT_V2_SCHEMA_VERSION,
    BindingRefused,
    BindingUnavailable,
    ExternalWorkerEnrollment,
    WorkerBinding,
)

__all__ = ["BINDING_PATH", "BINDING_V2_PATH", "RepoMeshBindingAdapter"]

_logger = logging.getLogger(__name__)

BINDING_PATH = "/api/v1/runtime/external-workers/{worker_agent_id}/binding"
"""Where RepoMesh serves the v1 preflight document.

The ``/api/v1`` prefix belongs to the path rather than to the enrollment's
``repomeshEndpoint``, which the schema describes as the control plane's base
URL: the same base URL is what governed execution will post runs to in PR 5, so
each caller owns its own path under it.
"""

BINDING_V2_PATH = "/api/v1/runtime/v2/external-members/{worker_agent_id}/binding"
"""Where RepoMesh serves the v2 preflight document.

A sibling route rather than the same one answering a longer body. The path
segment is ``external-members`` because that is what v2 generalised the concept
to; the ``{worker_agent_id}`` placeholder keeps its historical name here for the
same reason the wire field does.
"""

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 0.5
DEFAULT_TIMEOUT_SECONDS = 10.0

Sleeper = Callable[[float], Awaitable[None]]


class RepoMeshBindingAdapter:
    """Production :class:`WorkerBindingPort`.

    The retry policy is fixed here rather than left to a caller: the call is a
    GET, so it is safe to repeat, and it happens exactly once per process
    lifetime, so an unbounded wait would just be a hang with extra steps. Three
    attempts with exponential backoff, and the sleeper is injectable so tests
    prove the policy without spending the wall-clock time it describes.
    """

    requires_credential = True

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
        sleep: Sleeper = asyncio.sleep,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._transport = transport
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        self._sleep = sleep

    async def fetch_binding(
        self, enrollment: ExternalWorkerEnrollment, *, credential: str | None
    ) -> WorkerBinding:
        versioned = enrollment.schema_version == ENROLLMENT_V2_SCHEMA_VERSION
        template = BINDING_V2_PATH if versioned else BINDING_PATH
        path = template.format(worker_agent_id=enrollment.worker_agent_id)
        # The enrollment's claimed role, as a query parameter the v2 endpoint
        # requires. Sent from the enrollment rather than from anything else this
        # process knows, because it is exactly the claim RepoMesh is being asked
        # to confirm or contradict.
        params = {"role": enrollment.role} if versioned else None
        headers = {"Accept": "application/json"}
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        for attempt in range(1, self._max_attempts + 1):
            try:
                return await self._attempt(enrollment, path, params, headers, attempt)
            except BindingUnavailable:
                if attempt == self._max_attempts:
                    raise
                await self._sleep(self._backoff_seconds * 2 ** (attempt - 1))
        raise AssertionError("unreachable: the loop either returns or raises")

    async def _attempt(
        self,
        enrollment: ExternalWorkerEnrollment,
        path: str,
        params: dict[str, str] | None,
        headers: dict[str, str],
        attempt: int,
    ) -> WorkerBinding:
        async with httpx.AsyncClient(
            base_url=enrollment.repomesh_endpoint.rstrip("/"),
            timeout=self._timeout,
            transport=self._transport,
            # A redirect on a control-plane read is a misconfiguration, not a
            # route: following one would send the credential somewhere the
            # enrollment never named. (httpx already defaults to False; it is
            # spelled out because it is a security property, not a preference.)
            follow_redirects=False,
        ) as client:
            try:
                response = await client.get(path, params=params, headers=headers)
            except httpx.HTTPError as unreachable:
                _logger.warning(
                    "GET %s failed with %s (attempt %d/%d)",
                    path,
                    type(unreachable).__name__,
                    attempt,
                    self._max_attempts,
                )
                raise BindingUnavailable(
                    f"RepoMesh preflight is unreachable: {type(unreachable).__name__}"
                ) from unreachable
            _logger.debug(
                "GET %s -> %d (attempt %d/%d)",
                path,
                response.status_code,
                attempt,
                self._max_attempts,
            )
            return _translate(response, path, enrollment)


def _translate(
    response: httpx.Response, path: str, enrollment: ExternalWorkerEnrollment
) -> WorkerBinding:
    status = response.status_code
    if status == 429 or status >= 500:
        raise BindingUnavailable(f"RepoMesh preflight answered {status} for {path}")
    if status != 200:
        # 3xx included: with redirects disabled it means the control plane is
        # somewhere other than where the enrollment says, which no retry fixes.
        raise BindingRefused(f"RepoMesh preflight refused the binding: {status} for {path}")
    try:
        payload = response.json()
    except ValueError as unreadable:
        raise BindingRefused(
            "RepoMesh preflight answered 200 with a body that is not JSON"
        ) from unreadable
    if enrollment.schema_version != ENROLLMENT_V2_SCHEMA_VERSION:
        return WorkerBinding.from_wire(payload)
    binding = WorkerBinding.from_wire_v2(payload)
    if binding.role != enrollment.role:
        # RepoMesh answers 409 for this and the check would usually never fire.
        # It is here because "usually" is the wrong standard for the field that
        # decides whether this process may be handed a workspace at all: a
        # Bridge that took the answer's word for its own role would inherit
        # whatever a mistaken or compromised control plane said, and the whole
        # point of preflight is that a disagreement is a refusal.
        raise BindingRefused(
            f"role disagrees with RepoMesh: {enrollment.role!r} != {binding.role!r}"
        )
    return binding
