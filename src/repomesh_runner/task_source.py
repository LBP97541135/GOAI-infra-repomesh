"""Where a Runner process gets its work from.

The transport is deliberately behind a protocol: the runtime contract says the envelope does not
change with the transport, so HTTP long polling is one implementation among possible queue or
object-store ones.

Pacing lives inside the source rather than in the serve loop. The loop treats ``None`` as "nothing
to do, ask again", so a source that returns ``None`` without waiting would spin. Every non-task
outcome therefore covers a full wait before it returns: an empty poll waits out the poll window
(on the server if it holds the connection, locally for whatever it did not hold), and a failure
waits locally, backed off.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx

from .contracts import RunnerTask
from .wire import WireError, parse_runner_task

_logger = logging.getLogger(__name__)

__all__ = ["HttpLongPollTaskSource", "TaskSource"]

DEFAULT_BASE_BACKOFF_SECONDS = 1.0
DEFAULT_MAX_BACKOFF_SECONDS = 60.0
_REQUEST_TIMEOUT_MARGIN_SECONDS = 10.0

# Even a server that honours ``wait`` returns a little short of it: clock skew between the two
# machines, scheduler jitter, and the response travelling back all shave milliseconds off the
# measured window. Pacing that shortfall would add a pointless sleep to every well-behaved poll,
# so only a shortfall larger than this tolerance counts as "the server answered early".
_EMPTY_POLL_TOLERANCE_SECONDS = 0.5


class TaskSource(Protocol):
    async def next_task(self) -> RunnerTask | None:
        """Return the next task, or ``None`` when this cycle produced no work."""
        ...


class HttpLongPollTaskSource:
    """Long-polls an HTTP endpoint for the next :class:`RunnerTask`.

    ``200`` carries a task envelope and ``204`` means "no work". Nothing on the wire obliges the
    server to actually hold a ``204`` for the requested ``wait``, and the client cannot tell a
    long poll that expired from one answered instantly by a dispatcher that does not implement
    long polling, a proxy that returns early, or a misconfigured endpoint. Since the serve loop
    reads ``None`` as "ask again", trusting the server there turns an instant ``204`` into a hot
    loop: thousands of requests a minute, burnt CPU, flooded logs. So the source measures how long
    the request took and sleeps out the unused remainder of the window before returning ``None``.
    A server that holds the connection pays nothing; one that answers instantly is throttled to
    one request per ``timeout_seconds``.

    Anything else — including a transport error and a body that fails to parse — is reported as
    "no work" after an exponential backoff, because a worker that dies on a bad dispatcher
    response is worse than one that keeps asking. A malformed body is backed off too: it cannot be
    acknowledged, so without a wait it would be re-delivered in a hot loop.
    """

    def __init__(
        self,
        url: str,
        *,
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        base_backoff_seconds: float = DEFAULT_BASE_BACKOFF_SECONDS,
        max_backoff_seconds: float = DEFAULT_MAX_BACKOFF_SECONDS,
    ) -> None:
        self._url = url
        self._timeout_seconds = timeout_seconds
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout_seconds + _REQUEST_TIMEOUT_MARGIN_SECONDS
        )
        self._sleep = sleep
        self._monotonic = monotonic
        self._base_backoff_seconds = base_backoff_seconds
        self._max_backoff_seconds = max_backoff_seconds
        self._consecutive_failures = 0

    async def next_task(self) -> RunnerTask | None:
        started_at = self._monotonic()
        try:
            response = await self._client.get(self._url, params={"wait": self._timeout_seconds})
        except httpx.HTTPError as error:
            _logger.warning("task source request failed: %s", type(error).__name__)
            await self._back_off()
            return None

        if response.status_code == 204:
            self._consecutive_failures = 0
            await self._pace_empty_poll(started_at)
            return None

        if response.status_code != 200:
            _logger.warning("task source returned status %s", response.status_code)
            await self._back_off()
            return None

        try:
            task = parse_runner_task(response.json())
        except (WireError, ValueError) as error:
            _logger.error("task source returned an unparseable task: %s", error)
            await self._back_off()
            return None

        self._consecutive_failures = 0
        _logger.info("accepted task run=%s attempt=%s", task.run_id, task.attempt)
        return task

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _pace_empty_poll(self, started_at: float) -> None:
        """Sleep out whatever is left of the poll window the server declined to hold."""

        remaining = self._timeout_seconds - (self._monotonic() - started_at)
        if remaining > _EMPTY_POLL_TOLERANCE_SECONDS:
            _logger.debug("empty poll returned early; pacing for %.3fs", remaining)
            await self._sleep(remaining)

    async def _back_off(self) -> None:
        delay = min(
            self._base_backoff_seconds * (2**self._consecutive_failures),
            self._max_backoff_seconds,
        )
        self._consecutive_failures += 1
        await self._sleep(delay)
