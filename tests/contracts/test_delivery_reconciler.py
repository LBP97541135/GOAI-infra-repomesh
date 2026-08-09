from uuid import uuid4

import pytest

from repomesh.integrations.scm.contracts import (
    CheckRunObservation,
    CreateDraftPullRequestCommand,
    MergePullRequestCommand,
    MergePullRequestResult,
    PullRequestObservation,
    PullRequestReviewObservation,
    PullRequestState,
    RepositoryRef,
    SCMConflict,
    SCMProvider,
    SCMReviewState,
)
from repomesh.integrations.scm.delivery import ChangeSetSCMCoordinator
from repomesh.integrations.scm.reconciler import DeliveryReconciler
from repomesh.modules.delivery import DeliveryService, InMemoryChangeSetStore
from repomesh.modules.delivery.contracts import (
    ChangeSetStatus,
    PrepareChangeSetCommand,
    PullRequestObservationCommand,
    RepositoryCandidateInput,
)
from repomesh.modules.repository_intelligence.domain import RepositoryProfile
from repomesh.modules.repository_intelligence.infrastructure import (
    InMemoryRepositoryCatalog,
)


class SnapshotAdapter:
    def __init__(self, head_sha: str = "a" * 40) -> None:
        self.head_sha = head_sha
        self.merge_calls = 0

    async def create_draft_pull_request(
        self, command: CreateDraftPullRequestCommand
    ) -> PullRequestObservation:
        raise NotImplementedError

    async def get_pull_request(
        self, repository: RepositoryRef, number: int
    ) -> PullRequestObservation:
        return PullRequestObservation(
            provider=SCMProvider.GITHUB,
            repository=repository,
            number=number,
            url=f"https://github.com/acme/pricing/pull/{number}",
            state=PullRequestState.OPEN,
            draft=False,
            head_branch="repomesh/pricing",
            head_sha=self.head_sha,
            base_branch="main",
            base_sha="b" * 40,
            mergeable=True,
        )

    async def list_check_runs(
        self, repository: RepositoryRef, head_sha: str
    ) -> tuple[CheckRunObservation, ...]:
        return (
            CheckRunObservation("501", "unit-tests", head_sha, True, True, "passed"),
        )

    async def list_pull_request_reviews(
        self, repository: RepositoryRef, number: int
    ) -> tuple[PullRequestReviewObservation, ...]:
        return (
            PullRequestReviewObservation(
                "601", "reviewer-one", "a" * 40, SCMReviewState.APPROVED
            ),
        )

    async def close_pull_request(
        self, repository: RepositoryRef, number: int, *, idempotency_key: str
    ) -> PullRequestObservation:
        raise NotImplementedError

    async def merge_pull_request(
        self, command: MergePullRequestCommand
    ) -> MergePullRequestResult:
        self.merge_calls += 1
        return MergePullRequestResult(True, "d" * 40, "merged")


async def prepared_delivery():
    repository_id = uuid4()
    catalog = InMemoryRepositoryCatalog()
    await catalog.add(
        RepositoryProfile(
            id=repository_id,
            name="pricing",
            url="https://github.com/acme/pricing",
        )
    )
    store = InMemoryChangeSetStore()
    service = DeliveryService(store)
    change_set = await service.prepare(
        PrepareChangeSetCommand(
            organization_id=uuid4(),
            project_id=uuid4(),
            created_by_agent_id=uuid4(),
            title="Recover delivery",
            validation_snapshot_id=uuid4(),
            candidates=(
                RepositoryCandidateInput(
                    repository_id=repository_id,
                    task_id=uuid4(),
                    commit_sha="a" * 40,
                    base_sha="b" * 40,
                    branch_name="repomesh/pricing",
                    required_checks=("unit-tests",),
                    required_approvals=1,
                ),
            ),
        ),
        idempotency_key="recover-delivery",
    )
    await service.observe_pull_request(
        PullRequestObservationCommand(
            change_set.id,
            repository_id,
            42,
            "https://github.com/acme/pricing/pull/42",
            "a" * 40,
        )
    )
    return store, catalog, change_set.id


@pytest.mark.asyncio
async def test_reconciler_recovers_missed_webhooks_after_service_restart() -> None:
    store, catalog, change_set_id = await prepared_delivery()
    restarted_service = DeliveryService(store)
    adapter = SnapshotAdapter()
    coordinator = ChangeSetSCMCoordinator(restarted_service, catalog, adapter)
    reconciler = DeliveryReconciler(restarted_service, coordinator)

    await reconciler.run_once()
    await reconciler.run_once()

    recovered = await restarted_service.get(change_set_id)
    assert recovered.status is ChangeSetStatus.DELIVERED
    assert recovered.repositories[0].merge_sha == "d" * 40
    assert adapter.merge_calls == 1


@pytest.mark.asyncio
async def test_reconciliation_rejects_remote_head_drift() -> None:
    store, catalog, change_set_id = await prepared_delivery()
    coordinator = ChangeSetSCMCoordinator(
        DeliveryService(store), catalog, SnapshotAdapter(head_sha="c" * 40)
    )

    with pytest.raises(SCMConflict, match="head SHA"):
        await coordinator.reconcile_and_merge(change_set_id)
