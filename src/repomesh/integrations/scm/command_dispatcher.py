import asyncio
import logging
import socket
from contextlib import suppress
from uuid import uuid4

from repomesh.modules.delivery import DeliveryService, SCMCommandService
from repomesh.modules.delivery.contracts import (
    RecordMergeRequestedCommand,
    SCMCommandKind,
)
from repomesh.modules.repository_intelligence.ports import RepositoryCatalog

from .contracts import MergePullRequestCommand, PullRequestState, SCMAdapter
from .delivery import parse_repository_ref

logger = logging.getLogger(__name__)


class SCMCommandDispatcher:
    """Dispatch durable provider commands and safely resume interrupted attempts."""

    def __init__(
        self,
        commands: SCMCommandService,
        delivery: DeliveryService,
        catalog: RepositoryCatalog,
        adapter: SCMAdapter,
        *,
        interval_seconds: float = 5,
        lease_owner: str | None = None,
        lease_renew_interval_seconds: float = 60,
        run_limit: int = 100,
    ) -> None:
        self._commands = commands
        self._delivery = delivery
        self._catalog = catalog
        self._adapter = adapter
        self._interval_seconds = interval_seconds
        self._lease_owner = lease_owner or f"{socket.gethostname()}:{uuid4()}"
        self._lease_renew_interval_seconds = lease_renew_interval_seconds
        self._run_limit = run_limit
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="scm-command-dispatcher")

    async def close(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        await self._task
        self._task = None

    async def run_once(self) -> None:
        for _ in range(self._run_limit):
            batch = await self._commands.claim_batch(self._lease_owner, limit=1)
            if not batch:
                return
            claimed = batch[0]
            logger.info(
                "SCM command lease claimed",
                extra={
                    "scm_command_id": str(claimed.id),
                    "lease_owner": self._lease_owner,
                    "fencing_version": claimed.version,
                },
            )
            renewal_stop = asyncio.Event()
            renewal = asyncio.create_task(
                self._renew_lease(claimed.id, claimed.version, renewal_stop),
                name=f"scm-command-renew-{claimed.id}",
            )
            try:
                await self._dispatch(claimed)
                await self._commands.accept(
                    claimed.id, self._lease_owner, claimed.version
                )
            except Exception as error:
                with suppress(Exception):
                    await self._commands.fail(
                        claimed.id,
                        f"{type(error).__name__}: {error}",
                        self._lease_owner,
                        claimed.version,
                    )
                logger.exception(
                    "SCM command dispatch failed",
                    extra={"scm_command_id": str(claimed.id)},
                )
            finally:
                renewal_stop.set()
                await renewal

    async def _renew_lease(
        self, command_id, fencing_version: int, stop: asyncio.Event
    ) -> None:
        while not stop.is_set():
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    stop.wait(), timeout=self._lease_renew_interval_seconds
                )
            if stop.is_set():
                return
            try:
                await self._commands.renew(
                    command_id, self._lease_owner, fencing_version
                )
            except Exception:
                logger.exception(
                    "SCM command lease renewal failed",
                    extra={
                        "scm_command_id": str(command_id),
                        "lease_owner": self._lease_owner,
                        "fencing_version": fencing_version,
                    },
                )
                return

    async def _dispatch(self, command) -> None:
        if command.kind is SCMCommandKind.UNDRAFT_PULL_REQUEST:
            await self._undraft(command)
            return
        if command.kind is not SCMCommandKind.MERGE_PULL_REQUEST:
            raise ValueError(f"unsupported SCM command: {command.kind}")
        profile = await self._catalog.get(command.repository_id)
        if profile is None:
            raise ValueError(f"repository not in catalog: {command.repository_id}")
        repository = parse_repository_ref(profile.url)
        number = int(command.payload["pull_request_number"])
        expected_head = str(command.payload["expected_head_sha"])
        current = await self._adapter.get_pull_request(repository, number)
        if current.head_sha != expected_head:
            raise ValueError("remote PR head SHA differs from queued command")
        if current.state is PullRequestState.MERGED:
            # The previous dispatch reached GitHub but crashed before acknowledgement.
            await self._delivery.record_merge_requested(
                RecordMergeRequestedCommand(
                    command.change_set_id, command.repository_id, expected_head
                )
            )
            return
        await self._adapter.merge_pull_request(
            MergePullRequestCommand(
                repository=repository,
                number=number,
                expected_head_sha=expected_head,
                commit_title=str(command.payload["commit_title"]),
            )
        )
        await self._delivery.record_merge_requested(
            RecordMergeRequestedCommand(command.change_set_id, command.repository_id, expected_head)
        )

    async def _undraft(self, command) -> None:
        profile = await self._catalog.get(command.repository_id)
        if profile is None:
            raise ValueError(f"repository not in catalog: {command.repository_id}")
        repository = parse_repository_ref(profile.url)
        number = int(command.payload["pull_request_number"])
        expected_head = str(command.payload["expected_head_sha"])
        current = await self._adapter.get_pull_request(repository, number)
        if current.head_sha != expected_head:
            raise ValueError("remote PR head SHA differs from queued command")
        if not current.draft or current.state is not PullRequestState.OPEN:
            # Already non-draft: either the undraft landed before the previous
            # acknowledgement, or the PR moved past open (merged/closed).
            return
        await self._adapter.ready_for_review(
            repository, number, idempotency_key=f"undraft:{command.id}"
        )

    async def _run(self) -> None:
        while not self._stop.is_set():
            await self.run_once()
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_seconds)
