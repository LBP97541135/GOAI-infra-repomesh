from dataclasses import dataclass
from uuid import UUID

from repomesh.modules.specification import SpecificationService
from repomesh.modules.specification.contracts import (
    ApproveSpecificationCommand,
    CreateSpecificationCommand,
    SpecificationKind,
    SpecificationStatus,
    SubmitSpecificationCommand,
)
from repomesh.modules.task_orchestration import TaskOrchestrator
from repomesh.modules.task_orchestration.contracts import (
    AssignTaskCommand,
    TaskExecutionMode,
    TaskView,
)


@dataclass(frozen=True, slots=True)
class CreateGovernedWorkerTaskCommand:
    organization_id: UUID
    project_id: UUID
    repository_id: UUID
    parent_task_id: UUID
    leader_agent_id: UUID
    worker_agent_id: UUID
    title: str
    goal: str
    acceptance: tuple[str, ...]
    constraints: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    interface_changes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GovernedWorkerTaskResult:
    task: TaskView
    specification_id: UUID


class CreateGovernedWorkerTask:
    """Freeze a Task Spec before publishing its governed Worker assignment."""

    def __init__(
        self,
        tasks: TaskOrchestrator,
        specifications: SpecificationService,
    ) -> None:
        self._tasks = tasks
        self._specifications = specifications

    async def execute(
        self,
        command: CreateGovernedWorkerTaskCommand,
        *,
        idempotency_key: str,
    ) -> GovernedWorkerTaskResult:
        specification_id: UUID | None = None

        async def prepare(task: TaskView) -> None:
            nonlocal specification_id
            specification = await self._specifications.create(
                CreateSpecificationCommand(
                    organization_id=command.organization_id,
                    project_id=command.project_id,
                    repository_id=command.repository_id,
                    task_id=task.id,
                    kind=SpecificationKind.TASK,
                    title=command.title,
                    created_by_agent_id=command.leader_agent_id,
                    goal=command.goal,
                    acceptance=command.acceptance,
                    constraints=command.constraints,
                    tests=command.tests,
                    dependencies=command.dependencies,
                    allowed_paths=command.allowed_paths,
                    interface_changes=command.interface_changes,
                ),
                idempotency_key=f"{idempotency_key}:spec",
            )
            if specification.status is SpecificationStatus.DRAFT:
                specification = await self._specifications.submit(
                    SubmitSpecificationCommand(
                        specification_id=specification.id,
                        actor_agent_id=command.leader_agent_id,
                        expected_revision=specification.revision,
                    )
                )
            if specification.status is SpecificationStatus.IN_REVIEW:
                specification = await self._specifications.approve(
                    ApproveSpecificationCommand(
                        specification_id=specification.id,
                        actor_agent_id=command.leader_agent_id,
                        expected_revision=specification.revision,
                        freeze=True,
                    )
                )
            if specification.status is not SpecificationStatus.FROZEN:
                raise ValueError("Worker Task Specification must be frozen before assignment")
            specification_id = specification.id

        task = await self._tasks.assign(
            AssignTaskCommand(
                organization_id=command.organization_id,
                project_id=command.project_id,
                repository_id=command.repository_id,
                parent_task_id=command.parent_task_id,
                assigned_by_agent_id=command.leader_agent_id,
                assignee_agent_id=command.worker_agent_id,
                title=command.title,
                instruction=command.goal,
                acceptance=command.acceptance,
                execution_mode=TaskExecutionMode.GOVERNED_WORKER,
            ),
            idempotency_key=f"{idempotency_key}:task",
            prepare=prepare,
        )
        if specification_id is None:
            raise RuntimeError("Worker Task Specification was not prepared")
        return GovernedWorkerTaskResult(task=task, specification_id=specification_id)
