"""Rate-limit aware HTTP helpers shared by the platform fetchers.

GitHub and GitLab both enforce per-hour API quotas, and an org scan fans
out over many repos — a burst of 429/403s is an eventuality, not an edge
case.  :func:`get_with_retry` backs off with exponential delay and retries,
preferring the platform's own ``Retry-After``/``X-RateLimit-Reset`` signals
when they are present.

Retry is only attempted when the response actually *looks* rate-limited:
``429`` always, ``403`` only when a rate-limit signal header is present.
A plain 403 is a permission problem and retrying it would only burn quota.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from typing import Any

import httpx

_logger = logging.getLogger(__name__)

#: Default maximum request attempts (1 initial + retries).
_DEFAULT_MAX_ATTEMPTS = 3

#: Base seconds for exponential backoff: ``base * 2 ** (attempt - 1)``.
_DEFAULT_BASE_DELAY = 1.0

#: Cap on a single wait, so a hostile ``Retry-After`` cannot stall a scan.
_DEFAULT_MAX_WAIT = 30.0


def _rate_limit_wait(
    response: httpx.Response,
    attempt: int,
    *,
    base_delay: float = _DEFAULT_BASE_DELAY,
    max_wait: float = _DEFAULT_MAX_WAIT,
) -> float | None:
    """Seconds to wait before retrying, or ``None`` if not rate-limited.

    ``attempt`` is 1-based (the request that just failed).  Wait order:
    ``Retry-After`` header → ``X-RateLimit-Reset`` (GitHub, unix seconds)
    → exponential backoff ``base_delay * 2 ** (attempt - 1)``, all capped
    at *max_wait*.
    """

    status = response.status_code
    headers = response.headers
    if status == 429:
        pass  # rate limited by definition
    elif status == 403 and (
        "retry-after" in headers
        or "x-ratelimit-remaining" in headers
        or "x-ratelimit-reset" in headers
    ):
        pass  # GitHub/GitLab signal an exhausted quota, not bad permissions
    else:
        return None

    backoff = min(base_delay * (2 ** (attempt - 1)), max_wait)

    retry_after = headers.get("retry-after")
    if retry_after is not None:
        try:
            return min(float(retry_after), max_wait)
        except ValueError:
            return backoff

    reset = headers.get("x-ratelimit-reset")
    if reset is not None:
        try:
            wait = float(reset) - time.time()
        except ValueError:
            return backoff
        return min(wait, max_wait) if wait > 0 else backoff

    return backoff


async def get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: Mapping[str, str],
    params: Mapping[str, Any] | None = None,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    base_delay: float = _DEFAULT_BASE_DELAY,
    max_wait: float = _DEFAULT_MAX_WAIT,
) -> httpx.Response:
    """GET with rate-limit aware retries.

    Retries ``429`` (and ``403`` with a rate-limit signal) up to
    *max_attempts* attempts total, honouring the platform's wait hints
    (see :func:`_rate_limit_wait`).  The final response is returned either
    way — callers keep their existing ``raise_for_status``/404 handling, so
    a still-rate-limited final attempt surfaces as an ``HTTPStatusError``
    exactly like today.
    """

    assert max_attempts >= 1
    for attempt in range(1, max_attempts + 1):
        response = await client.get(url, params=params, headers=headers)
        if attempt >= max_attempts:
            return response
        wait = _rate_limit_wait(
            response, attempt, base_delay=base_delay, max_wait=max_wait
        )
        if wait is None:
            return response
        _logger.debug(
            "Rate limited (%s) on %s, retrying in %.1fs",
            response.status_code,
            url,
            wait,
        )
        await asyncio.sleep(wait)
    return response  # pragma: no cover — loop always returns
