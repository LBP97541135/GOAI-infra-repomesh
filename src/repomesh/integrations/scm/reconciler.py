import asyncio
import logging
from contextlib import suppress

from repomesh.modules.delivery import DeliveryService

from .delivery import ChangeSetSCMCoordinator

logger = logging.getLogger(__name__)


class DeliveryReconciler:
    """Periodically recovers SCM facts missed while RepoMesh was unavailable."""

    def __init__(
        self,
        delivery: DeliveryService,
        coordinator: ChangeSetSCMCoordinator,
        *,
        interval_seconds: float = 60,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self._delivery = delivery
        self._coordinator = coordinator
        self._interval_seconds = interval_seconds
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="delivery-reconciler")

    async def close(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        await self._task
        self._task = None

    async def run_once(self) -> None:
        for change_set in await self._delivery.list_active():
            try:
                await self._coordinator.reconcile_and_merge(change_set.id)
            except Exception:
                # Retry on the next interval; one broken ChangeSet must not block the rest.
                logger.exception(
                    "delivery reconciliation failed",
                    extra={"change_set_id": str(change_set.id)},
                )

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception:
                logger.exception("delivery reconciliation scan failed")
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_seconds)
