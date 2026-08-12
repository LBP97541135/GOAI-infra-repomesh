import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from repomesh.modules.delivery import (
    DeliveryConflict,
    DeliveryNotFound,
    DeliveryService,
    SCMObservationService,
)
from repomesh.modules.delivery.contracts import (
    ChangeSetView,
    MergeObservationCommand,
    PullRequestObservationCommand,
    RepositoryDeliveryStatus,
)
from repomesh.modules.repository_intelligence.ports import RepositoryCatalog

from .contracts import PullRequestState, SCMConflict
from .delivery import ChangeSetSCMCoordinator, parse_repository_ref
from .github_events import (
    parse_github_check_run,
    parse_github_pull_request,
    parse_github_pull_request_review,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProcessedSCMObservation:
    ignored: bool
    claimed: bool
    change_set: ChangeSetView | None = None


class CIReworkTaskGateway(Protocol):
    async def create_for_failed_candidate(
        self, change_set: ChangeSetView, repository_id: UUID
    ) -> UUID: ...


class GitHubObservationProcessor:
    """Project one durable GitHub fact into Delivery state."""

    _SUPPORTED = {"check_run", "pull_request", "pull_request_review"}

    def __init__(
        self,
        observations: SCMObservationService,
        delivery: DeliveryService,
        catalog: RepositoryCatalog,
        coordinator: ChangeSetSCMCoordinator,
        *,
        auto_merge: bool = False,
        on_observed: Callable[[ChangeSetView], Awaitable[None]] | None = None,
        rework_tasks: "CIReworkTaskGateway | None" = None,
    ) -> None:
        self._observations = observations
        self._delivery = delivery
        self._catalog = catalog
        self._coordinator = coordinator
        self._auto_merge = auto_merge
        # Notified with the resulting ChangeSet view after each processed
        # observation (e.g. to re-evaluate batch advancement after a merge).
        self._on_observed = on_observed
        # Turns a failed candidate into a Worker repair task. Without it a
        # CI failure only ever changes state and waits for a human to notice.
        self._rework_tasks = rework_tasks

    async def process(self, observation_id: UUID) -> ProcessedSCMObservation:
        claimed = await self._observations.claim(observation_id)
        if claimed is None:
            return ProcessedSCMObservation(ignored=False, claimed=False)
        try:
            if claimed.provider != "github" or claimed.event_type not in self._SUPPORTED:
                await self._observations.complete(observation_id)
                return ProcessedSCMObservation(ignored=True, claimed=True)
            if claimed.event_type == "check_run":
                parsed = parse_github_check_run(claimed.payload)
            elif claimed.event_type == "pull_request":
                parsed = parse_github_pull_request(claimed.payload)
            else:
                parsed = parse_github_pull_request_review(claimed.payload)
            if parsed is None:
                await self._observations.complete(observation_id)
                return ProcessedSCMObservation(ignored=True, claimed=True)
            change_set_id, repository_id = await self._route(
                claimed.change_set_id,
                claimed.repository_id,
                parsed.repository,
                parsed.head_sha,
            )
            if claimed.event_type == "check_run":
                result = await self._coordinator.record_github_ci(
                    change_set_id, repository_id, parsed
                )
                await self._request_rework(result, repository_id)
            elif claimed.event_type == "pull_request":
                current = await self._delivery.get(change_set_id)
                candidate = next(
                    item for item in current.repositories if item.repository_id == repository_id
                )
                if parsed.head_sha != candidate.commit_sha:
                    raise SCMConflict("remote PR head drifted from the ChangeSet candidate")
                if parsed.state is PullRequestState.MERGED:
                    if not parsed.merge_sha:
                        raise SCMConflict("merged pull request has no merge commit SHA")
                    result = await self._delivery.observe_merge(
                        MergeObservationCommand(change_set_id, repository_id, parsed.merge_sha)
                    )
                elif parsed.state is PullRequestState.OPEN:
                    result = await self._delivery.observe_pull_request(
                        PullRequestObservationCommand(
                            change_set_id,
                            repository_id,
                            parsed.number,
                            parsed.url,
                            parsed.head_sha,
                        )
                    )
                else:
                    raise SCMConflict("delivery pull request was closed without merge")
            else:
                result = await self._coordinator.record_github_review(
                    change_set_id, repository_id, parsed
                )
            if self._auto_merge and self._coordinator.can_mutate:
                # Draft PRs wait for their upstream dependencies to merge;
                # undraft first, then merge whatever became eligible. The
                # replay worker's 15s poll re-drives this until commands land.
                await self._coordinator.undraft_when_allowed(change_set_id)
                result = await self._coordinator.merge_ready_repositories(change_set_id)
            if self._on_observed is not None and result is not None:
                await self._on_observed(result)
            await self._observations.complete(observation_id)
            return ProcessedSCMObservation(
                ignored=False,
                claimed=True,
                change_set=result,
            )
        except Exception as error:
            await self._observations.fail(observation_id, f"{type(error).__name__}: {error}")
            raise

    async def _request_rework(self, result: ChangeSetView | None, repository_id: UUID) -> None:
        """Ask the repository's Worker to repair a candidate its CI just failed.

        Creation is keyed on the failed head SHA, so every replay of the same
        failure reuses one task instead of flooding the Worker, and a later
        candidate gets its own.

        Best effort on purpose: the CI fact is already durably recorded by the
        time we get here, and losing that projection because AgentTeams is down
        or a repository has no Worker would be the worse failure. The problem is
        logged rather than raised.
        """

        if self._rework_tasks is None or result is None:
            return
        candidate = next(
            (item for item in result.repositories if item.repository_id == repository_id),
            None,
        )
        if candidate is None or candidate.status is not RepositoryDeliveryStatus.CI_FAILED:
            return
        try:
            task_id = await self._rework_tasks.create_for_failed_candidate(result, repository_id)
        except Exception:
            logger.exception(
                "could not create a CI rework task",
                extra={
                    "change_set_id": str(result.id),
                    "repository_id": str(repository_id),
                },
            )
            return
        logger.info(
            "CI rework task created",
            extra={"task_id": str(task_id), "repository_id": str(repository_id)},
        )

    async def _route(
        self,
        change_set_id: UUID | None,
        repository_id: UUID | None,
        repository,
        head_sha: str,
    ) -> tuple[UUID, UUID]:
        if change_set_id is not None and repository_id is not None:
            return change_set_id, repository_id
        matches = []
        for profile in await self._catalog.list():
            try:
                candidate = parse_repository_ref(profile.url)
            except ValueError:
                continue
            if candidate == repository:
                matches.append(profile)
        if not matches:
            raise DeliveryNotFound("GitHub repository is not registered")
        if len(matches) > 1:
            raise DeliveryConflict("GitHub repository identity is ambiguous")
        routed, routed_repository_id = await self._delivery.resolve_candidate(
            matches[0].id, head_sha
        )
        return routed.id, routed_repository_id


class SCMObservationReplayWorker:
    def __init__(
        self,
        observations: SCMObservationService,
        processor: GitHubObservationProcessor,
        *,
        interval_seconds: float = 15,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self._observations = observations
        self._processor = processor
        self._interval_seconds = interval_seconds
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="scm-observation-replay")

    async def close(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        await self._task
        self._task = None

    async def run_once(self) -> None:
        for observation in await self._observations.list_replayable():
            try:
                await self._processor.process(observation.id)
            except Exception:
                logger.exception(
                    "SCM observation replay failed",
                    extra={"observation_id": str(observation.id)},
                )

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception:
                logger.exception("SCM observation replay scan failed")
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_seconds)
