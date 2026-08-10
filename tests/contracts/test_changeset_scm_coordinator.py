from pathlib import Path
from uuid import uuid4

import pytest

from repomesh.integrations.scm.contracts import (
    CreateDraftPullRequestCommand,
    PublishBranchCommand,
    PublishedBranch,
    PullRequestObservation,
    PullRequestState,
    RepositoryRef,
    SCMProvider,
)
from repomesh.integrations.scm.delivery import (
    ChangeSetSCMCoordinator,
    OpenChangeSetPullRequestCommand,
    PublishChangeSetPullRequestCommand,
    parse_repository_ref,
)
from repomesh.modules.delivery import DeliveryService, InMemoryChangeSetStore
from repomesh.modules.delivery.contracts import (
    PrepareChangeSetCommand,
    RepositoryCandidateInput,
    RepositoryDeliveryStatus,
)
from repomesh.modules.repository_intelligence.domain import RepositoryProfile
from repomesh.modules.repository_intelligence.infrastructure import InMemoryRepositoryCatalog


class RecordingAdapter:
    def __init__(self) -> None:
        self.commands: list[CreateDraftPullRequestCommand] = []

    async def create_draft_pull_request(
        self, command: CreateDraftPullRequestCommand
    ) -> PullRequestObservation:
        self.commands.append(command)
        return PullRequestObservation(
            provider=SCMProvider.GITHUB,
            repository=command.repository,
            number=17,
            url="https://github.com/acme/pricing/pull/17",
            state=PullRequestState.OPEN,
            draft=True,
            head_branch=command.head_branch,
            head_sha=command.expected_head_sha,
            base_branch=command.base_branch,
            base_sha="b" * 40,
            mergeable=None,
        )

    async def get_pull_request(
        self, repository: RepositoryRef, number: int
    ) -> PullRequestObservation:
        raise NotImplementedError

    async def close_pull_request(
        self, repository: RepositoryRef, number: int, *, idempotency_key: str
    ) -> PullRequestObservation:
        raise NotImplementedError


class RecordingPublisher:
    def __init__(self) -> None:
        self.commands: list[PublishBranchCommand] = []

    async def publish(self, command: PublishBranchCommand) -> PublishedBranch:
        self.commands.append(command)
        return PublishedBranch(
            command.branch_name,
            command.expected_head_sha,
            command.expected_head_sha,
        )

def test_repository_url_parser_accepts_clone_url() -> None:
    assert parse_repository_ref("https://github.com/acme/pricing.git") == RepositoryRef(
        SCMProvider.GITHUB, "acme", "pricing"
    )


@pytest.mark.asyncio
async def test_changeset_candidate_opens_draft_pr_and_records_it() -> None:
    repository_id = uuid4()
    catalog = InMemoryRepositoryCatalog()
    await catalog.add(
        RepositoryProfile(
            id=repository_id,
            name="pricing",
            url="https://github.com/acme/pricing",
        )
    )
    delivery = DeliveryService(InMemoryChangeSetStore())
    change_set = await delivery.prepare(
        PrepareChangeSetCommand(
            organization_id=uuid4(),
            project_id=uuid4(),
            created_by_agent_id=uuid4(),
            title="Update pricing",
            validation_snapshot_id=uuid4(),
            candidates=(
                RepositoryCandidateInput(
                    repository_id=repository_id,
                    task_id=uuid4(),
                    commit_sha="a" * 40,
                    base_sha="b" * 40,
                    branch_name="repomesh/pricing",
                ),
            ),
        ),
        idempotency_key="changeset-pricing",
    )
    adapter = RecordingAdapter()
    coordinator = ChangeSetSCMCoordinator(delivery, catalog, adapter)

    updated = await coordinator.open_draft_pull_request(
        OpenChangeSetPullRequestCommand(
            change_set_id=change_set.id,
            repository_id=repository_id,
            base_branch="main",
            body="Validated ChangeSet candidate",
        )
    )

    assert len(adapter.commands) == 1
    assert adapter.commands[0].expected_head_sha == "a" * 40
    assert updated.repositories[0].pull_request_number == 17
    assert updated.repositories[0].status is RepositoryDeliveryStatus.PR_OPEN


@pytest.mark.asyncio
async def test_combined_delivery_publishes_before_opening_pr(tmp_path: Path) -> None:
    repository_id = uuid4()
    catalog = InMemoryRepositoryCatalog()
    await catalog.add(
        RepositoryProfile(
            id=repository_id,
            name="pricing",
            url="https://github.com/acme/pricing",
        )
    )
    delivery = DeliveryService(InMemoryChangeSetStore())
    change_set = await delivery.prepare(
        PrepareChangeSetCommand(
            organization_id=uuid4(),
            project_id=uuid4(),
            created_by_agent_id=uuid4(),
            title="Update pricing",
            validation_snapshot_id=uuid4(),
            candidates=(
                RepositoryCandidateInput(
                    repository_id=repository_id,
                    task_id=uuid4(),
                    commit_sha="a" * 40,
                    base_sha="b" * 40,
                    branch_name="repomesh/pricing",
                ),
            ),
        ),
        idempotency_key="combined-delivery",
    )
    adapter = RecordingAdapter()
    publisher = RecordingPublisher()
    coordinator = ChangeSetSCMCoordinator(delivery, catalog, adapter, publisher)

    result = await coordinator.publish_and_open_draft_pull_request(
        PublishChangeSetPullRequestCommand(
            change_set_id=change_set.id,
            repository_id=repository_id,
            workspace=tmp_path,
            base_branch="main",
            body="Delivery evidence",
        )
    )

    assert publisher.commands[0].branch_name == "repomesh/pricing"
    assert adapter.commands[0].head_branch == "repomesh/pricing"
    assert result.repositories[0].status is RepositoryDeliveryStatus.PR_OPEN
