import json
import subprocess
from pathlib import Path
from uuid import uuid4

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


async def _store_candidate(
    store: InMemoryTaskStore,
    *,
    organization_id,
    project_id,
    repository_id,
    leader_id,
    workspace: Path,
    base_sha: str,
    commit_sha: str,
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
                "baseSha": base_sha,
                "workspacePath": str(workspace),
                "testResults": [{"command": "pytest", "exitCode": 0}],
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
    first_leader, _ = await _store_candidate(
        tasks,
        organization_id=organization_id,
        project_id=project_id,
        repository_id=first_repository_id,
        leader_id=uuid4(),
        workspace=first_workspace,
        base_sha=first_base,
        commit_sha=first_head,
    )
    second_leader, _ = await _store_candidate(
        tasks,
        organization_id=organization_id,
        project_id=project_id,
        repository_id=second_repository_id,
        leader_id=uuid4(),
        workspace=second_workspace,
        base_sha=second_base,
        commit_sha=second_head,
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
    assert all(not command.draft for command in adapter.pull_requests)
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
