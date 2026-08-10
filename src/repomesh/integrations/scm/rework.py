from repomesh.modules.task_orchestration.contracts import (
    AssignTaskCommand,
    CreateCIReworkTaskCommand,
    TaskAssignmentGateway,
    TaskView,
)


class CIReworkTaskCreator:
    """Create an idempotent Worker task for a failed candidate revision."""

    def __init__(self, tasks: TaskAssignmentGateway) -> None:
        self._tasks = tasks

    async def create(self, command: CreateCIReworkTaskCommand) -> TaskView:
        sha = command.failed_head_sha.strip().lower()
        if len(sha) != 40 or any(char not in "0123456789abcdef" for char in sha):
            raise ValueError("failed_head_sha must be a full Git object id")
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
        )
