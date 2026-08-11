"""Turn a completed execution plan into governed SCM delivery operations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from repomesh.modules.delivery import DeliveryService
from repomesh.modules.delivery.contracts import (
    AppendCandidatesCommand,
    PrepareChangeSetCommand,
    RepositoryCandidateInput,
)
from repomesh.modules.review_validation import (
    CreateValidationSnapshotCommand,
    ValidationSnapshotService,
    ValidationTestInput,
)
from repomesh.modules.task_orchestration.contracts import ExecutionPlanView, TaskStatus
from repomesh.modules.task_orchestration.ports import TaskStore

from .delivery import ChangeSetSCMCoordinator, PublishChangeSetPullRequestCommand


@dataclass(frozen=True, slots=True)
class PlanDeliveryPolicy:
    base_branch: str = "main"
    required_checks: tuple[str, ...] = ()
    required_approvals: int = 1


class PlanDeliveryFinalizer:
    """Create one idempotent ChangeSet and publish every completed candidate."""

    def __init__(
        self,
        delivery: DeliveryService,
        coordinator: ChangeSetSCMCoordinator,
        tasks: TaskStore,
        policy: PlanDeliveryPolicy,
        validation: ValidationSnapshotService | None = None,
    ) -> None:
        self._delivery = delivery
        self._coordinator = coordinator
        self._tasks = tasks
        self._policy = policy
        self._validation = validation

    async def handle(self, plan: ExecutionPlanView) -> None:
        candidates, workspaces, tests = await self._candidates(plan)
        if not candidates:
            return
        idempotency_key = f"execution-plan:{plan.id}:delivery"
        existing = await self._delivery.get_by_idempotency_key(idempotency_key)
        validation_snapshot_id = (
            existing.validation_snapshot_id if existing is not None else None
        )
        if existing is None and self._validation is not None:
            snapshot = await self._validation.create(
                CreateValidationSnapshotCommand(
                    organization_id=plan.organization_id,
                    project_id=plan.project_id,
                    specification_version_id=None,
                    candidate_heads={item.repository_id: item.commit_sha for item in candidates},
                    tests=tuple(tests),
                    environment={
                        "runner_protocol": "v1",
                        "execution_plan": str(plan.id),
                    },
                )
            )
            validation_snapshot_id = snapshot.id
        change_set = await self._delivery.prepare(
            PrepareChangeSetCommand(
                organization_id=plan.organization_id,
                project_id=plan.project_id,
                created_by_agent_id=plan.created_by_agent_id,
                title=f"RepoMesh delivery {str(plan.id)[:8]}",
                validation_snapshot_id=validation_snapshot_id,
                candidates=tuple(candidates),
            ),
            idempotency_key=idempotency_key,
        )
        for candidate in sorted(change_set.repositories, key=lambda item: item.merge_order):
            if candidate.pull_request_number is not None:
                continue
            await self._coordinator.publish_and_open_draft_pull_request(
                PublishChangeSetPullRequestCommand(
                    change_set_id=change_set.id,
                    repository_id=candidate.repository_id,
                    workspace=workspaces[candidate.repository_id],
                    base_branch=self._policy.base_branch,
                    body=(
                        f"Automated RepoMesh delivery for execution plan `{plan.id}`.\n\n"
                        "The candidate commit passed its frozen Task Spec commands."
                    ),
                    draft=False,
                )
            )

    async def handle_batch(self, plan: ExecutionPlanView) -> None:
        """Deliver the plan's current batch (batch-by-batch delivery).

        The first successful batch creates the plan's ChangeSet and the later
        batches append their candidates to the same ChangeSet, so merge order
        is preserved across the whole plan. Re-invocation for an already
        delivered batch is idempotent: existing pull requests are skipped and
        ``append_candidates`` ignores repositories already present.
        """
        batch_index = plan.current_batch_index
        candidates, workspaces, tests = await self._candidates_for_batch(plan, batch_index)
        if not candidates:
            return
        idempotency_key = f"execution-plan:{plan.id}:delivery"
        existing = await self._delivery.get_by_idempotency_key(idempotency_key)
        if existing is None:
            validation_snapshot_id = None
            if self._validation is not None:
                snapshot = await self._validation.create(
                    CreateValidationSnapshotCommand(
                        organization_id=plan.organization_id,
                        project_id=plan.project_id,
                        specification_version_id=None,
                        candidate_heads={
                            item.repository_id: item.commit_sha for item in candidates
                        },
                        tests=tuple(tests),
                        environment={
                            "runner_protocol": "v1",
                            "execution_plan": str(plan.id),
                        },
                    )
                )
                validation_snapshot_id = snapshot.id
            change_set = await self._delivery.prepare(
                PrepareChangeSetCommand(
                    organization_id=plan.organization_id,
                    project_id=plan.project_id,
                    created_by_agent_id=plan.created_by_agent_id,
                    title=f"RepoMesh delivery {str(plan.id)[:8]}",
                    validation_snapshot_id=validation_snapshot_id,
                    candidates=tuple(candidates),
                ),
                idempotency_key=idempotency_key,
            )
        else:
            change_set = await self._delivery.append_candidates(
                AppendCandidatesCommand(
                    change_set_id=existing.id,
                    candidates=tuple(candidates),
                ),
                idempotency_key=f"{idempotency_key}:b{batch_index}",
            )
        for candidate in sorted(change_set.repositories, key=lambda item: item.merge_order):
            if (
                candidate.pull_request_number is not None
                or candidate.repository_id not in workspaces
            ):
                continue
            await self._coordinator.publish_and_open_draft_pull_request(
                PublishChangeSetPullRequestCommand(
                    change_set_id=change_set.id,
                    repository_id=candidate.repository_id,
                    workspace=workspaces[candidate.repository_id],
                    base_branch=self._policy.base_branch,
                    body=(
                        f"Automated RepoMesh delivery for execution plan `{plan.id}` "
                        f"(batch {batch_index + 1}).\n\n"
                        "The candidate commit passed its frozen Task Spec commands."
                    ),
                    draft=False,
                )
            )

    async def _candidates_for_batch(
        self, plan: ExecutionPlanView, batch_index: int
    ) -> tuple[
        list[RepositoryCandidateInput],
        dict[UUID, Path],
        list[ValidationTestInput],
    ]:
        candidates: list[RepositoryCandidateInput] = []
        workspaces: dict[UUID, Path] = {}
        tests: list[ValidationTestInput] = []
        earlier_repositories = [
            item.repository_id
            for batch in plan.batches[:batch_index]
            for item in batch
        ]
        for planned in plan.batches[batch_index]:
            if planned.leader_task_id is None:
                continue
            workers = await self._tasks.list_by_parent(planned.leader_task_id)
            succeeded = [task for task in workers if task.status is TaskStatus.SUCCEEDED]
            if len(succeeded) != 1:
                raise ValueError(
                    f"repository {planned.repository_id} has no unique successful candidate"
                )
            worker = succeeded[0]
            evidence = self._evidence(worker.result_summary)
            commit_sha = str(evidence.get("commitSha") or "").lower()
            base_sha = str(evidence.get("baseSha") or "").lower()
            workspace = Path(str(evidence.get("workspacePath") or ""))
            if not self._full_sha(commit_sha) or not self._full_sha(base_sha):
                raise ValueError("Runner evidence has no frozen commit/base SHA")
            if not workspace.is_dir():
                raise ValueError("Runner evidence workspace no longer exists")
            branch = f"repomesh/{str(plan.id)[:8]}/{str(planned.repository_id)[:8]}"
            candidates.append(
                RepositoryCandidateInput(
                    repository_id=planned.repository_id,
                    task_id=worker.id,
                    commit_sha=commit_sha,
                    base_sha=base_sha,
                    branch_name=branch,
                    depends_on=tuple(earlier_repositories),
                    required_checks=self._policy.required_checks,
                    required_approvals=self._policy.required_approvals,
                )
            )
            workspaces[planned.repository_id] = workspace
            raw_tests = evidence.get("testResults") or ()
            if not isinstance(raw_tests, list) or not raw_tests:
                raise ValueError("Runner evidence has no test results")
            for result in raw_tests:
                if not isinstance(result, dict):
                    raise ValueError("Runner test evidence is malformed")
                tests.append(
                    ValidationTestInput(
                        repository_id=planned.repository_id,
                        command=str(result.get("command") or "").strip(),
                        exit_code=int(result.get("exitCode", -1)),
                        summary=str(
                            result.get("stderr")
                            or result.get("stdout")
                            or result.get("summary")
                            or ""
                        ),
                    )
                )
            earlier_repositories.append(planned.repository_id)
        return candidates, workspaces, tests

    async def _candidates(
        self, plan: ExecutionPlanView
    ) -> tuple[
        list[RepositoryCandidateInput],
        dict[UUID, Path],
        list[ValidationTestInput],
    ]:
        candidates: list[RepositoryCandidateInput] = []
        workspaces: dict[UUID, Path] = {}
        tests: list[ValidationTestInput] = []
        for batch_index in range(len(plan.batches)):
            batch_candidates, batch_workspaces, batch_tests = (
                await self._candidates_for_batch(plan, batch_index)
            )
            candidates.extend(batch_candidates)
            workspaces.update(batch_workspaces)
            tests.extend(batch_tests)
        return candidates, workspaces, tests

    @staticmethod
    def _evidence(raw: str | None) -> dict[str, object]:
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError("task result is not structured Runner evidence") from error
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _full_sha(value: str) -> bool:
        return len(value) == 40 and all(character in "0123456789abcdef" for character in value)
