import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime

from repomesh.modules.collaboration.contracts import (
    InboundMatrixMessage,
    MatrixInboundProcessor,
    RecordRoomTimelineCommand,
    RoomTimelineIngest,
)

from .matrix import AgentTeamsMatrixClient

_logger = logging.getLogger(__name__)


class AgentTeamsMatrixInboundPoller:
    """One ``/sync`` loop, two consumers of every message it sees.

    The timeline recorder runs *first* and the task-report consumer second,
    and the order is deliberate: recording is the weaker act — it stores what
    was said and moves nothing — so a message that goes on to be refused as a
    task report is still visible in the room, which is exactly what somebody
    asking "why did nothing happen when I said that?" needs to see. Running
    the recorder second would make the transcript conditional on the report
    path not raising.
    """

    def __init__(
        self,
        client: AgentTeamsMatrixClient,
        processor: MatrixInboundProcessor,
        timeline: RoomTimelineIngest | None = None,
        *,
        sync_timeout_ms: int = 20_000,
    ) -> None:
        self._client = client
        self._processor = processor
        # None only where no timeline store is composed. The room stream then
        # shows RepoMesh's own messages and no others, which is what it showed
        # before this lane existed — not a silent half-ingest.
        self._timeline = timeline
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
            occurred_at = datetime.fromtimestamp(message.origin_server_ts / 1000, tz=UTC)
            try:
                if self._timeline is not None:
                    await self._timeline.record(
                        RecordRoomTimelineCommand(
                            event_id=message.event_id,
                            room_id=message.room_id,
                            sender_matrix_user_id=message.sender,
                            body=message.body,
                            occurred_at=occurred_at,
                        )
                    )
                await self._processor.execute(
                    InboundMatrixMessage(
                        event_id=message.event_id,
                        room_id=message.room_id,
                        sender=message.sender,
                        body=message.body,
                        occurred_at=occurred_at,
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
