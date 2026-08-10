from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from repomesh.modules.delivery import DeliveryNotFound, DeliveryService
from repomesh.modules.delivery.contracts import (
    ChangeSetView,
    CIObservationCommand,
    MergeObservationCommand,
    PullRequestObservationCommand,
    RecordMergeRequestedCommand,
    RepositoryDeliveryStatus,
    ReviewObservationCommand,
    ReviewState,
)
from repomesh.modules.repository_intelligence.ports import RepositoryCatalog

from .contracts import (
    BranchPublisher,
    CreateDraftPullRequestCommand,
    MergePullRequestCommand,
    PublishBranchCommand,
    PullRequestObservation,
    PullRequestState,
    RepositoryRef,
    SCMAdapter,
    SCMConflict,
    SCMProvider,
    SCMReviewState,
)
from .github_events import (
    GitHubCIObservation,
    GitHubReviewObservation,
    validate_ci_observation,
)


@dataclass(frozen=True, slots=True)
class OpenChangeSetPullRequestCommand:
    change_set_id: UUID
    repository_id: UUID
    base_branch: str
    body: str
    draft: bool = False


@dataclass(frozen=True, slots=True)
class PublishChangeSetPullRequestCommand:
    change_set_id: UUID
    repository_id: UUID
    workspace: Path
    base_branch: str
    body: str
    expected_remote_sha: str | None = None
    draft: bool = False


class ChangeSetSCMCoordinator:
    """Connects frozen ChangeSet candidates to an SCM adapter."""

    def __init__(
        self,
        delivery: DeliveryService,
        catalog: RepositoryCatalog,
        adapter: SCMAdapter | None,
        branch_publisher: BranchPublisher | None = None,
    ) -> None:
        self._delivery = delivery
        self._catalog = catalog
        self._adapter = adapter
        self._branch_publisher = branch_publisher

    @property
    def can_mutate(self) -> bool:
        return self._adapter is not None

    async def publish_and_open_draft_pull_request(
        self, command: PublishChangeSetPullRequestCommand
    ) -> ChangeSetView:
        if self._branch_publisher is None:
            raise RuntimeError("branch publisher is not configured")
        change_set = await self._delivery.get(command.change_set_id)
        candidate = self._candidate(change_set, command.repository_id)
        profile = await self._catalog.get(command.repository_id)
        if profile is None:
            raise DeliveryNotFound(f"repository not in catalog: {command.repository_id}")
        await self._branch_publisher.publish(
            PublishBranchCommand(
                workspace=command.workspace,
                branch_name=candidate.branch_name,
                expected_head_sha=candidate.commit_sha,
                expected_remote_sha=command.expected_remote_sha,
                repository=parse_repository_ref(profile.url),
            )
        )
        return await self.open_draft_pull_request(
            OpenChangeSetPullRequestCommand(
                change_set_id=command.change_set_id,
                repository_id=command.repository_id,
                base_branch=command.base_branch,
                body=command.body,
                draft=command.draft,
            )
        )

    async def open_draft_pull_request(
        self, command: OpenChangeSetPullRequestCommand
    ) -> ChangeSetView:
        change_set = await self._delivery.get(command.change_set_id)
        candidate = self._candidate(change_set, command.repository_id)
        profile = await self._catalog.get(command.repository_id)
        if profile is None:
            raise DeliveryNotFound(f"repository not in catalog: {command.repository_id}")
        if self._adapter is None:
            raise RuntimeError("SCM adapter is not configured")
        observation = await self._adapter.create_draft_pull_request(
            CreateDraftPullRequestCommand(
                repository=parse_repository_ref(profile.url),
                head_branch=candidate.branch_name,
                base_branch=command.base_branch,
                expected_head_sha=candidate.commit_sha,
                title=change_set.title,
                body=command.body,
                idempotency_key=(
                    f"changeset:{change_set.id}:repository:{candidate.repository_id}"
                ),
                draft=command.draft,
            )
        )
        return await self._record_observation(
            change_set, candidate.repository_id, observation
        )

    async def reconcile_pull_request(
        self,
        change_set_id: UUID,
        repository_id: UUID,
        number: int,
    ) -> PullRequestObservation:
        profile = await self._catalog.get(repository_id)
        if profile is None:
            raise DeliveryNotFound(f"repository not in catalog: {repository_id}")
        if self._adapter is None:
            raise RuntimeError("SCM adapter is not configured")
        observation = await self._adapter.get_pull_request(
            parse_repository_ref(profile.url), number
        )
        change_set = await self._delivery.get(change_set_id)
        candidate = self._candidate(change_set, repository_id)
        if observation.head_sha != candidate.commit_sha.lower():
            raise ValueError("remote PR head SHA differs from the frozen ChangeSet candidate")
        return observation

    async def merge_when_allowed(
        self, change_set_id: UUID, repository_id: UUID
    ) -> ChangeSetView:
        if self._adapter is None:
            raise RuntimeError("SCM adapter is not configured")
        gate = await self._delivery.evaluate_merge_gate(change_set_id, repository_id)
        if not gate.allowed:
            raise ValueError(f"merge gate denied: {'; '.join(gate.reasons)}")
        change_set = await self._delivery.get(change_set_id)
        candidate = self._candidate(change_set, repository_id)
        if candidate.pull_request_number is None:
            raise ValueError("delivery candidate has no pull request")
        profile = await self._catalog.get(repository_id)
        if profile is None:
            raise DeliveryNotFound(f"repository not in catalog: {repository_id}")
        observation = await self.reconcile_pull_request(
            change_set_id, repository_id, candidate.pull_request_number
        )
        if observation.state.value != "open":
            raise ValueError("pull request is no longer open")
        if observation.draft:
            raise ValueError("draft pull request cannot merge")
        if observation.mergeable is False:
            raise ValueError("pull request has conflicts")
        await self._adapter.merge_pull_request(
            MergePullRequestCommand(
                repository=parse_repository_ref(profile.url),
                number=candidate.pull_request_number,
                expected_head_sha=candidate.commit_sha,
                commit_title=change_set.title,
            )
        )
        return await self._delivery.record_merge_requested(
            RecordMergeRequestedCommand(
                change_set_id,
                repository_id,
                candidate.commit_sha,
            )
        )

    async def record_github_ci(
        self,
        change_set_id: UUID,
        repository_id: UUID,
        observation: GitHubCIObservation,
    ) -> ChangeSetView:
        if not observation.terminal:
            return await self._delivery.get(change_set_id)
        change_set = await self._delivery.get(change_set_id)
        candidate = self._candidate(change_set, repository_id)
        profile = await self._catalog.get(repository_id)
        if profile is None:
            raise DeliveryNotFound(f"repository not in catalog: {repository_id}")
        validate_ci_observation(
            observation,
            expected_repository=parse_repository_ref(profile.url),
            expected_head_sha=candidate.commit_sha,
        )
        return await self._delivery.observe_ci(
            CIObservationCommand(
                change_set_id=change_set_id,
                repository_id=repository_id,
                passed=observation.passed,
                check_run_id=observation.check_run_id,
                summary=observation.summary,
                check_name=observation.check_name,
            )
        )

    async def record_github_review(
        self,
        change_set_id: UUID,
        repository_id: UUID,
        observation: GitHubReviewObservation,
    ) -> ChangeSetView:
        change_set = await self._delivery.get(change_set_id)
        candidate = self._candidate(change_set, repository_id)
        profile = await self._catalog.get(repository_id)
        if profile is None:
            raise DeliveryNotFound(f"repository not in catalog: {repository_id}")
        if observation.repository != parse_repository_ref(profile.url):
            raise ValueError("review event belongs to another repository")
        if observation.head_sha != candidate.commit_sha.lower():
            raise ValueError("review event head SHA differs from the frozen candidate")
        return await self._delivery.observe_review(
            ReviewObservationCommand(
                change_set_id=change_set_id,
                repository_id=repository_id,
                review_id=observation.review_id,
                reviewer=observation.reviewer,
                state=observation.state,
                head_sha=observation.head_sha,
                summary=observation.summary,
            )
        )

    async def merge_ready_repositories(self, change_set_id: UUID) -> ChangeSetView:
        """Merge every currently eligible candidate in dependency order."""

        current = await self._delivery.get(change_set_id)
        for candidate in sorted(current.repositories, key=lambda item: item.merge_order):
            gate = await self._delivery.evaluate_merge_gate(
                change_set_id, candidate.repository_id
            )
            if gate.allowed:
                current = await self.merge_when_allowed(
                    change_set_id, candidate.repository_id
                )
        return current

    async def reconcile_and_merge(self, change_set_id: UUID) -> ChangeSetView:
        """Recover remote SCM facts, then merge eligible repositories in order."""

        if self._adapter is None:
            raise RuntimeError("SCM adapter is not configured")
        current = await self._delivery.get(change_set_id)
        for original in sorted(current.repositories, key=lambda item: item.merge_order):
            current = await self._delivery.get(change_set_id)
            candidate = self._candidate(current, original.repository_id)
            if (
                candidate.status is RepositoryDeliveryStatus.MERGED
                or candidate.pull_request_number is None
            ):
                continue
            repository = await self._repository_ref(candidate.repository_id)
            pull_request = await self._adapter.get_pull_request(
                repository, candidate.pull_request_number
            )
            if pull_request.head_sha != candidate.commit_sha.lower():
                raise SCMConflict(
                    "remote PR head SHA differs from the frozen ChangeSet candidate"
                )

            for check in await self._adapter.list_check_runs(
                repository, candidate.commit_sha
            ):
                if not check.terminal or check.head_sha != candidate.commit_sha.lower():
                    continue
                current = await self._delivery.observe_ci(
                    CIObservationCommand(
                        change_set_id=change_set_id,
                        repository_id=candidate.repository_id,
                        passed=check.passed,
                        check_run_id=check.check_run_id,
                        summary=check.summary,
                        check_name=check.check_name,
                    )
                )

            review_states = {
                SCMReviewState.APPROVED: ReviewState.APPROVED,
                SCMReviewState.CHANGES_REQUESTED: ReviewState.CHANGES_REQUESTED,
                SCMReviewState.DISMISSED: ReviewState.DISMISSED,
            }
            latest_reviews = {
                review.reviewer: review
                for review in await self._adapter.list_pull_request_reviews(
                    repository, candidate.pull_request_number
                )
                if review.head_sha == candidate.commit_sha.lower()
            }
            for review in latest_reviews.values():
                current = await self._delivery.observe_review(
                    ReviewObservationCommand(
                        change_set_id=change_set_id,
                        repository_id=candidate.repository_id,
                        review_id=review.review_id,
                        reviewer=review.reviewer,
                        state=review_states[review.state],
                        head_sha=review.head_sha,
                        summary=review.summary,
                    )
                )

            candidate = self._candidate(current, candidate.repository_id)
            if pull_request.state is PullRequestState.MERGED:
                if not pull_request.merge_sha:
                    raise SCMConflict("merged pull request has no merge commit SHA")
                gate = await self._delivery.evaluate_merge_gate(
                    change_set_id, candidate.repository_id
                )
                if (
                    candidate.status is not RepositoryDeliveryStatus.MERGE_REQUESTED
                    and not gate.allowed
                ):
                    raise SCMConflict(
                        "remote pull request merged while RepoMesh merge gate was closed"
                    )
                current = await self._delivery.observe_merge(
                    MergeObservationCommand(
                        change_set_id,
                        candidate.repository_id,
                        pull_request.merge_sha,
                    )
                )
                continue
            if candidate.status is RepositoryDeliveryStatus.MERGE_REQUESTED:
                continue
            if (
                pull_request.state is not PullRequestState.OPEN
                or pull_request.draft
                or pull_request.mergeable is False
            ):
                continue
            gate = await self._delivery.evaluate_merge_gate(
                change_set_id, candidate.repository_id
            )
            if gate.allowed:
                current = await self.merge_when_allowed(
                    change_set_id, candidate.repository_id
                )
        return current

    async def _repository_ref(self, repository_id: UUID) -> RepositoryRef:
        profile = await self._catalog.get(repository_id)
        if profile is None:
            raise DeliveryNotFound(f"repository not in catalog: {repository_id}")
        return parse_repository_ref(profile.url)

    @staticmethod
    def _candidate(change_set: ChangeSetView, repository_id: UUID):
        candidate = next(
            (item for item in change_set.repositories if item.repository_id == repository_id),
            None,
        )
        if candidate is None:
            raise DeliveryNotFound(f"repository not in ChangeSet: {repository_id}")
        return candidate

    async def _record_observation(
        self,
        change_set: ChangeSetView,
        repository_id: UUID,
        observation: PullRequestObservation,
    ) -> ChangeSetView:
        return await self._delivery.observe_pull_request(
            PullRequestObservationCommand(
                change_set_id=change_set.id,
                repository_id=repository_id,
                pull_request_number=observation.number,
                pull_request_url=observation.url,
                head_sha=observation.head_sha,
            )
        )


def parse_repository_ref(url: str) -> RepositoryRef:
    parsed = urlparse(url.strip())
    if (parsed.hostname or "").lower() != "github.com":
        raise ValueError("only github.com repository URLs are supported")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) != 2:
        raise ValueError("repository URL must identify one owner and repository")
    owner, name = parts
    if name.endswith(".git"):
        name = name[:-4]
    if not owner or not name:
        raise ValueError("repository owner and name are required")
    return RepositoryRef(SCMProvider.GITHUB, owner, name)
