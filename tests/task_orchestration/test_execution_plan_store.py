from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio

from repomesh.modules.task_orchestration.contracts import ExecutionPlanStatus, TaskStatus
from repomesh.modules.task_orchestration.domain import (
    ExecutionPlan,
    PlannedRepositoryTask,
    Task,
    TaskConflict,
)
from repomesh.modules.task_orchestration.infrastructure import (
    ExecutionPlanRecord,
    PostgresExecutionPlanStore,
    PostgresTaskStore,
)
from repomesh.persistence import Database
from repomesh.persistence.base import ALL_SCHEMAS


@pytest_asyncio.fixture
async def database(tmp_path: object) -> Database:
    database_path = tmp_path.joinpath("repomesh-execution-plans.db")
    instance = Database(
        f"sqlite+aiosqlite:///{database_path}",
        schema_translate_map={schema: None for schema in ALL_SCHEMAS},
    )
    await instance.create_all_for_tests()
    yield instance
    await instance.dispose()


def build_plan(repository_id) -> ExecutionPlan:
    return ExecutionPlan(
        organization_id=uuid4(),
        project_id=uuid4(),
        created_by_agent_id=uuid4(),
        batches=(
            (
                PlannedRepositoryTask(
                    repository_id=repository_id,
                    title="Deliver pricing",
                    instruction="Implement the approved scope.",
                    acceptance=("Tests pass",),
                    tests=("uv run pytest -q tests/pricing",),
                ),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_plan_store_persists_batches_and_indexes_leader_tasks(
    database: Database,
) -> None:
    store = PostgresExecutionPlanStore(database)
    repository_id = uuid4()
    leader_task_id = uuid4()
    plan = build_plan(repository_id)
    await store.add(plan, idempotency_key="persistent-plan")

    assigned = plan.with_leader_tasks(0, (leader_task_id,))
    await store.update(assigned, expected_version=plan.version)

    stored = await store.get(plan.id)
    replay = await store.get_by_idempotency_key("persistent-plan")
    bound = await store.find_by_leader_task(leader_task_id)
    assert stored == assigned
    assert replay == assigned
    assert bound == assigned
    assert stored.batches[0][0].repository_id == repository_id
    assert stored.batches[0][0].tests == ("uv run pytest -q tests/pricing",)
    assert await store.find_by_leader_task(uuid4()) is None

    with pytest.raises(TaskConflict, match="version changed"):
        await store.update(assigned.complete(), expected_version=plan.version)


@pytest.mark.asyncio
async def test_plan_store_reads_batches_persisted_before_verification_commands(
    database: Database,
) -> None:
    store = PostgresExecutionPlanStore(database)
    plan_id = uuid4()
    now = datetime.now(UTC)
    async with database.transaction() as session:
        session.add(
            ExecutionPlanRecord(
                id=plan_id,
                organization_id=uuid4(),
                project_id=uuid4(),
                created_by_agent_id=uuid4(),
                status=ExecutionPlanStatus.IN_PROGRESS.value,
                current_batch_index=0,
                batches=[
                    [
                        {
                            "repository_id": str(uuid4()),
                            "title": "Deliver pricing",
                            "instruction": "Implement the approved scope.",
                            "acceptance": ["Tests pass"],
                            "leader_task_id": None,
                        }
                    ]
                ],
                idempotency_key="legacy-plan",
                version=1,
                created_at=now,
                updated_at=now,
            )
        )

    stored = await store.get(plan_id)

    assert stored is not None
    assert stored.batches[0][0].tests == ()


@pytest.mark.asyncio
async def test_task_store_lists_children_of_a_parent_task(database: Database) -> None:
    store = PostgresTaskStore(database)
    parent = Task(
        organization_id=uuid4(),
        project_id=uuid4(),
        repository_id=uuid4(),
        assigned_by_agent_id=uuid4(),
        assignee_agent_id=uuid4(),
        title="Repository task",
        instruction="Coordinate the change.",
        acceptance=("Worker result is collected",),
    )
    child = Task(
        organization_id=parent.organization_id,
        project_id=parent.project_id,
        repository_id=parent.repository_id,
        parent_task_id=parent.id,
        assigned_by_agent_id=parent.assignee_agent_id,
        assignee_agent_id=uuid4(),
        title="Worker task",
        instruction="Implement the change.",
        acceptance=("Tests pass",),
    )
    await store.add(parent, idempotency_key="parent", request_fingerprint="sha256:parent")
    await store.add(child, idempotency_key="child", request_fingerprint="sha256:child")

    children = await store.list_by_parent(parent.id)

    assert children == (child,)
    assert children[0].status is TaskStatus.ASSIGNED
    assert await store.list_by_parent(child.id) == ()


@pytest.mark.asyncio
async def test_plan_store_rejects_a_reused_idempotency_key(database: Database) -> None:
    store = PostgresExecutionPlanStore(database)
    await store.add(build_plan(uuid4()), idempotency_key="single-plan")

    with pytest.raises(TaskConflict, match="already exists"):
        await store.add(build_plan(uuid4()), idempotency_key="single-plan")


@pytest.mark.asyncio
async def test_completed_plan_keeps_its_status(database: Database) -> None:
    store = PostgresExecutionPlanStore(database)
    plan = build_plan(uuid4())
    await store.add(plan, idempotency_key="completed-plan")

    completed = plan.complete()
    await store.update(completed, expected_version=plan.version)

    stored = await store.get(plan.id)
    assert stored is not None and stored.status is ExecutionPlanStatus.COMPLETED
