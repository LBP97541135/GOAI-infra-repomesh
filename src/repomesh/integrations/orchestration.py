"""Cross-module adapters used to compose the RepoMesh execution plane."""

from collections.abc import Sequence
from uuid import UUID

from repomesh.modules.change_orchestration import StartedExecutionPlan
from repomesh.modules.delivery.contracts import RepositoryDeliveryStatus
from repomesh.modules.specification import (
    ApproveSpecificationCommand,
    CreateSpecificationCommand,
    PublishSpecificationContextCommand,
    SpecificationKind,
    SpecificationService,
    SpecificationStatus,
    SubmitSpecificationCommand,
)
from repomesh.modules.specification.ports import SpecificationStore
from repomesh.modules.task_orchestration import (
    AdvanceExecutionPlan,
    ExecutionPlan,
    PlannedRepositoryTask,
)
from repomesh.modules.task_orchestration.contracts import (
    DeliveryGatedRepositoryView,
    ExecutionPlanView,
    PlannedRepositoryTaskView,
    TaskView,
)
from repomesh.modules.task_orchestration.ports import TaskStore


class AdvanceExecutionPlanStarter:
    """Translate a published change plan into a task-orchestration aggregate."""

    def __init__(self, advancer: AdvanceExecutionPlan, tasks: TaskStore) -> None:
        self._advancer = advancer
        self._tasks = tasks

    async def start_plan(
        self,
        *,
        organization_id: UUID,
        project_id: UUID,
        created_by_agent_id: UUID,
        batches: Sequence[Sequence[PlannedRepositoryTaskView]],
        idempotency_key: str,
    ) -> StartedExecutionPlan:
        plan = ExecutionPlan(
            organization_id=organization_id,
            project_id=project_id,
            created_by_agent_id=created_by_agent_id,
            batches=tuple(
                tuple(
                    PlannedRepositoryTask(
                        repository_id=item.repository_id,
                        title=item.title,
                        instruction=item.instruction,
                        acceptance=item.acceptance,
                        tests=item.tests,
                        test_paths=item.test_paths,
                    )
                    for item in batch
                )
                for batch in batches
            ),
        )
        view = await self._advancer.start(plan, idempotency_key=idempotency_key)
        return StartedExecutionPlan(plan=view, tasks=await self._assigned_tasks(view))

    async def _assigned_tasks(self, view: ExecutionPlanView) -> tuple[TaskView, ...]:
        assigned: list[TaskView] = []
        for batch in view.batches:
            for planned in batch:
                if planned.leader_task_id is None:
                    continue
                leader = await self._tasks.get(planned.leader_task_id)
                if leader is None:
                    continue
                assigned.append(leader.to_view())
                assigned.extend(
                    child.to_view() for child in await self._tasks.list_by_parent(leader.id)
                )
        return tuple(assigned)


class ApprovedTaskSpecificationAuthor:
    """Create and publish the approved Task Spec required by a Worker run."""

    def __init__(self, specifications: SpecificationService, stored: SpecificationStore) -> None:
        self._specifications = specifications
        self._stored = stored

    async def ensure_approved(
        self,
        task: TaskView,
        *,
        allowed_paths: tuple[str, ...],
        tests: tuple[str, ...],
        idempotency_key: str,
    ) -> None:
        if await self._has_permit(task):
            return
        owner_agent_id = task.assigned_by_agent_id
        current = await self._specifications.create(
            CreateSpecificationCommand(
                organization_id=task.organization_id,
                project_id=task.project_id,
                repository_id=task.repository_id,
                task_id=task.id,
                kind=SpecificationKind.TASK,
                title=f"Task spec: {task.title}".strip()[:500],
                created_by_agent_id=owner_agent_id,
                goal=task.instruction,
                acceptance=task.acceptance,
                tests=self._clean(tests),
                allowed_paths=self._clean(allowed_paths),
            ),
            idempotency_key=idempotency_key,
        )
        if current.status is SpecificationStatus.DRAFT:
            current = await self._specifications.submit(
                SubmitSpecificationCommand(current.id, owner_agent_id, current.revision)
            )
        if current.status is SpecificationStatus.IN_REVIEW:
            current = await self._specifications.approve(
                ApproveSpecificationCommand(
                    current.id, owner_agent_id, current.revision, freeze=True
                )
            )
            await self._specifications.publish_to_context(
                PublishSpecificationContextCommand(current.id, owner_agent_id)
            )

    async def _has_permit(self, task: TaskView) -> bool:
        return any(
            item.kind is SpecificationKind.TASK
            and item.task_id == task.id
            and item.repository_id == task.repository_id
            and item.status in {SpecificationStatus.APPROVED, SpecificationStatus.FROZEN}
            for item in await self._stored.list_by_project(task.project_id)
        )

    @staticmethod
    def _clean(values: Sequence[str]) -> tuple[str, ...]:
        return tuple(value.strip() for value in values if value.strip())


class DeliveryStateAdapter:
    """Project delivery records into the batch scheduler's merge-state port."""

    def __init__(self, delivery) -> None:  # noqa: ANN001
        self._delivery = delivery

    async def repository_states(self, project_id: UUID) -> tuple[DeliveryGatedRepositoryView, ...]:
        by_repository: dict[UUID, DeliveryGatedRepositoryView] = {}
        for change_set in await self._delivery.find_by_project(project_id):
            for repository in change_set.repositories:
                merged = repository.status is RepositoryDeliveryStatus.MERGED
                previous = by_repository.get(repository.repository_id)
                if previous is None or (merged and not previous.merged):
                    by_repository[repository.repository_id] = DeliveryGatedRepositoryView(
                        repository_id=repository.repository_id,
                        merged=merged,
                    )
        return tuple(by_repository.values())
