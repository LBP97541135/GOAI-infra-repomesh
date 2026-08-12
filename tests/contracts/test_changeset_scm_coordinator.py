from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest

from repomesh.integrations.scm.contracts import (
    CreateDraftPullRequestCommand,
    PublishBranchCommand,
    PublishedBranch,
    PullRequestObservation,
    PullRequestState,
    RepositoryRef,
    SCMConflict,
    SCMProvider,
)
from repomesh.integrations.scm.delivery import (
    ChangeSetSCMCoordinator,
    OpenChangeSetPullRequestCommand,
    PublishChangeSetPullRequestCommand,
    parse_repository_ref,
)
from repomesh.integrations.scm.github import GitHubAdapter
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


def pull_payload() -> dict:
    return {
        "number": 42,
        "html_url": "https://github.com/acme/pricing/pull/42",
        "state": "open",
        "draft": True,
        "merged_at": None,
        "head": {"ref": "repomesh/pricing", "sha": "a" * 40},
        "base": {"ref": "main", "sha": "b" * 40},
        "mergeable": None,
    }


async def gated_change_set() -> tuple[DeliveryService, InMemoryRepositoryCatalog, object, UUID]:
    """A candidate that demands CI and an approval, as the delivery policy does."""

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
                    required_checks=("ci",),
                    required_approvals=1,
                ),
            ),
        ),
        idempotency_key="gated-changeset",
    )
    return delivery, catalog, change_set, repository_id


@pytest.mark.asyncio
async def test_unprotected_base_branch_does_not_strand_the_publish() -> None:
    """The live blocker: GitHub 404s protection for an unprotected branch.

    The candidate branch is already pushed by then, so a refusal here leaves
    the ChangeSet at ``ready`` with no PR and nothing to retry against.
    """

    delivery, catalog, change_set, repository_id = await gated_change_set()
    paths: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append((request.method, request.url.path))
        if request.url.path.endswith("/protection"):
            return httpx.Response(404, json={"message": "Branch not protected"})
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(201, json=pull_payload())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = GitHubAdapter(lambda repo: "installation-token", client=client)
    coordinator = ChangeSetSCMCoordinator(delivery, catalog, adapter)

    updated = await coordinator.open_draft_pull_request(
        OpenChangeSetPullRequestCommand(
            change_set_id=change_set.id,
            repository_id=repository_id,
            base_branch="main",
            body="Validated ChangeSet candidate",
        )
    )

    assert ("POST", "/repos/acme/pricing/pulls") in paths
    assert updated.repositories[0].pull_request_number == 42
    assert updated.repositories[0].status is RepositoryDeliveryStatus.PR_OPEN
    # The gate did not move: RepoMesh still owes itself a passing check and an
    # approval before this candidate can merge.
    assert updated.repositories[0].required_checks == ("ci",)
    assert updated.repositories[0].required_approvals == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_protection_weaker_than_the_candidate_is_still_a_refusal() -> None:
    """ "Unprotected" is forgiven; "protected, but not enough" is not."""

    delivery, catalog, change_set, repository_id = await gated_change_set()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/protection"):
            return httpx.Response(
                200,
                json={
                    "required_status_checks": {"checks": [], "contexts": []},
                    "required_pull_request_reviews": {
                        "required_approving_review_count": 1,
                        "dismiss_stale_reviews": True,
                    },
                },
            )
        raise AssertionError("preflight must refuse before touching /pulls")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = GitHubAdapter(lambda repo: "installation-token", client=client)
    coordinator = ChangeSetSCMCoordinator(delivery, catalog, adapter)

    with pytest.raises(SCMConflict, match="missing required checks"):
        await coordinator.open_draft_pull_request(
            OpenChangeSetPullRequestCommand(
                change_set_id=change_set.id,
                repository_id=repository_id,
                base_branch="main",
                body="Validated ChangeSet candidate",
            )
        )
    await client.aclose()
