import asyncio
import hashlib
import json
import logging
from contextlib import suppress
from datetime import UTC, datetime

from repomesh.modules.delivery import (
    DeliveryService,
    SCMObservationService,
    SCMPollCursorService,
)
from repomesh.modules.delivery.contracts import (
    RecordSCMObservationCommand,
    RepositoryDeliveryStatus,
    SCMObservationSource,
)
from repomesh.modules.repository_intelligence.ports import RepositoryCatalog

from .contracts import PullRequestObservation, SCMAdapter, SCMRateLimited
from .delivery import parse_repository_ref
from .observation_processor import GitHubObservationProcessor

logger = logging.getLogger(__name__)


class GitHubObservationPoller:
    """Acquire missing GitHub facts into the same durable inbox as webhooks."""

    def __init__(
        self,
        delivery: DeliveryService,
        observations: SCMObservationService,
        cursors: SCMPollCursorService,
        catalog: RepositoryCatalog,
        adapter: SCMAdapter,
        processor: GitHubObservationProcessor,
        *,
        scan_interval_seconds: float = 15,
    ) -> None:
        self._delivery = delivery
        self._observations = observations
        self._cursors = cursors
        self._catalog = catalog
        self._adapter = adapter
        self._processor = processor
        self._scan_interval_seconds = scan_interval_seconds
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="github-observation-poller")

    async def close(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        await self._task
        self._task = None

    async def run_once(self) -> None:
        for change_set in await self._delivery.list_active():
            for candidate in change_set.repositories:
                if candidate.status is RepositoryDeliveryStatus.MERGED:
                    continue
                if candidate.pull_request_number is None:
                    continue
                if not await self._cursors.due(change_set.id, candidate.repository_id):
                    continue
                try:
                    await self._poll_candidate(change_set.id, candidate)
                    await self._cursors.succeed(change_set.id, candidate.repository_id)
                except SCMRateLimited as error:
                    await self._cursors.fail(
                        change_set.id,
                        candidate.repository_id,
                        str(error),
                        retry_after=error.retry_after_seconds,
                    )
                except Exception as error:
                    await self._cursors.fail(
                        change_set.id,
                        candidate.repository_id,
                        f"{type(error).__name__}: {error}",
                    )
                    logger.exception(
                        "GitHub observation polling failed",
                        extra={
                            "change_set_id": str(change_set.id),
                            "repository_id": str(candidate.repository_id),
                        },
                    )

    async def _poll_candidate(self, change_set_id, candidate) -> None:
        profile = await self._catalog.get(candidate.repository_id)
        if profile is None:
            raise ValueError(f"repository not in catalog: {candidate.repository_id}")
        repository = parse_repository_ref(profile.url)
        pull_request = await self._adapter.get_pull_request(
            repository, candidate.pull_request_number
        )
        await self._record(
            change_set_id,
            candidate.repository_id,
            "pull_request",
            self._pull_request_payload(pull_request),
            f"pr:{pull_request.number}:{pull_request.head_sha}:"
            f"{pull_request.state.value}:{pull_request.merge_sha or '-'}",
        )
        for check in await self._adapter.list_check_runs(repository, candidate.commit_sha):
            payload = {
                "repository": self._repository_payload(repository),
                "check_run": {
                    "id": check.check_run_id,
                    "name": check.check_name,
                    "head_sha": check.head_sha,
                    "status": "completed" if check.terminal else "in_progress",
                    "conclusion": "success"
                    if check.terminal and check.passed
                    else ("failure" if check.terminal else None),
                    "output": {"summary": check.summary},
                },
            }
            await self._record(
                change_set_id,
                candidate.repository_id,
                "check_run",
                payload,
                f"check:{check.check_run_id}:{check.head_sha}:"
                f"{int(check.terminal)}:{int(check.passed)}",
            )
        for review in await self._adapter.list_pull_request_reviews(
            repository, candidate.pull_request_number
        ):
            payload = {
                "action": "submitted",
                "repository": self._repository_payload(repository),
                "pull_request": {"head": {"sha": review.head_sha}},
                "review": {
                    "id": review.review_id,
                    "user": {"login": review.reviewer},
                    "commit_id": review.head_sha,
                    "state": review.state.value,
                    "body": review.summary,
                },
            }
            await self._record(
                change_set_id,
                candidate.repository_id,
                "pull_request_review",
                payload,
                f"review:{review.review_id}:{review.head_sha}:{review.state.value}",
            )

    async def _record(
        self,
        change_set_id,
        repository_id,
        event_type: str,
        payload: dict[str, object],
        external_id: str,
    ) -> None:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        recorded = await self._observations.record(
            RecordSCMObservationCommand(
                provider="github",
                source=SCMObservationSource.POLLER,
                external_id=f"{change_set_id}:{repository_id}:{external_id}",
                event_type=event_type,
                payload=payload,
                payload_hash=hashlib.sha256(encoded).hexdigest(),
                observed_at=datetime.now(UTC),
                change_set_id=change_set_id,
                repository_id=repository_id,
            )
        )
        if recorded.created:
            await self._processor.process(recorded.observation.id)

    @staticmethod
    def _repository_payload(repository) -> dict[str, object]:
        return {"name": repository.name, "owner": {"login": repository.owner}}

    @classmethod
    def _pull_request_payload(cls, item: PullRequestObservation) -> dict[str, object]:
        return {
            "repository": cls._repository_payload(item.repository),
            "pull_request": {
                "number": item.number,
                "html_url": item.url,
                "state": "closed" if item.state.value == "merged" else item.state.value,
                "merged": item.state.value == "merged",
                "merged_at": "observed" if item.state.value == "merged" else None,
                "draft": item.draft,
                "head": {"ref": item.head_branch, "sha": item.head_sha},
                "base": {"ref": item.base_branch, "sha": item.base_sha},
                "mergeable": item.mergeable,
                "merge_commit_sha": item.merge_sha,
            },
        }

    async def _run(self) -> None:
        while not self._stop.is_set():
            await self.run_once()
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._scan_interval_seconds)
