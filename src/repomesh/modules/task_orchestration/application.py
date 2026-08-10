import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from uuid import UUID

from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalReader,
    AgentPrincipalStatus,
    AgentPrincipalView,
    AgentRole,
)
from repomesh.modules.collaboration.contracts import (
    CollaborationGateway,
    CollaborationMessageKind,
    SendCollaborationMessageCommand,
)
from repomesh.modules.project.contracts import ProjectTopologyReader, RepositoryTeamView
from repomesh.modules.task_orchestration.contracts import (
    AssignTaskCommand,
    ExecutionPlanStatus,
    ExecutionPlanView,
    ProjectTaskProgress,
    PublishedTaskPackage,
    ReportTaskCommand,
    SupersedeTaskCommand,
    TaskAssignmentGateway,
    TaskAssignmentPublisher,
    TaskSpecificationAuthor,
    TaskStatus,
    TaskView,
)
from repomesh.modules.task_orchestration.domain import (
    FINAL_TASK_STATUSES,
    ExecutionPlan,
    Task,
    TaskConflict,
    TaskDenied,
    TaskNotFound,
)
from repomesh.modules.task_orchestration.ports import ExecutionPlanStore, TaskStore

_FAILED_TASK_STATUSES = frozenset({TaskStatus.FAILED, TaskStatus.CANCELLED})


class TaskOrchestrator:
    def __init__(
        self,
        directory: AgentPrincipalReader,
        topologies: ProjectTopologyReader,
        tasks: TaskStore,
        collaboration: CollaborationGateway,
        publisher: TaskAssignmentPublisher | None = None,
    ) -> None:
        self._directory = directory
        self._topologies = topologies
        self._tasks = tasks
        self._collaboration = collaboration
        self._publisher = publisher

    async def assign(self, command: AssignTaskCommand, *, idempotency_key: str) -> TaskView:
        key = idempotency_key.strip()
        if not key:
            raise ValueError("idempotency_key is required")
        fingerprint = self._fingerprint(command)
        if existing := await self._tasks.get_by_idempotency_key(key):
            task, previous_fingerprint = existing
            if fingerprint != previous_fingerprint:
                raise TaskConflict("idempotency key was used for a different task")
            await self._deliver_assignment(task, key)
            return task.to_view()

        assigner = await self._required_agent(command.assigned_by_agent_id)
        assignee = await self._required_agent(command.assignee_agent_id)
        if assignee.leader_agent_id != assigner.id:
            raise TaskDenied("tasks can only be assigned to a direct subordinate")
        topology = await self._topologies.get_view(command.project_id)
        if topology is None or topology.organization_id != command.organization_id:
            raise TaskDenied("project topology does not exist")
        self._validate_membership(assigner, assignee, command.repository_id, topology)
        if command.parent_task_id is not None:
            parent = await self._tasks.get(command.parent_task_id)
            if parent is None:
                raise TaskNotFound("parent task does not exist")
            if parent.assignee_agent_id != assigner.id:
                raise TaskDenied("only the parent task assignee can create a child task")
            if (
                parent.project_id != command.project_id
                or parent.repository_id != command.repository_id
            ):
                raise TaskDenied("child task must remain in its parent project and repository")

        task = Task(
            organization_id=command.organization_id,
            project_id=command.project_id,
            repository_id=command.repository_id,
            parent_task_id=command.parent_task_id,
            assigned_by_agent_id=assigner.id,
            assignee_agent_id=assignee.id,
            title=command.title.strip(),
            instruction=command.instruction.strip(),
            acceptance=tuple(item.strip() for item in command.acceptance),
        )
        await self._tasks.add(
            task,
            idempotency_key=key,
            request_fingerprint=fingerprint,
        )
        await self._deliver_assignment(task, key)
        return task.to_view()

    async def _deliver_assignment(self, task: Task, key: str) -> None:
        assignee = await self._required_agent(task.assignee_agent_id)
        published = None
        if assignee.role is AgentRole.WORKER:
            if self._publisher is None:
                raise RuntimeError("Worker task publisher is not configured")
            topology = await self._topologies.get_view(task.project_id)
            team = next(
                (
                    item
                    for item in topology.repository_teams
                    if item.repository_id == task.repository_id
                    and task.assignee_agent_id in item.worker_agent_ids
                ),
                None,
            ) if topology else None
            if team is None or not team.room_id:
                raise TaskDenied("Worker Team runtime is not ready for task publication")
            published = await self._publisher.publish(
                task.to_view(),
                team_name=team.agentteams_team_name,
                room_id=team.room_id,
                assignee_resource_name=assignee.agentteams_resource_name,
                idempotency_key=f"{key}:publication",
            )
        await self._send_assignment(task, key, published)

    async def _send_assignment(
        self, task: Task, key: str, published: PublishedTaskPackage | None
    ) -> None:
        await self._collaboration.send(
            SendCollaborationMessageCommand(
                organization_id=task.organization_id,
                project_id=task.project_id,
                repository_id=task.repository_id,
                task_id=task.id,
                sender_agent_id=task.assigned_by_agent_id,
                recipient_agent_id=task.assignee_agent_id,
                kind=CollaborationMessageKind.TASK_ASSIGNMENT,
                subject=task.title,
                body=self._assignment_body(task, published),
                correlation_id=task.id,
            ),
            idempotency_key=f"{key}:message",
        )

    async def start(self, task_id: UUID, *, agent_id: UUID) -> TaskView:
        task = await self._required_task(task_id)
        if task.assignee_agent_id != agent_id:
            raise TaskDenied("only the assignee can start a task")
        updated = task.start()
        await self._tasks.update(updated, expected_version=task.version)
        return updated.to_view()

    async def report(self, command: ReportTaskCommand, *, idempotency_key: str) -> TaskView:
        await self._required_agent(command.reporter_agent_id)
        task = await self._required_task(command.task_id)
        if task.assignee_agent_id != command.reporter_agent_id:
            raise TaskDenied("only the assignee can report a task")
        if task.status is command.status and task.result_summary == command.summary.strip():
            updated = task
        elif task.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED}:
            if task.status is not command.status or task.result_summary != command.summary.strip():
                raise TaskConflict("a final task cannot be reported with different results")
            updated = task
        else:
            updated = task.report(command.status, command.summary)
            await self._tasks.update(updated, expected_version=task.version)
        kind = (
            CollaborationMessageKind.PROGRESS
            if command.status is TaskStatus.BLOCKED
            else CollaborationMessageKind.TASK_REPORT
        )
        await self._collaboration.send(
            SendCollaborationMessageCommand(
                organization_id=task.organization_id,
                project_id=task.project_id,
                repository_id=task.repository_id,
                task_id=task.id,
                sender_agent_id=task.assignee_agent_id,
                recipient_agent_id=task.assigned_by_agent_id,
                kind=kind,
                subject=f"{task.title}: {updated.status.value}",
                body=command.summary.strip(),
                correlation_id=task.id,
            ),
            idempotency_key=f"{idempotency_key}:message",
        )
        return updated.to_view()

    async def supersede(
        self, command: SupersedeTaskCommand, *, idempotency_key: str
    ) -> TaskView:
        key = idempotency_key.strip()
        if not key:
            raise ValueError("idempotency_key is required")
        task = await self._required_task(command.task_id)
        reason = command.reason.strip()
        # Idempotent replay: same supersede already persisted.
        if (
            task.status is TaskStatus.SUPERSEDED
            and task.result_summary == (f"SUPERSEDED: {reason}" if reason else "SUPERSEDED")
        ):
            return task.to_view()
        updated = task.supersede(
            reason=reason,
            superseded_by=command.superseded_by_task_id,
        )
        await self._tasks.update(updated, expected_version=task.version)
        return updated.to_view()

    async def progress(self, project_id: UUID) -> ProjectTaskProgress:
        tasks = await self._tasks.list_by_project(project_id)
        counts = {status: 0 for status in TaskStatus}
        for task in tasks:
            counts[task.status] += 1
        return ProjectTaskProgress(
            project_id=project_id,
            total=len(tasks),
            assigned=counts[TaskStatus.ASSIGNED],
            in_progress=counts[TaskStatus.IN_PROGRESS],
            blocked=counts[TaskStatus.BLOCKED],
            succeeded=counts[TaskStatus.SUCCEEDED],
            failed=counts[TaskStatus.FAILED],
            cancelled=counts[TaskStatus.CANCELLED],
        )

    async def _required_agent(self, agent_id: UUID) -> AgentPrincipalView:
        profile = await self._directory.get_view(agent_id)
        if profile is None:
            raise TaskDenied(f"agent does not exist: {agent_id}")
        if profile.status is not AgentPrincipalStatus.ACTIVE:
            raise TaskDenied("agent_disabled")
        return profile

    async def _required_task(self, task_id: UUID) -> Task:
        task = await self._tasks.get(task_id)
        if task is None:
            raise TaskNotFound(f"task does not exist: {task_id}")
        return task

    @staticmethod
    def _validate_membership(assigner, assignee, repository_id, topology) -> None:
        if assigner.id == topology.organization_leader_id:
            if not any(
                team.repository_id == repository_id and team.leader_agent_id == assignee.id
                for team in topology.repository_teams
            ):
                raise TaskDenied("repository leader is not assigned to this project repository")
            return
        if not any(
            team.repository_id == repository_id
            and team.leader_agent_id == assigner.id
            and assignee.id in team.worker_agent_ids
            for team in topology.repository_teams
        ):
            raise TaskDenied("worker is not assigned to this project repository team")

    @staticmethod
    def _assignment_body(
        task: Task, published: PublishedTaskPackage | None = None
    ) -> str:
        if published is not None:
            return (
                "A verified RepoMesh task package is ready. Do not edit code directly in this "
                "chat session. Call the MCP tool "
                "repomesh-task-control.start_assigned_task with:\n"
                f'{{"task_id":"{task.id}","worker_agent_id":"{task.assignee_agent_id}"}}\n\n'
                f"Task package: {published.task_path}\n"
                f"Content hash: {published.content_hash}\n"
                "RepoMesh Runner will prepare the isolated workspace, invoke the configured "
                "coding-agent adapter, run verification, and persist the result."
            )
        acceptance = "\n".join(f"- {item}" for item in task.acceptance)
        return (
            f"{task.instruction}\n\nAcceptance criteria:\n{acceptance}\n\n"
            "When blocked or finished, reply with only this JSON object:\n"
            "{\n"
            '  "schema": "repomesh.agent-report.v1",\n'
            f'  "sender_agent_id": "{task.assignee_agent_id}",\n'
            f'  "project_id": "{task.project_id}",\n'
            f'  "task_id": "{task.id}",\n'
            '  "status": "blocked|succeeded|failed",\n'
            '  "summary": "what changed, tests run, or the blocking question"\n'
            "}"
        )

    @staticmethod
    def _fingerprint(command: AssignTaskCommand) -> str:
        encoded = json.dumps(
            asdict(command), sort_keys=True, default=str, separators=(",", ":")
        ).encode()
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


class TaskExecutionState:
    """Persist Worker execution transitions without depending on chat delivery."""

    def __init__(self, directory: AgentPrincipalReader, tasks: TaskStore) -> None:
        self._directory = directory
        self._tasks = tasks

    async def start(self, task_id: UUID, *, agent_id: UUID) -> TaskView:
        task = await self._required(task_id, agent_id)
        if task.status is TaskStatus.IN_PROGRESS:
            return task.to_view()
        updated = task.start()
        await self._tasks.update(updated, expected_version=task.version)
        return updated.to_view()

    async def block(self, task_id: UUID, *, agent_id: UUID, summary: str) -> TaskView:
        task = await self._required(task_id, agent_id)
        normalized = summary.strip()
        if task.status is TaskStatus.BLOCKED and task.result_summary == normalized:
            return task.to_view()
        updated = task.report(TaskStatus.BLOCKED, normalized)
        await self._tasks.update(updated, expected_version=task.version)
        return updated.to_view()

    async def _required(self, task_id: UUID, agent_id: UUID) -> Task:
        agent = await self._directory.get_view(agent_id)
        if agent is None or agent.status is not AgentPrincipalStatus.ACTIVE:
            raise TaskDenied("worker agent is missing or disabled")
        task = await self._tasks.get(task_id)
        if task is None:
            raise TaskNotFound(f"task does not exist: {task_id}")
        if task.assignee_agent_id != agent_id:
            raise TaskDenied("only the assignee can change execution state")
        return task


class DecomposeRepositoryTask:
    """Expand a repository task into the Worker tasks that execute it."""

    def __init__(
        self,
        directory: AgentPrincipalReader,
        topologies: ProjectTopologyReader,
        tasks: TaskStore,
        assigner: TaskAssignmentGateway,
        spec_author: TaskSpecificationAuthor | None = None,
    ) -> None:
        self._directory = directory
        self._topologies = topologies
        self._tasks = tasks
        self._assigner = assigner
        self._spec_author = spec_author

    async def execute(
        self,
        repository_task_id: UUID,
        *,
        idempotency_key: str,
        tests: tuple[str, ...] = (),
    ) -> tuple[TaskView, ...]:
        key = idempotency_key.strip()
        if not key:
            raise ValueError("idempotency_key is required")
        task = await self._tasks.get(repository_task_id)
        if task is None:
            raise TaskNotFound(f"task does not exist: {repository_task_id}")
        leader = await self._directory.get_view(task.assignee_agent_id)
        if leader is None or leader.status is not AgentPrincipalStatus.ACTIVE:
            raise TaskDenied("repository leader is missing or disabled")
        if leader.role is not AgentRole.REPOSITORY_LEADER:
            raise TaskDenied("only a repository leader task can be decomposed")
        team = await self.repository_team(task.project_id, task.repository_id)
        if team.leader_agent_id != leader.id:
            raise TaskDenied("repository team is led by another agent")
        if not team.worker_agent_ids:
            raise TaskDenied("repository team has no worker to execute the task")

        # MVP granularity: one Worker task per repository task.
        worker_agent_id = team.worker_agent_ids[0]
        children = await self._tasks.list_by_parent(task.id)
        in_flight = any(
            child.assignee_agent_id == worker_agent_id
            and child.status not in FINAL_TASK_STATUSES
            for child in children
        )
        if in_flight:
            # A replay must still heal a Worker task whose execution permit is missing.
            views = tuple(child.to_view() for child in children)
            for child, view in zip(children, views, strict=True):
                if child.status not in FINAL_TASK_STATUSES:
                    await self._ensure_specification(view, tests=tests, key=key)
            return views

        worker_task = await self._assigner.assign(
            AssignTaskCommand(
                organization_id=task.organization_id,
                project_id=task.project_id,
                repository_id=task.repository_id,
                assigned_by_agent_id=task.assignee_agent_id,
                assignee_agent_id=worker_agent_id,
                title=task.title,
                instruction=task.instruction,
                acceptance=task.acceptance,
                parent_task_id=task.id,
            ),
            idempotency_key=f"{key}:worker:{worker_agent_id}",
        )
        await self._ensure_specification(worker_task, tests=tests, key=key)
        return (worker_task,)

    async def _ensure_specification(
        self, worker_task: TaskView, *, tests: tuple[str, ...], key: str
    ) -> None:
        """Produce the execution permit in the same motion as the executable task."""
        if self._spec_author is None:
            return
        worker = await self._directory.get_view(worker_task.assignee_agent_id)
        if worker is None or worker.status is not AgentPrincipalStatus.ACTIVE:
            raise TaskDenied("worker agent is missing or disabled")
        await self._spec_author.ensure_approved(
            worker_task,
            allowed_paths=tuple(worker.responsibility_paths) or ("**",),
            tests=tests,
            idempotency_key=f"{key}:spec:{worker_task.assignee_agent_id}",
        )

    async def repository_team(
        self, project_id: UUID, repository_id: UUID
    ) -> RepositoryTeamView:
        topology = await self._topologies.get_view(project_id)
        team = (
            next(
                (
                    item
                    for item in topology.repository_teams
                    if item.repository_id == repository_id
                ),
                None,
            )
            if topology is not None
            else None
        )
        if team is None:
            raise TaskDenied("project repository team does not exist")
        return team


class AdvanceExecutionPlan:
    """Drive an execution plan batch by batch as repository tasks reach a result."""

    def __init__(
        self,
        plans: ExecutionPlanStore,
        tasks: TaskStore,
        assigner: TaskAssignmentGateway,
        decomposer: DecomposeRepositoryTask,
        on_plan_completed: Callable[[ExecutionPlanView], Awaitable[None]] | None = None,
    ) -> None:
        self._plans = plans
        self._tasks = tasks
        self._assigner = assigner
        self._decomposer = decomposer
        self._on_plan_completed = on_plan_completed

    async def start(self, plan: ExecutionPlan, *, idempotency_key: str) -> ExecutionPlanView:
        key = idempotency_key.strip()
        if not key:
            raise ValueError("idempotency_key is required")
        if existing := await self._plans.get_by_idempotency_key(key):
            return existing.to_view()
        await self._plans.add(plan, idempotency_key=key)
        assigned = await self._assign_batch(plan, plan.current_batch_index, key_prefix=key)
        return assigned.to_view()

    async def on_task_terminal(self, task_id: UUID) -> None:
        task = await self._tasks.get(task_id)
        if task is None:
            return
        leader_task = task
        if task.parent_task_id is not None:
            parent = await self._tasks.get(task.parent_task_id)
            if parent is None:
                return
            leader_task = await self._roll_up(parent)
        if leader_task.status not in FINAL_TASK_STATUSES:
            return
        plan = await self._plans.find_by_leader_task(leader_task.id)
        if plan is None:
            return
        if plan.status is ExecutionPlanStatus.COMPLETED:
            await self._notify_plan_completed(plan.to_view())
            return
        if plan.status is not ExecutionPlanStatus.IN_PROGRESS:
            return
        if leader_task.id not in plan.leader_task_ids(plan.current_batch_index):
            return
        if leader_task.status is not TaskStatus.SUCCEEDED:
            await self._settle(plan, plan.fail())
            return
        if not await self._batch_succeeded(plan):
            return
        if plan.is_last_batch:
            completed = plan.complete()
            if await self._settle(plan, completed):
                await self._notify_plan_completed(completed.to_view())
            return
        advanced = plan.advance()
        if not await self._settle(plan, advanced):
            return
        await self._assign_batch(
            advanced, advanced.current_batch_index, key_prefix=str(plan.id)
        )

    async def _assign_batch(
        self, plan: ExecutionPlan, batch_index: int, *, key_prefix: str
    ) -> ExecutionPlan:
        leader_task_ids: list[UUID] = []
        for planned in plan.batches[batch_index]:
            team = await self._decomposer.repository_team(
                plan.project_id, planned.repository_id
            )
            leader_task = await self._assigner.assign(
                AssignTaskCommand(
                    organization_id=plan.organization_id,
                    project_id=plan.project_id,
                    repository_id=planned.repository_id,
                    assigned_by_agent_id=plan.created_by_agent_id,
                    assignee_agent_id=team.leader_agent_id,
                    title=planned.title,
                    instruction=planned.instruction,
                    acceptance=planned.acceptance,
                ),
                idempotency_key=f"{key_prefix}:b{batch_index}:{planned.repository_id}",
            )
            leader_task_ids.append(leader_task.id)
        assigned = plan.with_leader_tasks(batch_index, tuple(leader_task_ids))
        await self._plans.update(assigned, expected_version=plan.version)
        for planned, leader_task_id in zip(
            assigned.batches[batch_index], leader_task_ids, strict=True
        ):
            await self._decomposer.execute(
                leader_task_id,
                idempotency_key=(
                    f"{key_prefix}:b{batch_index}:{planned.repository_id}:decompose"
                ),
                tests=planned.tests,
            )
        return assigned

    async def _roll_up(self, parent: Task) -> Task:
        if parent.status in FINAL_TASK_STATUSES:
            return parent
        children = await self._tasks.list_by_parent(parent.id)
        if not children:
            return parent
        failed = tuple(child for child in children if child.status in _FAILED_TASK_STATUSES)
        if failed:
            updated = parent.report(
                TaskStatus.FAILED,
                f"{len(failed)} of {len(children)} worker tasks did not succeed.",
            )
        elif all(child.status is TaskStatus.SUCCEEDED for child in children):
            updated = parent.report(
                TaskStatus.SUCCEEDED,
                f"All {len(children)} worker tasks succeeded.",
            )
        else:
            return parent
        try:
            await self._tasks.update(updated, expected_version=parent.version)
        except TaskConflict:
            # Another report already moved the parent, so trust the persisted state.
            current = await self._tasks.get(parent.id)
            return current if current is not None else parent
        return updated

    async def _batch_succeeded(self, plan: ExecutionPlan) -> bool:
        for planned in plan.batches[plan.current_batch_index]:
            if planned.leader_task_id is None:
                return False
            leader_task = await self._tasks.get(planned.leader_task_id)
            if leader_task is None or leader_task.status is not TaskStatus.SUCCEEDED:
                return False
        return True

    async def _settle(self, plan: ExecutionPlan, updated: ExecutionPlan) -> bool:
        try:
            await self._plans.update(updated, expected_version=plan.version)
        except TaskConflict:
            # Another caller already moved this plan forward.
            return False
        return True

    async def _notify_plan_completed(self, plan: ExecutionPlanView) -> None:
        if self._on_plan_completed is not None:
            await self._on_plan_completed(plan)
