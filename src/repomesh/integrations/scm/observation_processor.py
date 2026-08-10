import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
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
    ) -> None:
        self._observations = observations
        self._delivery = delivery
        self._catalog = catalog
        self._coordinator = coordinator
        self._auto_merge = auto_merge

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
                result = await self._coordinator.merge_ready_repositories(change_set_id)
            await self._observations.complete(observation_id)
            return ProcessedSCMObservation(
                ignored=False,
                claimed=True,
                change_set=result,
            )
        except Exception as error:
            await self._observations.fail(observation_id, f"{type(error).__name__}: {error}")
            raise

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
