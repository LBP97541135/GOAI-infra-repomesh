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
from repomesh.modules.project.contracts import ProjectTopologyReader
from repomesh.modules.task_orchestration.contracts import (
    AssignTaskCommand,
    ProjectTaskProgress,
    PublishedTaskPackage,
    ReportTaskCommand,
    TaskAssignmentPublisher,
    TaskExecutionMode,
    TaskStatus,
    TaskView,
)
from repomesh.modules.task_orchestration.domain import (
    Task,
    TaskConflict,
    TaskDenied,
    TaskNotFound,
)
from repomesh.modules.task_orchestration.ports import TaskStore


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

    async def assign(
        self,
        command: AssignTaskCommand,
        *,
        idempotency_key: str,
        prepare: Callable[[TaskView], Awaitable[None]] | None = None,
    ) -> TaskView:
        key = idempotency_key.strip()
        if not key:
            raise ValueError("idempotency_key is required")
        fingerprint = self._fingerprint(command)
        if existing := await self._tasks.get_by_idempotency_key(key):
            task, previous_fingerprint = existing
            if fingerprint != previous_fingerprint:
                raise TaskConflict("idempotency key was used for a different task")
            if prepare is not None:
                await prepare(task.to_view())
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
        execution_mode = command.execution_mode or (
            TaskExecutionMode.GOVERNED_WORKER
            if assignee.role is AgentRole.WORKER
            else TaskExecutionMode.COORDINATION
        )
        self._validate_execution_mode(execution_mode, assignee)
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
            execution_mode=execution_mode,
        )
        await self._tasks.add(
            task,
            idempotency_key=key,
            request_fingerprint=fingerprint,
        )
        if prepare is not None:
            await prepare(task.to_view())
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
                "chat session. First inspect the package and call "
                "repomesh-task-control.assess_assigned_task. Only after a ready decision may "
                "you call repomesh-task-control.start_assigned_task with:\n"
                f'{{"task_id":"{task.id}","worker_agent_id":"{task.assignee_agent_id}"}}\n\n'
                f"Task package: {published.task_path}\n"
                f"Content hash: {published.content_hash}\n"
                "RepoMesh Runner will prepare the isolated workspace, invoke the configured "
                "coding-agent adapter, run verification, and persist the result."
            )
        if task.execution_mode is TaskExecutionMode.DIRECT_RUN:
            acceptance = "\n".join(f"- {item}" for item in task.acceptance)
            return (
                f"{task.instruction}\n\nAcceptance criteria:\n{acceptance}\n\n"
                "This task uses direct_run. Review the approved Task Specification, then invoke "
                "the RepoMesh direct-run API. The Runner still enforces context, path, test, and "
                "commit controls; no Worker conversation is created."
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
    def _validate_execution_mode(
        execution_mode: TaskExecutionMode, assignee: AgentPrincipalView
    ) -> None:
        if execution_mode is TaskExecutionMode.GOVERNED_WORKER:
            if assignee.role is not AgentRole.WORKER:
                raise TaskDenied("governed_worker tasks must be assigned to a Worker")
            return
        if execution_mode is TaskExecutionMode.DIRECT_RUN:
            if assignee.role is not AgentRole.REPOSITORY_LEADER:
                raise TaskDenied("direct_run tasks must be assigned to a Repository Leader")
            return
        if assignee.role is AgentRole.WORKER:
            raise TaskDenied("Worker tasks use governed_worker mode by default")

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
