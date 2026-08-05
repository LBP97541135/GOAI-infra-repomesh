"""Where a Runner process gets its work from.

The transport is deliberately behind a protocol: the runtime contract says the envelope does not
change with the transport, so HTTP long polling is one implementation among possible queue or
object-store ones.

Pacing lives inside the source rather than in the serve loop. The loop treats ``None`` as "nothing
to do, ask again", so a source that returns ``None`` without waiting would spin. Every non-task
outcome therefore either waited on the server (a long poll that expired) or waits locally (a
failure, backed off).
"""

import asyncio
import logging
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


class TaskSource(Protocol):
    async def next_task(self) -> RunnerTask | None:
        """Return the next task, or ``None`` when this cycle produced no work."""
        ...


class HttpLongPollTaskSource:
    """Long-polls an HTTP endpoint for the next :class:`RunnerTask`.

    ``200`` carries a task envelope, ``204`` means the poll expired with no work. Anything else —
    including a transport error and a body that fails to parse — is reported as "no work" after an
    exponential backoff, because a worker that dies on a bad dispatcher response is worse than one
    that keeps asking. A malformed body is backed off too: it cannot be acknowledged, so without a
    wait it would be re-delivered in a hot loop.
    """

    def __init__(
        self,
        url: str,
        *,
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
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
        self._base_backoff_seconds = base_backoff_seconds
        self._max_backoff_seconds = max_backoff_seconds
        self._consecutive_failures = 0

    async def next_task(self) -> RunnerTask | None:
        try:
            response = await self._client.get(self._url, params={"wait": self._timeout_seconds})
        except httpx.HTTPError as error:
            _logger.warning("task source request failed: %s", type(error).__name__)
            await self._back_off()
            return None

        if response.status_code == 204:
            self._consecutive_failures = 0
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

    async def _back_off(self) -> None:
        delay = min(
            self._base_backoff_seconds * (2**self._consecutive_failures),
            self._max_backoff_seconds,
        )
        self._consecutive_failures += 1
        await self._sleep(delay)
