import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Mapping

from .contracts import RecoveryAction, RecoveryOperationView
from .infrastructure import PostgresRecoveryCaseStore

logger = logging.getLogger(__name__)
RecoveryActionHandler = Callable[[RecoveryOperationView], Awaitable[None]]


class RecoveryActionExecutor:
    def __init__(
        self,
        store: PostgresRecoveryCaseStore,
        handlers: Mapping[RecoveryAction, RecoveryActionHandler],
        *,
        owner: str,
        interval_seconds: float = 5,
    ) -> None:
        self._store = store
        self._handlers = dict(handlers)
        self._owner = owner
        self._interval_seconds = interval_seconds
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def run_once(self) -> bool:
        operation = await self._store.claim_operation(self._owner)
        if operation is None:
            return False
        handler = self._handlers.get(operation.action)
        if handler is None:
            await self._store.finish_operation(
                operation.id, self._owner, succeeded=False,
                error_code="recovery_action_handler_unavailable",
            )
            return True
        try:
            await handler(operation)
        except Exception:
            logger.exception(
                "Unified recovery action failed case=%s action=%s",
                operation.case_id,
                operation.action.value,
            )
            await self._store.finish_operation(
                operation.id, self._owner, succeeded=False,
                error_code="recovery_action_failed",
            )
        else:
            await self._store.finish_operation(
                operation.id, self._owner, succeeded=True
            )
        return True

    async def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="recovery-action-executor")

    async def close(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        await self._task
        self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception:
                logger.exception("Unified recovery executor loop failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), self._interval_seconds)
