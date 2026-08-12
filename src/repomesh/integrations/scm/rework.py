from uuid import UUID

from repomesh.modules.delivery.contracts import ChangeSetView, RepositoryDeliveryView
from repomesh.modules.project.contracts import ProjectTopologyReader
from repomesh.modules.task_orchestration.contracts import (
    AssignTaskCommand,
    CreateCIReworkTaskCommand,
    TaskAssignmentGateway,
    TaskOrigin,
    TaskView,
)
from repomesh.shared.git import normalize_full_sha

from .recovery import RecoveryExecutionContext


def _failure_summary(candidate: RepositoryDeliveryView) -> str:
    """Name the checks that actually failed, not just that something did."""

    failed = [check for check in candidate.ci_checks if not check.passed]
    if failed:
        return "; ".join(f"{check.check_name}: {check.summary}" for check in failed)
    return candidate.ci_summary or "CI reported the candidate as failed"


class CIReworkTaskCreator:
    """Create an idempotent Worker task for a failed candidate revision.

    ``create`` takes a fully resolved command. ``create_for_failed_candidate``
    is the observation-side entry point: it resolves the repository's team from
    project topology the same way ``RecoveryConflictTaskCreator`` does, so the
    CI path does not have to know who owns a repository.
    """

    def __init__(
        self, tasks: TaskAssignmentGateway, topology: ProjectTopologyReader | None = None
    ) -> None:
        self._tasks = tasks
        self._topology = topology

    async def create_for_failed_candidate(
        self, change_set: ChangeSetView, repository_id: UUID
    ) -> UUID:
        if self._topology is None:
            raise ValueError("CI rework needs project topology to find the repository Worker")
        candidate = next(
            (item for item in change_set.repositories if item.repository_id == repository_id),
            None,
        )
        if candidate is None:
            raise ValueError(f"repository not in ChangeSet: {repository_id}")
        view = await self._topology.get_view(change_set.project_id)
        if view is None:
            raise ValueError(f"project topology is unavailable: {change_set.project_id}")
        team = next(
            (item for item in view.repository_teams if item.repository_id == repository_id),
            None,
        )
        if team is None or not team.worker_agent_ids:
            raise ValueError(f"repository {repository_id} has no Worker to repair a candidate")
        task = await self.create(
            CreateCIReworkTaskCommand(
                organization_id=change_set.organization_id,
                project_id=change_set.project_id,
                change_set_id=change_set.id,
                repository_id=repository_id,
                repository_manager_agent_id=team.leader_agent_id,
                worker_agent_id=team.worker_agent_ids[0],
                parent_task_id=candidate.task_id,
                failed_head_sha=candidate.commit_sha,
                failure_summary=_failure_summary(candidate),
                acceptance=(
                    "The failing checks pass on the repaired candidate.",
                    "The repaired candidate keeps the ChangeSet's agreed scope.",
                ),
            )
        )
        return task.id

    async def create(self, command: CreateCIReworkTaskCommand) -> TaskView:
        sha = normalize_full_sha(command.failed_head_sha, field="failed_head_sha")
        return await self._tasks.assign(
            AssignTaskCommand(
                organization_id=command.organization_id,
                project_id=command.project_id,
                repository_id=command.repository_id,
                assigned_by_agent_id=command.repository_manager_agent_id,
                assignee_agent_id=command.worker_agent_id,
                parent_task_id=command.parent_task_id,
                title="Repair failed delivery candidate",
                instruction=(
                    f"Repair candidate {sha} for ChangeSet {command.change_set_id}. "
                    f"CI evidence: {command.failure_summary.strip()}"
                ),
                acceptance=command.acceptance,
            ),
            idempotency_key=(f"ci-rework:{command.change_set_id}:{command.repository_id}:{sha}"),
            # The title above is display text; this is the fact consumers read.
            origin=TaskOrigin.REWORK,
        )


class RecoveryConflictTaskCreator:
    """Hand a revert conflict to the repository's Worker as one durable task.

    A revert the platform cannot produce mechanically is a human-scale repair,
    not a retry: the Saga parks the action in ``waiting_worker`` and never
    releases the later rollback actions until this task comes back. The task is
    keyed on the recovery action, so every replay of that action reuses the same
    task instead of flooding the Worker.
    """

    def __init__(self, tasks: TaskAssignmentGateway, topology: ProjectTopologyReader) -> None:
        self._tasks = tasks
        self._topology = topology

    async def create_for_conflict(self, context: RecoveryExecutionContext, error: str) -> UUID:
        change_set = context.change_set
        action = context.action
        if action.repository_id is None:
            raise ValueError("a revert conflict must name the repository that conflicts")
        view = await self._topology.get_view(change_set.project_id)
        if view is None:
            raise ValueError(f"project topology is unavailable: {change_set.project_id}")
        team = next(
            (item for item in view.repository_teams if item.repository_id == action.repository_id),
            None,
        )
        if team is None or not team.worker_agent_ids:
            raise ValueError(
                f"repository {action.repository_id} has no Worker to resolve a revert conflict"
            )
        candidate = next(
            (
                item
                for item in change_set.repositories
                if item.repository_id == action.repository_id
            ),
            None,
        )
        task = await self._tasks.assign(
            AssignTaskCommand(
                organization_id=change_set.organization_id,
                project_id=change_set.project_id,
                repository_id=action.repository_id,
                assigned_by_agent_id=team.leader_agent_id,
                assignee_agent_id=team.worker_agent_ids[0],
                parent_task_id=candidate.task_id if candidate is not None else None,
                title="Resolve delivery revert conflict",
                instruction=(
                    f"Recovery action {action.kind.value} for ChangeSet {change_set.id} "
                    f"could not roll back repository {action.repository_id} automatically. "
                    f"Conflict evidence: {error.strip()}"
                ),
                acceptance=(
                    "The revert applies cleanly on top of the delivery base branch.",
                    "Shared history is not force-reset; the rollback stays a revert commit.",
                ),
            ),
            idempotency_key=f"revert-conflict:{change_set.id}:{action.id}",
        )
        return task.id
