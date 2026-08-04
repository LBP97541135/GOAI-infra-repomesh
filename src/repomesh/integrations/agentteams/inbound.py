import asyncio
import logging
from contextlib import suppress

from repomesh.modules.collaboration.contracts import (
    InboundMatrixMessage,
    MatrixInboundProcessor,
)

from .matrix import AgentTeamsMatrixClient

_logger = logging.getLogger(__name__)


class AgentTeamsMatrixInboundPoller:
    def __init__(
        self,
        client: AgentTeamsMatrixClient,
        processor: MatrixInboundProcessor,
        *,
        sync_timeout_ms: int = 20_000,
    ) -> None:
        self._client = client
        self._processor = processor
        self._sync_timeout_ms = sync_timeout_ms
        self._since: str | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(
                self._run(), name="agentteams-matrix-inbound"
            )

    async def close(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def run_once(self, *, timeout_ms: int = 0) -> int:
        batch = await self._client.sync_once(
            since=self._since,
            timeout_ms=timeout_ms,
        )
        processed = 0
        failed = False
        for message in batch.messages:
            try:
                await self._processor.execute(
                    InboundMatrixMessage(
                        event_id=message.event_id,
                        room_id=message.room_id,
                        sender=message.sender,
                        body=message.body,
                    )
                )
                processed += 1
            except Exception:
                _logger.exception("Failed to process Matrix event %s", message.event_id)
                failed = True
        if failed:
            raise RuntimeError("one or more Matrix events failed; batch will be retried")
        self._since = batch.next_batch
        return processed

    async def _run(self) -> None:
        while True:
            try:
                await self.run_once(timeout_ms=self._sync_timeout_ms)
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.exception("AgentTeams Matrix sync failed; retrying")
                await asyncio.sleep(2)
