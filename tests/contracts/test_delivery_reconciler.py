import logging
from uuid import UUID, uuid4

import pytest

from repomesh.integrations.scm.contracts import (
    BranchProtectionObservation,
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
    RepositoryDeliveryStatus,
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
        merged = self.merge_calls > 0
        return PullRequestObservation(
            provider=SCMProvider.GITHUB,
            repository=repository,
            number=number,
            url=f"https://github.com/acme/pricing/pull/{number}",
            state=PullRequestState.MERGED if merged else PullRequestState.OPEN,
            draft=False,
            head_branch="repomesh/pricing",
            head_sha=self.head_sha,
            base_branch="main",
            base_sha="b" * 40,
            mergeable=True,
            merge_sha="d" * 40 if merged else None,
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
    requested = await restarted_service.get(change_set_id)
    assert requested.repositories[0].status.value == "merge_requested"
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


class StrandedPublishAdapter:
    """The live convergence gap: the branch reached the remote, the PR never opened.

    ``remote_head`` is what ``get_branch_head`` reports -- the frozen commit
    for a genuinely stranded candidate, ``None`` for a branch that was never
    pushed, another SHA for a branch that drifted.
    """

    def __init__(self, *, remote_head: str | None = "a" * 40) -> None:
        self.remote_head = remote_head
        self.created: list[CreateDraftPullRequestCommand] = []
        self.branch_reads = 0
        self._by_number: dict[int, PullRequestObservation] = {}
        self._next_number = 77

    async def get_branch_head(self, repository: RepositoryRef, branch: str) -> str | None:
        self.branch_reads += 1
        return self.remote_head

    async def get_branch_protection(
        self, repository: RepositoryRef, branch: str
    ) -> BranchProtectionObservation:
        return BranchProtectionObservation.unprotected()

    async def create_draft_pull_request(
        self, command: CreateDraftPullRequestCommand
    ) -> PullRequestObservation:
        self.created.append(command)
        # GitHub allows one open PR per head branch, and GitHubAdapter looks
        # the branch up before it posts; this fake keeps the same promise.
        for observation in self._by_number.values():
            if observation.head_branch == command.head_branch:
                return observation
        observation = PullRequestObservation(
            provider=SCMProvider.GITHUB,
            repository=command.repository,
            number=self._next_number,
            url=f"https://github.com/acme/pricing/pull/{self._next_number}",
            state=PullRequestState.OPEN,
            draft=command.draft,
            head_branch=command.head_branch,
            head_sha=command.expected_head_sha.lower(),
            base_branch=command.base_branch,
            base_sha="b" * 40,
            mergeable=True,
        )
        self._by_number[self._next_number] = observation
        self._next_number += 1
        return observation

    async def get_pull_request(
        self, repository: RepositoryRef, number: int
    ) -> PullRequestObservation:
        existing = self._by_number.get(number)
        if existing is not None:
            return existing
        return PullRequestObservation(
            provider=SCMProvider.GITHUB,
            repository=repository,
            number=number,
            url=f"https://github.com/acme/pricing/pull/{number}",
            state=PullRequestState.OPEN,
            draft=True,
            head_branch="repomesh/a762abba/9dfa78f2",
            head_sha="a" * 40,
            base_branch="main",
            base_sha="b" * 40,
            mergeable=True,
        )

    async def list_check_runs(
        self, repository: RepositoryRef, head_sha: str
    ) -> tuple[CheckRunObservation, ...]:
        return ()

    async def list_pull_request_reviews(
        self, repository: RepositoryRef, number: int
    ) -> tuple[PullRequestReviewObservation, ...]:
        return ()

    async def merge_pull_request(self, command: MergePullRequestCommand) -> MergePullRequestResult:
        raise AssertionError("a draft pull request must never be merged")


async def stranded_delivery() -> tuple[
    InMemoryChangeSetStore, InMemoryRepositoryCatalog, UUID, UUID
]:
    """A ChangeSet whose only candidate has a branch and a commit but no PR."""

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
            title="Stranded delivery",
            validation_snapshot_id=uuid4(),
            candidates=(
                RepositoryCandidateInput(
                    repository_id=repository_id,
                    task_id=uuid4(),
                    commit_sha="a" * 40,
                    base_sha="b" * 40,
                    branch_name="repomesh/a762abba/9dfa78f2",
                    required_checks=("unit-tests",),
                    required_approvals=1,
                ),
            ),
        ),
        idempotency_key="stranded-delivery",
    )
    return store, catalog, change_set.id, repository_id


@pytest.mark.asyncio
async def test_sweep_opens_the_pull_request_a_failed_publish_left_behind() -> None:
    store, catalog, change_set_id, repository_id = await stranded_delivery()
    service = DeliveryService(store)
    adapter = StrandedPublishAdapter()
    reconciler = DeliveryReconciler(
        service, ChangeSetSCMCoordinator(service, catalog, adapter, base_branch="main")
    )

    await reconciler.run_once()

    candidate = (await service.get(change_set_id)).repositories[0]
    assert candidate.pull_request_number == 77
    assert candidate.pull_request_url.endswith("/pull/77")
    # Exactly where handle_batch would have left it: PR open, still draft.
    assert candidate.status is RepositoryDeliveryStatus.PR_OPEN
    assert len(adapter.created) == 1
    command = adapter.created[0]
    assert command.draft is True
    assert command.head_branch == "repomesh/a762abba/9dfa78f2"
    assert command.base_branch == "main"
    assert command.expected_head_sha == "a" * 40
    # Honest minimal body: only facts, no plan narrative it could not verify.
    assert str(change_set_id) in command.body
    assert str(repository_id) in command.body
    assert "a" * 40 in command.body
    assert "completed by reconciliation" in command.body.lower()


@pytest.mark.asyncio
async def test_sweep_leaves_a_candidate_that_already_has_a_pull_request_alone() -> None:
    store, catalog, change_set_id, repository_id = await stranded_delivery()
    service = DeliveryService(store)
    await service.observe_pull_request(
        PullRequestObservationCommand(
            change_set_id,
            repository_id,
            42,
            "https://github.com/acme/pricing/pull/42",
            "a" * 40,
        )
    )
    adapter = StrandedPublishAdapter()
    reconciler = DeliveryReconciler(service, ChangeSetSCMCoordinator(service, catalog, adapter))

    await reconciler.run_once()

    assert adapter.created == []
    assert adapter.branch_reads == 0
    assert (await service.get(change_set_id)).repositories[0].pull_request_number == 42


@pytest.mark.asyncio
async def test_sweep_skips_a_candidate_whose_remote_branch_is_missing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store, catalog, change_set_id, _ = await stranded_delivery()
    service = DeliveryService(store)
    adapter = StrandedPublishAdapter(remote_head=None)
    reconciler = DeliveryReconciler(service, ChangeSetSCMCoordinator(service, catalog, adapter))

    with caplog.at_level(logging.INFO):
        await reconciler.run_once()

    assert adapter.created == []
    candidate = (await service.get(change_set_id)).repositories[0]
    assert candidate.pull_request_number is None
    assert candidate.status is RepositoryDeliveryStatus.PENDING
    assert any("not on the remote" in record.getMessage() for record in caplog.records)
    # The sweep survived it -- nothing reached the reconciler's error shield.
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


@pytest.mark.asyncio
async def test_sweep_skips_a_candidate_whose_remote_branch_drifted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store, catalog, change_set_id, _ = await stranded_delivery()
    service = DeliveryService(store)
    adapter = StrandedPublishAdapter(remote_head="c" * 40)
    reconciler = DeliveryReconciler(service, ChangeSetSCMCoordinator(service, catalog, adapter))

    with caplog.at_level(logging.INFO):
        await reconciler.run_once()

    assert adapter.created == []
    assert (await service.get(change_set_id)).repositories[0].pull_request_number is None
    assert any(
        "differs from the frozen commit" in record.getMessage() for record in caplog.records
    )


@pytest.mark.asyncio
async def test_a_second_sweep_does_not_open_a_second_pull_request() -> None:
    store, catalog, change_set_id, _ = await stranded_delivery()
    service = DeliveryService(store)
    adapter = StrandedPublishAdapter()
    reconciler = DeliveryReconciler(service, ChangeSetSCMCoordinator(service, catalog, adapter))

    await reconciler.run_once()
    await reconciler.run_once()
    await reconciler.run_once()

    assert len(adapter.created) == 1
    assert adapter.branch_reads == 1
    assert (await service.get(change_set_id)).repositories[0].pull_request_number == 77
