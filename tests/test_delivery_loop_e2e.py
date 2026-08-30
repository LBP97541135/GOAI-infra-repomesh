import json
import subprocess
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from repomesh.integrations.scm import (
    ChangeSetSCMCoordinator,
    GitBranchPublisher,
    GitHubCIObservation,
    GitHubReviewObservation,
    PlanDeliveryFinalizer,
    PlanDeliveryPolicy,
)
from repomesh.integrations.scm.contracts import (
    CheckRunObservation,
    CreateDraftPullRequestCommand,
    MergePullRequestCommand,
    MergePullRequestResult,
    PullRequestObservation,
    PullRequestReviewObservation,
    PullRequestState,
    RepositoryRef,
    SCMProvider,
)
from repomesh.modules.delivery import DeliveryService, InMemoryChangeSetStore
from repomesh.modules.delivery.contracts import ChangeSetStatus, ReviewState
from repomesh.modules.repository_intelligence.domain import RepositoryProfile
from repomesh.modules.repository_intelligence.infrastructure import InMemoryRepositoryCatalog
from repomesh.modules.review_validation import (
    InMemoryValidationSnapshotStore,
    ValidationSnapshotService,
)
from repomesh.modules.task_orchestration.contracts import (
    BatchDeliveryRefused,
    ExecutionPlanStatus,
    ExecutionPlanView,
    PlannedRepositoryTaskView,
    TaskStatus,
)
from repomesh.modules.task_orchestration.domain import Task
from repomesh.modules.task_orchestration.infrastructure import InMemoryTaskStore


def _git(path: Path, *arguments: str, bare: bool = False) -> str:
    command = ["git", "--git-dir", str(path)] if bare else ["git", "-C", str(path)]
    return subprocess.run(
        [*command, *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()


def _repository(root: Path, name: str) -> tuple[Path, Path, str, str]:
    remote = root / f"{name}.git"
    workspace = root / name
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "init", str(workspace)], check=True, capture_output=True)
    _git(workspace, "config", "user.email", "worker@repomesh.test")
    _git(workspace, "config", "user.name", "RepoMesh Worker")
    _git(workspace, "remote", "add", "origin", str(remote))
    (workspace / "result.txt").write_text("base\n", encoding="utf-8")
    _git(workspace, "add", "result.txt")
    _git(workspace, "commit", "-m", "base")
    base = _git(workspace, "rev-parse", "HEAD")
    _git(workspace, "push", "origin", f"{base}:refs/heads/main")
    (workspace / "result.txt").write_text("candidate\n", encoding="utf-8")
    _git(workspace, "add", "result.txt")
    _git(workspace, "commit", "-m", "candidate")
    return remote, workspace, base, _git(workspace, "rev-parse", "HEAD")


class RecordingGitHubAdapter:
    def __init__(self) -> None:
        self.pull_requests: list[CreateDraftPullRequestCommand] = []
        self.merges: list[MergePullRequestCommand] = []
        self.updates: list[tuple[RepositoryRef, int, str]] = []
        self.labels: list[tuple[RepositoryRef, int, str]] = []

    async def create_draft_pull_request(
        self, command: CreateDraftPullRequestCommand
    ) -> PullRequestObservation:
        self.pull_requests.append(command)
        number = len(self.pull_requests)
        return PullRequestObservation(
            provider=SCMProvider.GITHUB,
            repository=command.repository,
            number=number,
            url=f"https://github.com/{command.repository.owner}/{command.repository.name}/pull/{number}",
            state=PullRequestState.OPEN,
            draft=command.draft,
            head_branch=command.head_branch,
            head_sha=command.expected_head_sha,
            base_branch=command.base_branch,
            base_sha="b" * 40,
            mergeable=True,
        )

    async def get_pull_request(
        self, repository: RepositoryRef, number: int
    ) -> PullRequestObservation:
        command = self.pull_requests[number - 1]
        merged_index = next(
            (
                index
                for index, merge in enumerate(self.merges, start=1)
                if merge.number == number and merge.repository == repository
            ),
            None,
        )
        return PullRequestObservation(
            provider=SCMProvider.GITHUB,
            repository=repository,
            number=number,
            url=f"https://github.com/{repository.owner}/{repository.name}/pull/{number}",
            state=(
                PullRequestState.MERGED
                if merged_index is not None
                else PullRequestState.OPEN
            ),
            draft=False,
            head_branch=command.head_branch,
            head_sha=command.expected_head_sha,
            base_branch=command.base_branch,
            base_sha="b" * 40,
            mergeable=True,
            merge_sha=(
                ("c" if merged_index == 1 else "d") * 40
                if merged_index is not None
                else None
            ),
        )

    async def close_pull_request(
        self, repository: RepositoryRef, number: int, *, idempotency_key: str
    ) -> PullRequestObservation:
        raise NotImplementedError

    async def list_check_runs(
        self, repository: RepositoryRef, head_sha: str
    ) -> tuple[CheckRunObservation, ...]:
        return ()

    async def list_pull_request_reviews(
        self, repository: RepositoryRef, number: int
    ) -> tuple[PullRequestReviewObservation, ...]:
        return ()

    async def merge_pull_request(
        self, command: MergePullRequestCommand
    ) -> MergePullRequestResult:
        self.merges.append(command)
        marker = "c" if len(self.merges) == 1 else "d"
        return MergePullRequestResult(True, marker * 40, "merged")

    async def update_pull_request(
        self, repository: RepositoryRef, number: int, *, body: str, idempotency_key: str
    ) -> PullRequestObservation:
        self.updates.append((repository, number, body))
        return await self.get_pull_request(repository, number)

    async def add_label(
        self, repository: RepositoryRef, number: int, label: str, *, idempotency_key: str
    ) -> None:
        self.labels.append((repository, number, label))


async def _store_candidate(
    store: InMemoryTaskStore,
    *,
    organization_id,
    project_id,
    repository_id,
    leader_id,
    workspace: Path,
    base_sha: str,
    # Nullable since A-18's fourth face: a Runner document may carry
    # ``commitSha: null``. On a SUCCEEDED worker that is a contradiction
    # delivery has to refuse, which is what the test below pins.
    commit_sha: str | None,
    run_id: UUID | None = None,
    test_results: list | None = None,
) -> tuple[Task, Task]:
    actor = uuid4()
    leader = Task(
        organization_id=organization_id,
        project_id=project_id,
        repository_id=repository_id,
        assigned_by_agent_id=actor,
        assignee_agent_id=uuid4(),
        title="repository task",
        instruction="implement",
        acceptance=("tests pass",),
        id=leader_id,
        status=TaskStatus.SUCCEEDED,
    )
    worker = Task(
        organization_id=organization_id,
        project_id=project_id,
        repository_id=repository_id,
        parent_task_id=leader.id,
        assigned_by_agent_id=actor,
        assignee_agent_id=uuid4(),
        title="worker task",
        instruction="implement",
        acceptance=("tests pass",),
        status=TaskStatus.SUCCEEDED,
        result_summary=json.dumps(
            {
                "commitSha": commit_sha,
                "runId": str(run_id) if run_id is not None else None,
                "baseSha": base_sha,
                "workspacePath": str(workspace),
                "testResults": (
                    [{"command": "pytest", "exitCode": 0}]
                    if test_results is None
                    else test_results
                ),
            }
        ),
    )
    await store.add(leader, idempotency_key=str(leader.id), request_fingerprint="leader")
    await store.add(worker, idempotency_key=str(worker.id), request_fingerprint="worker")
    return leader, worker


@pytest.mark.asyncio
async def test_completed_two_repository_plan_reaches_reviewed_ci_green_merge(
    tmp_path: Path,
) -> None:
    first_remote, first_workspace, first_base, first_head = _repository(tmp_path, "api")
    second_remote, second_workspace, second_base, second_head = _repository(tmp_path, "web")
    organization_id = uuid4()
    project_id = uuid4()
    creator_id = uuid4()
    first_repository_id = uuid4()
    second_repository_id = uuid4()
    tasks = InMemoryTaskStore()
    first_run_id = uuid4()
    second_run_id = uuid4()
    first_leader, first_worker = await _store_candidate(
        tasks,
        organization_id=organization_id,
        project_id=project_id,
        repository_id=first_repository_id,
        leader_id=uuid4(),
        workspace=first_workspace,
        base_sha=first_base,
        commit_sha=first_head,
        run_id=first_run_id,
    )
    second_leader, second_worker = await _store_candidate(
        tasks,
        organization_id=organization_id,
        project_id=project_id,
        repository_id=second_repository_id,
        leader_id=uuid4(),
        workspace=second_workspace,
        base_sha=second_base,
        commit_sha=second_head,
        run_id=second_run_id,
    )
    plan = ExecutionPlanView(
        id=uuid4(),
        organization_id=organization_id,
        project_id=project_id,
        created_by_agent_id=creator_id,
        status=ExecutionPlanStatus.COMPLETED,
        current_batch_index=1,
        batches=(
            (
                PlannedRepositoryTaskView(
                    first_repository_id, "api", "implement", (), first_leader.id
                ),
            ),
            (
                PlannedRepositoryTaskView(
                    second_repository_id, "web", "implement", (), second_leader.id
                ),
            ),
        ),
    )
    catalog = InMemoryRepositoryCatalog()
    await catalog.add(
        RepositoryProfile(
            id=first_repository_id,
            name="api",
            url="https://github.com/acme/api",
        )
    )
    await catalog.add(
        RepositoryProfile(
            id=second_repository_id,
            name="web",
            url="https://github.com/acme/web",
        )
    )
    validation_store = InMemoryValidationSnapshotStore()
    validation = ValidationSnapshotService(validation_store)
    delivery = DeliveryService(InMemoryChangeSetStore())
    adapter = RecordingGitHubAdapter()
    coordinator = ChangeSetSCMCoordinator(
        delivery, catalog, adapter, GitBranchPublisher(tmp_path)
    )
    finalizer = PlanDeliveryFinalizer(
        delivery,
        coordinator,
        tasks,
        PlanDeliveryPolicy(required_checks=("unit",), required_approvals=1),
        validation,
    )

    await finalizer.handle(plan)
    first_change_set, _ = await delivery.resolve_candidate(first_repository_id, first_head)
    first_snapshot_id = first_change_set.validation_snapshot_id
    await finalizer.handle(plan)
    change_set, _ = await delivery.resolve_candidate(first_repository_id, first_head)
    assert len(adapter.pull_requests) == 2
    assert change_set.id == first_change_set.id
    assert change_set.validation_snapshot_id == first_snapshot_id
    assert all(command.draft for command in adapter.pull_requests)
    # D-9: every published description carries the whole chain back to the
    # Issue -- issue, change_set, plan, repository, task, run, worker_agent and
    # commit -- assembled here because the finalizer is the only place holding
    # all eight at once.
    traceability = {
        "api": (first_repository_id, first_worker, first_run_id, first_head),
        "web": (second_repository_id, second_worker, second_run_id, second_head),
    }
    for command in adapter.pull_requests:
        repository_id, worker, run_id, head = traceability[command.repository.name]
        assert f"- issue: `{project_id}`" in command.body
        assert f"- change_set: `{change_set.id}`" in command.body
        assert f"- plan: `{plan.id}`" in command.body
        assert f"- repository: `{repository_id}`" in command.body
        assert f"- task: `{worker.id}`" in command.body
        assert f"- run: `{run_id}`" in command.body
        assert f"- worker_agent: `{worker.assignee_agent_id}`" in command.body
        assert f"- commit: `{head}`" in command.body
    # Sibling PR descriptions are back-filled with links to every PR in the
    # ChangeSet, and keep the traceability chain + execution order markers.
    # handle() runs twice (create + idempotent replay), so every PR is updated
    # twice.
    assert len(adapter.updates) == 4
    for repository, _, body in adapter.updates:
        repository_id, worker, run_id, head = traceability[repository.name]
        assert f"- run: `{run_id}`" in body
        assert f"- worker_agent: `{worker.assignee_agent_id}`" in body
        assert f"- commit: `{head}`" in body
        assert "execution order: full plan" in body
        assert "## Sibling PRs in this ChangeSet" in body
        assert "PR #1" in body and "PR #2" in body
    first_published = _git(
        first_remote, "rev-parse", adapter.pull_requests[0].head_branch, bare=True
    )
    second_published = _git(
        second_remote, "rev-parse", adapter.pull_requests[1].head_branch, bare=True
    )
    assert first_published == first_head
    assert second_published == second_head

    for repository_id, name, head in (
        (first_repository_id, "api", first_head),
        (second_repository_id, "web", second_head),
    ):
        await coordinator.record_github_ci(
            change_set.id,
            repository_id,
            GitHubCIObservation(
                RepositoryRef.from_github("acme", name),
                f"ci-{name}",
                "unit",
                head,
                "completed",
                "success",
                "passed",
            ),
        )
        await coordinator.record_github_review(
            change_set.id,
            repository_id,
            GitHubReviewObservation(
                RepositoryRef.from_github("acme", name),
                f"review-{name}",
                "reviewer",
                head,
                ReviewState.APPROVED,
                "approved",
            ),
        )

    requested = await coordinator.merge_ready_repositories(change_set.id)
    assert requested.status is not ChangeSetStatus.DELIVERED
    await coordinator.reconcile_and_merge(change_set.id)
    completed = await coordinator.reconcile_and_merge(change_set.id)

    assert completed.status is ChangeSetStatus.DELIVERED
    assert [command.repository.name for command in adapter.merges] == ["api", "web"]


@pytest.mark.asyncio
async def test_handle_batch_delivers_batches_into_one_change_set_in_order(
    tmp_path: Path,
) -> None:
    first_remote, first_workspace, first_base, first_head = _repository(tmp_path, "api")
    second_remote, second_workspace, second_base, second_head = _repository(
        tmp_path, "web"
    )
    organization_id = uuid4()
    project_id = uuid4()
    creator_id = uuid4()
    first_repository_id = uuid4()
    second_repository_id = uuid4()
    tasks = InMemoryTaskStore()
    first_run_id = uuid4()
    second_run_id = uuid4()
    first_leader, first_worker = await _store_candidate(
        tasks,
        organization_id=organization_id,
        project_id=project_id,
        repository_id=first_repository_id,
        leader_id=uuid4(),
        workspace=first_workspace,
        base_sha=first_base,
        commit_sha=first_head,
        run_id=first_run_id,
    )
    second_leader, second_worker = await _store_candidate(
        tasks,
        organization_id=organization_id,
        project_id=project_id,
        repository_id=second_repository_id,
        leader_id=uuid4(),
        workspace=second_workspace,
        base_sha=second_base,
        commit_sha=second_head,
        run_id=second_run_id,
    )
    plan = ExecutionPlanView(
        id=uuid4(),
        organization_id=organization_id,
        project_id=project_id,
        created_by_agent_id=creator_id,
        status=ExecutionPlanStatus.IN_PROGRESS,
        current_batch_index=0,
        batches=(
            (
                PlannedRepositoryTaskView(
                    first_repository_id, "api", "implement", (), first_leader.id
                ),
            ),
            (
                PlannedRepositoryTaskView(
                    second_repository_id, "web", "implement", (), second_leader.id
                ),
            ),
        ),
    )
    catalog = InMemoryRepositoryCatalog()
    await catalog.add(
        RepositoryProfile(
            id=first_repository_id,
            name="api",
            url="https://github.com/acme/api",
        )
    )
    await catalog.add(
        RepositoryProfile(
            id=second_repository_id,
            name="web",
            url="https://github.com/acme/web",
        )
    )
    delivery = DeliveryService(InMemoryChangeSetStore())
    adapter = RecordingGitHubAdapter()
    coordinator = ChangeSetSCMCoordinator(
        delivery, catalog, adapter, GitBranchPublisher(tmp_path)
    )
    finalizer = PlanDeliveryFinalizer(
        delivery,
        coordinator,
        tasks,
        PlanDeliveryPolicy(required_checks=("unit",), required_approvals=1),
    )

    # Batch 0 succeeded: one PR opens and the plan's ChangeSet exists.
    await finalizer.handle_batch(plan)
    change_set, _ = await delivery.resolve_candidate(first_repository_id, first_head)
    assert len(adapter.pull_requests) == 1
    assert [item.repository_id for item in change_set.repositories] == [
        first_repository_id
    ]
    assert len(adapter.updates) == 1
    assert f"- plan: `{plan.id}`" in adapter.updates[0][2]
    assert f"- run: `{first_run_id}`" in adapter.updates[0][2]
    assert f"- worker_agent: `{first_worker.assignee_agent_id}`" in adapter.updates[0][2]

    # Replaying the same batch must not open a duplicate pull request.
    await finalizer.handle_batch(plan)
    assert len(adapter.pull_requests) == 1
    assert len(adapter.updates) == 2

    # Batch 1 appends to the SAME ChangeSet, ordered after batch 0.
    advanced = replace(plan, current_batch_index=1)
    await finalizer.handle_batch(advanced)
    change_set, _ = await delivery.resolve_candidate(first_repository_id, first_head)
    assert len(adapter.pull_requests) == 2
    assert len(change_set.repositories) == 2
    # Batch-by-batch: after the second batch, sibling links cover both PRs
    # and the back-filled body records the batch execution order.
    assert len(adapter.updates) == 4
    traceability = {
        "api": (first_worker, first_run_id),
        "web": (second_worker, second_run_id),
    }
    for repository, _, body in adapter.updates[-2:]:
        worker, run_id = traceability[repository.name]
        assert "execution order: batch 2" in body
        assert "## Sibling PRs in this ChangeSet" in body
        # Batch 1's pull request was opened with its own run and worker ids and
        # keeps them: the back-fill resolves provenance for every batch
        # delivered so far, not just the one it is standing on.
        assert f"- run: `{run_id}`" in body
        assert f"- worker_agent: `{worker.assignee_agent_id}`" in body
    orders = {item.repository_id: item.merge_order for item in change_set.repositories}
    assert orders[second_repository_id] > orders[first_repository_id]
    second = next(
        item
        for item in change_set.repositories
        if item.repository_id == second_repository_id
    )
    assert second.depends_on == (first_repository_id,)


@pytest.mark.asyncio
async def test_a_candidate_with_no_test_results_is_refused_by_name(tmp_path: Path) -> None:
    """Defect A-19: the refusal delivery actually makes, typed and attributable.

    This is the live failure of run d261dbb4, reproduced from the same shape of
    evidence: the dispatch carried ``testCommands: []`` because the console
    supplied none, the Runner therefore ran nothing, and the completion event
    came back with ``testResults: []``.

    Delivery refuses — and must keep refusing; nothing here relaxes that. What
    is asserted is that the refusal now arrives as a ``BatchDeliveryRefused``
    naming its repository and its task, because the advancer cannot record on
    the round what the refusal does not say, and because a bare ``ValueError``
    is indistinguishable from a bug in the delivering side.

    Also asserted: nothing was published. A refusal that had already opened a
    pull request would not be a refusal.
    """

    _remote, workspace, base_sha, head_sha = _repository(tmp_path, "checkout")
    organization_id = uuid4()
    project_id = uuid4()
    repository_id = uuid4()
    tasks = InMemoryTaskStore()
    leader, worker = await _store_candidate(
        tasks,
        organization_id=organization_id,
        project_id=project_id,
        repository_id=repository_id,
        leader_id=uuid4(),
        workspace=workspace,
        base_sha=base_sha,
        commit_sha=head_sha,
        test_results=[],
    )
    plan = ExecutionPlanView(
        id=uuid4(),
        organization_id=organization_id,
        project_id=project_id,
        created_by_agent_id=uuid4(),
        status=ExecutionPlanStatus.IN_PROGRESS,
        current_batch_index=0,
        batches=(
            (PlannedRepositoryTaskView(repository_id, "checkout", "implement", (), leader.id),),
        ),
    )
    catalog = InMemoryRepositoryCatalog()
    await catalog.add(
        RepositoryProfile(
            id=repository_id, name="checkout", url="https://github.com/acme/checkout"
        )
    )
    delivery = DeliveryService(InMemoryChangeSetStore())
    adapter = RecordingGitHubAdapter()
    finalizer = PlanDeliveryFinalizer(
        delivery,
        ChangeSetSCMCoordinator(delivery, catalog, adapter, GitBranchPublisher(tmp_path)),
        tasks,
        PlanDeliveryPolicy(),
    )

    with pytest.raises(BatchDeliveryRefused) as refused:
        await finalizer.handle_batch(plan)

    assert refused.value.reason == "Runner evidence has no test results"
    assert refused.value.repository_id == repository_id
    assert refused.value.task_id == worker.id
    assert adapter.pull_requests == []


@pytest.mark.asyncio
async def test_a_succeeded_candidate_with_no_commit_sha_is_refused(tmp_path) -> None:
    """A-18's fourth face widened ``commit_sha`` to nullable. Publication must not soften.

    Failed runs keep their evidence now, and their commit is null. This path
    only ever looks at SUCCEEDED workers, so a null here is a run that claimed
    success without committing anything — there is no head to push and none may
    be invented. The refusal already existed (``_full_sha`` rejects the empty
    string); what is pinned is that it still *arrives*, as a named refusal
    rather than the ``AttributeError`` a nullable field would otherwise cause
    one line earlier, and that nothing was published on the way.
    """

    _remote, workspace, base_sha, _head_sha = _repository(tmp_path, "checkout")
    organization_id = uuid4()
    project_id = uuid4()
    repository_id = uuid4()
    tasks = InMemoryTaskStore()
    leader, worker = await _store_candidate(
        tasks,
        organization_id=organization_id,
        project_id=project_id,
        repository_id=repository_id,
        leader_id=uuid4(),
        workspace=workspace,
        base_sha=base_sha,
        commit_sha=None,
    )
    # The evidence is present — that is the change — and it is still unusable here.
    evidence = worker.to_view().evidence
    assert evidence is not None
    assert evidence.commit_sha is None

    plan = ExecutionPlanView(
        id=uuid4(),
        organization_id=organization_id,
        project_id=project_id,
        created_by_agent_id=uuid4(),
        status=ExecutionPlanStatus.IN_PROGRESS,
        current_batch_index=0,
        batches=(
            (PlannedRepositoryTaskView(repository_id, "checkout", "implement", (), leader.id),),
        ),
    )
    catalog = InMemoryRepositoryCatalog()
    await catalog.add(
        RepositoryProfile(
            id=repository_id, name="checkout", url="https://github.com/acme/checkout"
        )
    )
    delivery = DeliveryService(InMemoryChangeSetStore())
    adapter = RecordingGitHubAdapter()
    finalizer = PlanDeliveryFinalizer(
        delivery,
        ChangeSetSCMCoordinator(delivery, catalog, adapter, GitBranchPublisher(tmp_path)),
        tasks,
        PlanDeliveryPolicy(),
    )

    with pytest.raises(BatchDeliveryRefused) as refused:
        await finalizer.handle_batch(plan)

    assert refused.value.reason == "Runner evidence has no frozen commit/base SHA"
    assert refused.value.repository_id == repository_id
    assert refused.value.task_id == worker.id
    assert adapter.pull_requests == []
