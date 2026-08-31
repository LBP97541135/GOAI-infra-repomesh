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
from repomesh.modules.delivery import (
    DeliveryService,
    InMemoryChangeSetStore,
    SCMCommandService,
)
from repomesh.modules.delivery.contracts import (
    ChangeSetStatus,
    PrepareChangeSetCommand,
    PullRequestObservationCommand,
    RepositoryCandidateInput,
    RepositoryDeliveryStatus,
    SCMCommandKind,
)
from repomesh.modules.delivery.infrastructure import InMemorySCMCommandStore
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
    plan_id = uuid4()
    run_id = uuid4()
    worker_agent_id = uuid4()
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
                    plan_id=plan_id,
                    run_id=run_id,
                    worker_agent_id=worker_agent_id,
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
    plan_id = uuid4()
    run_id = uuid4()
    worker_agent_id = uuid4()
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
                    plan_id=plan_id,
                    run_id=run_id,
                    worker_agent_id=worker_agent_id,
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
    # The owning plan-delivery application persists the complete chain on the
    # candidate, so reconciliation can render it without querying another module.
    change_set = await service.get(change_set_id)
    assert f"- issue: `{change_set.project_id}`" in command.body
    assert f"- change_set: `{change_set_id}`" in command.body
    assert f"- repository: `{repository_id}`" in command.body
    assert f"- task: `{change_set.repositories[0].task_id}`" in command.body
    assert "- branch: `repomesh/a762abba/9dfa78f2`" in command.body
    assert f"- commit: `{'a' * 40}`" in command.body
    assert f"- plan: `{change_set.repositories[0].plan_id}`" in command.body
    assert f"- run: `{change_set.repositories[0].run_id}`" in command.body
    assert f"- worker_agent: `{change_set.repositories[0].worker_agent_id}`" in command.body
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


class DraftPullRequestAdapter:
    """Open draft PRs with green CI and no reviews -- the live post-publish shape.

    ``ready_for_review`` mirrors ``GitHubAdapter.ready_for_review``: it reads
    the PR first and only promotes one that is still draft, so ``ready_calls``
    counts attempts while ``promotions`` counts what actually reached the
    remote.
    """

    def __init__(self, *, drafts: dict[int, bool] | None = None) -> None:
        self.draft_by_number = dict(drafts if drafts is not None else {3: True})
        self.ready_calls: list[str] = []
        self.promotions: list[int] = []

    async def get_pull_request(
        self, repository: RepositoryRef, number: int
    ) -> PullRequestObservation:
        return PullRequestObservation(
            provider=SCMProvider.GITHUB,
            repository=repository,
            number=number,
            url=f"https://github.com/acme/pricing/pull/{number}",
            state=PullRequestState.OPEN,
            draft=self.draft_by_number.get(number, False),
            head_branch="repomesh/a762abba/9dfa78f2",
            head_sha="a" * 40,
            base_branch="main",
            base_sha="b" * 40,
            mergeable=True,
        )

    async def list_check_runs(
        self, repository: RepositoryRef, head_sha: str
    ) -> tuple[CheckRunObservation, ...]:
        return (CheckRunObservation("901", "unit-tests", head_sha, True, True, "passed"),)

    async def list_pull_request_reviews(
        self, repository: RepositoryRef, number: int
    ) -> tuple[PullRequestReviewObservation, ...]:
        return ()

    async def ready_for_review(
        self, repository: RepositoryRef, number: int, *, idempotency_key: str
    ) -> PullRequestObservation:
        self.ready_calls.append(idempotency_key)
        if self.draft_by_number.get(number, False):
            self.promotions.append(number)
            self.draft_by_number[number] = False
        return await self.get_pull_request(repository, number)

    async def merge_pull_request(self, command: MergePullRequestCommand) -> MergePullRequestResult:
        raise AssertionError("a candidate awaiting review must never be merged")


async def draft_delivery(*, with_dependency: bool = False):
    """A ChangeSet whose PRs are open and draft, exactly as handle_batch leaves them."""

    catalog = InMemoryRepositoryCatalog()
    consumer_id = uuid4()
    await catalog.add(
        RepositoryProfile(id=consumer_id, name="pricing", url="https://github.com/acme/pricing")
    )
    producer_id = uuid4()
    if with_dependency:
        await catalog.add(
            RepositoryProfile(id=producer_id, name="api", url="https://github.com/acme/api")
        )

    def input_for(repository_id, branch, depends_on=()):
        return RepositoryCandidateInput(
            repository_id=repository_id,
            task_id=uuid4(),
            commit_sha="a" * 40,
            base_sha="b" * 40,
            branch_name=branch,
            depends_on=tuple(depends_on),
            required_checks=("unit-tests",),
            required_approvals=1,
        )

    candidates = (
        (input_for(producer_id, "repomesh/api"),) if with_dependency else ()
    ) + (
        input_for(
            consumer_id,
            "repomesh/a762abba/9dfa78f2",
            depends_on=(producer_id,) if with_dependency else (),
        ),
    )
    store = InMemoryChangeSetStore()
    service = DeliveryService(store)
    change_set = await service.prepare(
        PrepareChangeSetCommand(
            organization_id=uuid4(),
            project_id=uuid4(),
            created_by_agent_id=uuid4(),
            title="Draft delivery",
            validation_snapshot_id=uuid4(),
            candidates=candidates,
        ),
        idempotency_key="draft-delivery",
    )
    if with_dependency:
        await service.observe_pull_request(
            PullRequestObservationCommand(
                change_set.id, producer_id, 2, "https://github.com/acme/api/pull/2", "a" * 40
            )
        )
    await service.observe_pull_request(
        PullRequestObservationCommand(
            change_set.id,
            consumer_id,
            3,
            "https://github.com/acme/pricing/pull/3",
            "a" * 40,
        )
    )
    return store, catalog, change_set.id, producer_id, consumer_id


@pytest.mark.asyncio
async def test_sweep_undrafts_a_candidate_whose_ci_already_moved_it_past_pr_open() -> None:
    """The whole point: the reconciler is the only thing driving this deployment.

    A candidate with no dependencies is eligible the moment its PR exists --
    ``all()`` over an empty ``depends_on`` is vacuously true -- and by the time
    the sweep asks, this sweep's own CI observation has already moved it out of
    PR_OPEN. Both halves have to hold or the PR stays draft forever.
    """

    store, catalog, change_set_id, _, consumer_id = await draft_delivery()
    service = DeliveryService(store)
    adapter = DraftPullRequestAdapter()
    reconciler = DeliveryReconciler(service, ChangeSetSCMCoordinator(service, catalog, adapter))

    await reconciler.run_once()

    assert adapter.promotions == [3]
    assert len(adapter.ready_calls) == 1
    candidate = (await service.get(change_set_id)).repositories[0]
    assert candidate.repository_id == consumer_id
    # CI was recorded in the same sweep, so the status is no longer PR_OPEN --
    # the exact state the old guard refused to promote.
    assert candidate.status is RepositoryDeliveryStatus.REVIEW_PENDING


@pytest.mark.asyncio
async def test_a_second_sweep_does_not_promote_the_pull_request_twice() -> None:
    store, catalog, change_set_id, _, _ = await draft_delivery()
    service = DeliveryService(store)
    adapter = DraftPullRequestAdapter()
    reconciler = DeliveryReconciler(service, ChangeSetSCMCoordinator(service, catalog, adapter))

    await reconciler.run_once()
    await reconciler.run_once()
    await reconciler.run_once()

    # The remote is promoted once; the later attempts are the adapter's
    # read-then-skip, which is where this path's idempotency lives.
    assert adapter.promotions == [3]
    assert len(adapter.ready_calls) == 3
    assert (await service.get(change_set_id)).repositories[0].pull_request_number == 3


@pytest.mark.asyncio
async def test_repeated_sweeps_queue_exactly_one_undraft_command() -> None:
    """The wired deployment's path: dedup by idempotency key at the queue."""

    store, catalog, change_set_id, _, consumer_id = await draft_delivery()
    service = DeliveryService(store)
    commands = SCMCommandService(InMemorySCMCommandStore())
    adapter = DraftPullRequestAdapter()
    reconciler = DeliveryReconciler(
        service,
        ChangeSetSCMCoordinator(service, catalog, adapter, command_service=commands),
    )

    await reconciler.run_once()
    await reconciler.run_once()

    queued = await commands.list_dispatchable()
    undrafts = [item for item in queued if item.kind is SCMCommandKind.UNDRAFT_PULL_REQUEST]
    assert len(undrafts) == 1
    assert undrafts[0].repository_id == consumer_id
    assert undrafts[0].payload["pull_request_number"] == 3
    assert undrafts[0].idempotency_key == f"undraft:{change_set_id}:{consumer_id}:3"
    # The command service, not the adapter, is what deduplicated here.
    assert adapter.ready_calls == []


@pytest.mark.asyncio
async def test_sweep_leaves_a_candidate_with_an_unmerged_dependency_draft() -> None:
    store, catalog, _, _, _ = await draft_delivery(with_dependency=True)
    service = DeliveryService(store)
    adapter = DraftPullRequestAdapter(drafts={2: True, 3: True})
    reconciler = DeliveryReconciler(service, ChangeSetSCMCoordinator(service, catalog, adapter))

    await reconciler.run_once()

    # The producer depends on nothing and is promoted; the consumer waits for
    # the producer's merge, which has not happened.
    assert adapter.promotions == [2]
    assert adapter.draft_by_number[3] is True


class MergeableAdapter:
    """A non-draft, open, green pull request -- everything the gate asks for.

    Faithful on one point that matters: once merged, GitHub reports the PR as
    merged. A fake that kept answering "open" would let the sweep re-decide a
    finished merge, which is a property of the fake and not of the system.
    """

    def __init__(self) -> None:
        self.merges: list[int] = []
        self.merged_numbers: set[int] = set()

    async def get_pull_request(
        self, repository: RepositoryRef, number: int
    ) -> PullRequestObservation:
        merged = number in self.merged_numbers
        return PullRequestObservation(
            provider=SCMProvider.GITHUB,
            repository=repository,
            number=number,
            url=f"https://github.com/acme/pricing/pull/{number}",
            state=PullRequestState.MERGED if merged else PullRequestState.OPEN,
            draft=False,
            head_branch="repomesh/a762abba/9dfa78f2",
            head_sha="a" * 40,
            base_branch="main",
            base_sha="b" * 40,
            mergeable=True,
            merge_sha="d" * 40 if merged else None,
        )

    async def list_check_runs(
        self, repository: RepositoryRef, head_sha: str
    ) -> tuple[CheckRunObservation, ...]:
        return (CheckRunObservation("901", "unit-tests", head_sha, True, True, "passed"),)

    async def list_pull_request_reviews(
        self, repository: RepositoryRef, number: int
    ) -> tuple[PullRequestReviewObservation, ...]:
        return ()

    async def ready_for_review(
        self, repository: RepositoryRef, number: int, *, idempotency_key: str
    ) -> PullRequestObservation:
        return await self.get_pull_request(repository, number)

    async def merge_pull_request(self, command: MergePullRequestCommand) -> MergePullRequestResult:
        self.merges.append(command.number)
        self.merged_numbers.add(command.number)
        return MergePullRequestResult(True, "d" * 40, "merged")


async def mergeable_delivery():
    """A candidate the gate will allow: green required check, no approval required."""

    repository_id = uuid4()
    catalog = InMemoryRepositoryCatalog()
    await catalog.add(
        RepositoryProfile(id=repository_id, name="pricing", url="https://github.com/acme/pricing")
    )
    store = InMemoryChangeSetStore()
    service = DeliveryService(store)
    change_set = await service.prepare(
        PrepareChangeSetCommand(
            organization_id=uuid4(),
            project_id=uuid4(),
            created_by_agent_id=uuid4(),
            title="Mergeable delivery",
            validation_snapshot_id=uuid4(),
            candidates=(
                RepositoryCandidateInput(
                    repository_id=repository_id,
                    task_id=uuid4(),
                    commit_sha="a" * 40,
                    base_sha="b" * 40,
                    branch_name="repomesh/a762abba/9dfa78f2",
                    required_checks=("unit-tests",),
                    required_approvals=0,
                ),
            ),
        ),
        idempotency_key="mergeable-delivery",
    )
    await service.observe_pull_request(
        PullRequestObservationCommand(
            change_set.id, repository_id, 3, "https://github.com/acme/pricing/pull/3", "a" * 40
        )
    )
    return store, catalog, change_set.id, repository_id


@pytest.mark.asyncio
async def test_the_sweep_already_decides_and_merges_once() -> None:
    """The forward decision is in reconcile_and_merge's main path, not only its
    already-merged safety branch: an open non-draft PR whose gate opens is
    merged by the sweep itself, and repeated sweeps do not merge again."""

    store, catalog, change_set_id, _ = await mergeable_delivery()
    service = DeliveryService(store)
    adapter = MergeableAdapter()
    reconciler = DeliveryReconciler(service, ChangeSetSCMCoordinator(service, catalog, adapter))

    await reconciler.run_once()
    await reconciler.run_once()
    await reconciler.run_once()

    assert adapter.merges == [3]
    candidate = (await service.get(change_set_id)).repositories[0]
    assert candidate.status is RepositoryDeliveryStatus.MERGED
    assert candidate.merge_sha == "d" * 40


@pytest.mark.asyncio
async def test_the_sweep_queues_exactly_one_merge_command() -> None:
    """The wired deployment merges through the SCM command queue, not inline."""

    store, catalog, change_set_id, repository_id = await mergeable_delivery()
    service = DeliveryService(store)
    commands = SCMCommandService(InMemorySCMCommandStore())
    adapter = MergeableAdapter()
    reconciler = DeliveryReconciler(
        service,
        ChangeSetSCMCoordinator(service, catalog, adapter, command_service=commands),
    )

    await reconciler.run_once()
    await reconciler.run_once()
    await reconciler.run_once()

    queued = await commands.list_dispatchable()
    merges = [item for item in queued if item.kind is SCMCommandKind.MERGE_PULL_REQUEST]
    assert len(merges) == 1
    assert merges[0].idempotency_key == f"merge:{change_set_id}:{repository_id}:{'a' * 40}"
    # The adapter is never called directly on this path.
    assert adapter.merges == []


@pytest.mark.asyncio
async def test_a_blocked_gate_is_a_decision_the_sweep_makes_without_merging() -> None:
    """required_approvals: 1 with no reviews is an honest 'no', not a hang.

    The verdict itself is computed on read (``attach_merge_gates``), so what
    the sweep owes here is to reach the decision point and refuse to merge --
    not to persist a verdict, which nothing stores.
    """

    store, catalog, change_set_id, _, repository_id = await draft_delivery()
    service = DeliveryService(store)
    adapter = MergeableAdapter()
    reconciler = DeliveryReconciler(service, ChangeSetSCMCoordinator(service, catalog, adapter))

    await reconciler.run_once()

    assert adapter.merges == []
    gate = await service.evaluate_merge_gate(change_set_id, repository_id)
    assert gate.allowed is False
    assert "required reviews have not passed" in gate.reasons
