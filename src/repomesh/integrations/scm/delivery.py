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
)
from repomesh.modules.repository_intelligence.ports import RepositoryCatalog

from .contracts import (
    BranchPublisher,
    CreateDraftPullRequestCommand,
    MergePullRequestCommand,
    PublishBranchCommand,
    PullRequestObservation,
    RepositoryRef,
    SCMAdapter,
    SCMProvider,
)
from .github_events import GitHubCIObservation, validate_ci_observation


@dataclass(frozen=True, slots=True)
class OpenChangeSetPullRequestCommand:
    change_set_id: UUID
    repository_id: UUID
    base_branch: str
    body: str


@dataclass(frozen=True, slots=True)
class PublishChangeSetPullRequestCommand:
    change_set_id: UUID
    repository_id: UUID
    workspace: Path
    base_branch: str
    body: str
    expected_remote_sha: str | None = None


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

    async def publish_and_open_draft_pull_request(
        self, command: PublishChangeSetPullRequestCommand
    ) -> ChangeSetView:
        if self._branch_publisher is None:
            raise RuntimeError("branch publisher is not configured")
        change_set = await self._delivery.get(command.change_set_id)
        candidate = self._candidate(change_set, command.repository_id)
        await self._branch_publisher.publish(
            PublishBranchCommand(
                workspace=command.workspace,
                branch_name=candidate.branch_name,
                expected_head_sha=candidate.commit_sha,
                expected_remote_sha=command.expected_remote_sha,
            )
        )
        return await self.open_draft_pull_request(
            OpenChangeSetPullRequestCommand(
                change_set_id=command.change_set_id,
                repository_id=command.repository_id,
                base_branch=command.base_branch,
                body=command.body,
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
        result = await self._adapter.merge_pull_request(
            MergePullRequestCommand(
                repository=parse_repository_ref(profile.url),
                number=candidate.pull_request_number,
                expected_head_sha=candidate.commit_sha,
                commit_title=change_set.title,
            )
        )
        return await self._delivery.observe_merge(
            MergeObservationCommand(change_set_id, repository_id, result.merge_sha)
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
