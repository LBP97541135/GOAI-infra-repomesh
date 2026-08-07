"""Tests for the execution-plan observation endpoint."""

import asyncio
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from repomesh.bootstrap import create_app
from repomesh.bootstrap.container import ApplicationContainer
from repomesh.modules.task_orchestration.contracts import TaskStatus
from repomesh.modules.task_orchestration.domain import (
    ExecutionPlan,
    PlannedRepositoryTask,
    Task,
)


def _task(
    *,
    organization_id: UUID,
    project_id: UUID,
    repository_id: UUID,
    assigned_by_agent_id: UUID,
    assignee_agent_id: UUID,
    status: TaskStatus,
    parent_task_id: UUID | None = None,
) -> Task:
    return Task(
        organization_id=organization_id,
        project_id=project_id,
        repository_id=repository_id,
        parent_task_id=parent_task_id,
        assigned_by_agent_id=assigned_by_agent_id,
        assignee_agent_id=assignee_agent_id,
        title="Implement changes for ts-order-service",
        instruction="Implement the approved scope.",
        acceptance=("Tests pass",),
        status=status,
    )


def test_observe_execution_plan_reports_task_status(
    application_container: ApplicationContainer,
) -> None:
    organization_id = uuid4()
    project_id = uuid4()
    organization_leader_id = uuid4()
    repository_leader_id = uuid4()
    worker_id = uuid4()
    first_repository_id = uuid4()
    second_repository_id = uuid4()

    leader_task = _task(
        organization_id=organization_id,
        project_id=project_id,
        repository_id=first_repository_id,
        assigned_by_agent_id=organization_leader_id,
        assignee_agent_id=repository_leader_id,
        status=TaskStatus.IN_PROGRESS,
    )
    worker_task = _task(
        organization_id=organization_id,
        project_id=project_id,
        repository_id=first_repository_id,
        assigned_by_agent_id=repository_leader_id,
        assignee_agent_id=worker_id,
        status=TaskStatus.SUCCEEDED,
        parent_task_id=leader_task.id,
    )
    plan = ExecutionPlan(
        organization_id=organization_id,
        project_id=project_id,
        created_by_agent_id=organization_leader_id,
        batches=(
            (
                PlannedRepositoryTask(
                    repository_id=first_repository_id,
                    title="Implement changes for ts-order-service",
                    instruction="Implement the approved scope.",
                    acceptance=("Tests pass",),
                    leader_task_id=leader_task.id,
                ),
            ),
            (
                PlannedRepositoryTask(
                    repository_id=second_repository_id,
                    title="Implement changes for ts-billing-service",
                    instruction="Adapt to the new pricing contract.",
                    acceptance=("Tests pass",),
                ),
            ),
        ),
    )

    async def seed() -> None:
        tasks = application_container.task_store
        await tasks.add(
            leader_task, idempotency_key="leader-task", request_fingerprint="sha256:leader"
        )
        await tasks.add(
            worker_task, idempotency_key="worker-task", request_fingerprint="sha256:worker"
        )
        await application_container.execution_plan_store().add(plan, idempotency_key="plan")

    asyncio.run(seed())

    with TestClient(create_app(application_container)) as client:
        response = client.get(f"/api/v1/bridge/plans/{plan.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["plan_id"] == str(plan.id)
    assert body["status"] == "in_progress"
    assert body["current_batch_index"] == 0
    assert body["batches"] == [
        [
            {
                "repository_id": str(first_repository_id),
                "leader_task_id": str(leader_task.id),
                "leader_status": "in_progress",
                "worker_tasks": [{"task_id": str(worker_task.id), "status": "succeeded"}],
            }
        ],
        [
            {
                "repository_id": str(second_repository_id),
                "leader_task_id": None,
                "leader_status": None,
                "worker_tasks": [],
            }
        ],
    ]


def test_observe_unknown_execution_plan_returns_404(
    application_container: ApplicationContainer,
) -> None:
    with TestClient(create_app(application_container)) as client:
        response = client.get(f"/api/v1/bridge/plans/{uuid4()}")

    assert response.status_code == 404
