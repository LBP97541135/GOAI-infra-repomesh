from uuid import uuid4

from repomesh.modules.task_orchestration.contracts import TaskStatus
from repomesh.modules.task_orchestration.domain import Task


def test_blocked_task_can_restart_and_forgets_old_blocker() -> None:
    task = Task(
        organization_id=uuid4(),
        project_id=uuid4(),
        repository_id=uuid4(),
        assigned_by_agent_id=uuid4(),
        assignee_agent_id=uuid4(),
        title="Fix pricing",
        instruction="Implement task spec",
        acceptance=("tests pass",),
    )
    blocked = task.report(TaskStatus.BLOCKED, "repository temporarily unavailable")

    restarted = blocked.start()

    assert restarted.status is TaskStatus.IN_PROGRESS
    assert restarted.result_summary is None
