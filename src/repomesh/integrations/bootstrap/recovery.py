import asyncio
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    attempts: int = 3
    delays: tuple[float, ...] = (1, 2)

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("retry attempts must be positive")
        if len(self.delays) < self.attempts - 1 or any(delay < 0 for delay in self.delays):
            raise ValueError("retry delays must cover attempts and be non-negative")

    async def run(
        self,
        action: Callable[[], Awaitable[T]],
        *,
        accept: Callable[[T], bool],
        retry_exceptions: tuple[type[Exception], ...] = (),
    ) -> T:
        result: T | None = None
        for attempt in range(self.attempts):
            try:
                result = await action()
            except retry_exceptions:
                if attempt + 1 == self.attempts:
                    raise
                await asyncio.sleep(self.delays[attempt])
                continue
            if accept(result) or attempt + 1 == self.attempts:
                return result
            await asyncio.sleep(self.delays[attempt])
        assert result is not None
        return result


_BEARER = re.compile(r"(?i)(bearer\s+)[^\s]+")
_SECRET_FIELD = re.compile(
    r"(?i)((?:api[_-]?key|password|secret|token|authorization)\s*[:=]\s*[\"']?)[^,\s\"']+"
)
_TOKEN_SHAPE = re.compile(r"[A-Za-z0-9_-]{32,}={0,2}")
_UUID_SHAPE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class BootstrapRedactor:
    def __init__(self, secrets: Iterable[str] = (), *, limit: int = 2000) -> None:
        if limit < 1:
            raise ValueError("redaction limit must be positive")
        self._secrets = tuple(
            sorted({secret for secret in secrets if secret}, key=len, reverse=True)
        )
        self._limit = limit

    def redact(self, text: str) -> str:
        redacted = text
        for secret in self._secrets:
            redacted = redacted.replace(secret, "[REDACTED]")
        redacted = _BEARER.sub(r"\1[REDACTED]", redacted)
        redacted = _SECRET_FIELD.sub(r"\1[REDACTED]", redacted)
        redacted = _TOKEN_SHAPE.sub(self._redact_token_shape, redacted)
        return redacted[: self._limit]

    @staticmethod
    def _redact_token_shape(match: re.Match[str]) -> str:
        value = match.group(0)
        return value if _UUID_SHAPE.fullmatch(value) else "[REDACTED]"
