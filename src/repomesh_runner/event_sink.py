"""Delivery of Runner events back to the control plane over HTTP.

Receivers deduplicate events by ``eventId`` and the engine derives that id deterministically from
``run/attempt/sequence``, so a redelivery is free: retrying is always safe here.

The status split is the interesting part. ``5xx``, ``429`` and transport errors are transient — the
receiver may yet accept the event, so retry. Any other ``4xx`` means the receiver looked at a
contract-shaped payload and refused it; retrying an argument the peer has already rejected only
delays the report, so it surfaces as :class:`EventDeliveryError` for the serve loop to log.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from .contracts import RunnerEvent

_logger = logging.getLogger(__name__)

__all__ = ["EventDeliveryError", "HttpEventSink"]

DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_BASE_BACKOFF_SECONDS = 1.0
DEFAULT_MAX_BACKOFF_SECONDS = 60.0
IDEMPOTENCY_HEADER = "Idempotency-Key"


class EventDeliveryError(RuntimeError):
    """Raised when an event could not be delivered and retrying would not help."""


class HttpEventSink:
    """POSTs :meth:`RunnerEvent.to_wire` payloads with an ``Idempotency-Key`` header."""

    def __init__(
        self,
        url: str,
        *,
        client: httpx.AsyncClient | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        base_backoff_seconds: float = DEFAULT_BASE_BACKOFF_SECONDS,
        max_backoff_seconds: float = DEFAULT_MAX_BACKOFF_SECONDS,
        timeout_seconds: float = 30.0,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self._url = url
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._base_backoff_seconds = base_backoff_seconds
        self._max_backoff_seconds = max_backoff_seconds

    async def publish(self, event: RunnerEvent, *, idempotency_key: str) -> None:
        payload = event.to_wire()
        headers = {IDEMPOTENCY_HEADER: idempotency_key}
        reason = "not attempted"

        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._client.post(self._url, json=payload, headers=headers)
            except httpx.HTTPError as error:
                reason = f"transport error {type(error).__name__}"
            else:
                status = response.status_code
                if 200 <= status < 300:
                    return
                if status != 429 and 400 <= status < 500:
                    raise EventDeliveryError(
                        f"event {event.event_id} was rejected with status {status}"
                    )
                reason = f"status {status}"

            _logger.warning(
                "event %s delivery attempt %s/%s failed: %s",
                event.event_id,
                attempt,
                self._max_attempts,
                reason,
            )
            if attempt < self._max_attempts:
                await self._sleep(self._backoff_delay(attempt))

        raise EventDeliveryError(
            f"event {event.event_id} was not delivered after {self._max_attempts} attempts "
            f"(last failure: {reason})"
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _backoff_delay(self, attempt: int) -> float:
        return min(self._base_backoff_seconds * (2 ** (attempt - 1)), self._max_backoff_seconds)
