from uuid import uuid4

import pytest

from repomesh.integrations.scm.contracts import (
    CheckRunObservation,
    PullRequestObservation,
    PullRequestReviewObservation,
    PullRequestState,
    SCMProvider,
    SCMRateLimited,
    SCMReviewState,
)
from repomesh.integrations.scm.delivery import ChangeSetSCMCoordinator
from repomesh.integrations.scm.observation_processor import GitHubObservationProcessor
from repomesh.integrations.scm.poller import GitHubObservationPoller
from repomesh.modules.delivery import (
    DeliveryService,
    InMemoryChangeSetStore,
    InMemorySCMObservationStore,
    InMemorySCMPollCursorStore,
    SCMObservationService,
    SCMPollCursorService,
)
from repomesh.modules.delivery.contracts import (
    PrepareChangeSetCommand,
    PullRequestObservationCommand,
    RepositoryCandidateInput,
)
from repomesh.modules.repository_intelligence.domain import RepositoryProfile
from repomesh.modules.repository_intelligence.infrastructure import InMemoryRepositoryCatalog


class PollAdapter:
    def __init__(self, *, rate_limited: bool = False) -> None:
        self.rate_limited = rate_limited
        self.pr_calls = 0

    async def get_pull_request(self, repository, number):
        self.pr_calls += 1
        if self.rate_limited:
            raise SCMRateLimited("slow down", retry_after_seconds=120)
        return PullRequestObservation(
            SCMProvider.GITHUB,
            repository,
            number,
            f"https://github.com/acme/pricing/pull/{number}",
            PullRequestState.OPEN,
            False,
            "repomesh/pricing",
            "a" * 40,
            "main",
            "b" * 40,
            True,
        )

    async def list_check_runs(self, repository, head_sha):
        return (CheckRunObservation("501", "unit", head_sha, True, True, "passed"),)

    async def list_pull_request_reviews(self, repository, number):
        return (
            PullRequestReviewObservation(
                "601", "reviewer", "a" * 40, SCMReviewState.APPROVED, "approved"
            ),
        )


async def setup_poller(adapter: PollAdapter):
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
            uuid4(),
            uuid4(),
            uuid4(),
            "Poll delivery",
            uuid4(),
            (
                RepositoryCandidateInput(
                    repository_id,
                    uuid4(),
                    "a" * 40,
                    "b" * 40,
                    "repomesh/pricing",
                    required_checks=("unit",),
                    required_approvals=1,
                ),
            ),
        ),
        idempotency_key="poll-delivery",
    )
    await delivery.observe_pull_request(
        PullRequestObservationCommand(
            change_set.id,
            repository_id,
            42,
            "https://github.com/acme/pricing/pull/42",
            "a" * 40,
        )
    )
    observation_store = InMemorySCMObservationStore()
    observations = SCMObservationService(observation_store)
    cursor_store = InMemorySCMPollCursorStore()
    processor = GitHubObservationProcessor(
        observations,
        delivery,
        catalog,
        ChangeSetSCMCoordinator(delivery, catalog, None),
    )
    poller = GitHubObservationPoller(
        delivery,
        observations,
        SCMPollCursorService(cursor_store, interval_seconds=60),
        catalog,
        adapter,
        processor,
    )
    return poller, delivery, change_set.id, repository_id, observation_store, cursor_store


@pytest.mark.asyncio
async def test_poller_persists_and_projects_pr_ci_and_review_facts() -> None:
    adapter = PollAdapter()
    poller, delivery, change_set_id, _, observations, _ = await setup_poller(adapter)

    await poller.run_once()

    result = await delivery.get(change_set_id)
    assert result.repositories[0].status.value == "ready_to_merge"
    assert len(observations.items) == 3
    assert all(item.status.value == "processed" for item in observations.items.values())

    await poller.run_once()
    assert adapter.pr_calls == 1
    assert len(observations.items) == 3


@pytest.mark.asyncio
async def test_poller_persists_rate_limit_backoff() -> None:
    adapter = PollAdapter(rate_limited=True)
    poller, _, change_set_id, repository_id, _, cursors = await setup_poller(adapter)

    await poller.run_once()
    cursor = cursors.items[(change_set_id, repository_id)]

    assert cursor.consecutive_failures == 1
    assert cursor.last_error == "slow down"
    await poller.run_once()
    assert adapter.pr_calls == 1
