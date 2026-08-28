from types import SimpleNamespace
from uuid import uuid4

import pytest

from repomesh.modules.task_orchestration import (
    AppendPlanTaskInput,
    AppendPlanTasksCommand,
    DynamicPlanRevisionService,
    ExecutionPlan,
    PlannedRepositoryTask,
    PostgresExecutionPlanRevisionStore,
    PostgresExecutionPlanStore,
    TaskConflict,
)
from repomesh.persistence import Database
from repomesh.persistence.base import ALL_SCHEMAS


def _planned(repository_id, *, depends_on=()):
    return PlannedRepositoryTask(
        repository_id=repository_id, title=f"Task {repository_id}",
        instruction="Implement discovered work", acceptance=("tests pass",),
        depends_on=depends_on,
    )


def test_append_preserves_existing_batches_and_topologically_groups_new_work() -> None:
    existing = uuid4()
    first_new = uuid4()
    second_new = uuid4()
    plan = ExecutionPlan(uuid4(), uuid4(), uuid4(), ((_planned(existing),),))

    revised = plan.append_tasks(
        (_planned(second_new, depends_on=(first_new,)), _planned(first_new, depends_on=(existing,)))
    )

    assert revised.batches[0] == plan.batches[0]
    assert [tuple(item.repository_id for item in batch) for batch in revised.batches[1:]] == [
        (first_new,), (second_new,)
    ]
    assert revised.version == plan.version + 1


def test_append_refuses_existing_repository_missing_dependency_and_cycle() -> None:
    existing = uuid4()
    plan = ExecutionPlan(uuid4(), uuid4(), uuid4(), ((_planned(existing),),))
    with pytest.raises(TaskConflict, match="full replan"):
        plan.append_tasks((_planned(existing),))
    with pytest.raises(TaskConflict, match="not in the plan"):
        plan.append_tasks((_planned(uuid4(), depends_on=(uuid4(),)),))
    first, second = uuid4(), uuid4()
    with pytest.raises(TaskConflict, match="cycle"):
        plan.append_tasks(
            (_planned(first, depends_on=(second,)), _planned(second, depends_on=(first,)))
        )


class _Topology:
    def __init__(self, project_id, repositories):
        self.view = SimpleNamespace(
            project_id=project_id,
            repository_teams=tuple(
                SimpleNamespace(repository_id=repository_id) for repository_id in repositories
            ),
        )

    async def get_view(self, project_id):
        return self.view if project_id == self.view.project_id else None


@pytest.mark.asyncio
async def test_preview_is_side_effect_free_and_commit_is_idempotent(tmp_path) -> None:
    database = Database(
        f"sqlite+aiosqlite:///{tmp_path / 'dynamic-plan.db'}",
        schema_translate_map={schema: None for schema in ALL_SCHEMAS},
    )
    await database.create_all_for_tests()
    plans = PostgresExecutionPlanStore(database)
    revisions = PostgresExecutionPlanRevisionStore(database)
    existing, appended = uuid4(), uuid4()
    plan = ExecutionPlan(uuid4(), uuid4(), uuid4(), ((_planned(existing),),))
    await plans.add(plan, idempotency_key=f"plan:{plan.id}")
    service = DynamicPlanRevisionService(
        plans, revisions, _Topology(plan.project_id, (existing, appended))
    )
    base = dict(
        plan_id=plan.id, expected_plan_version=plan.version,
        actor_agent_id=plan.created_by_agent_id, reason="discovered compatibility dependency",
        items=(AppendPlanTaskInput(
            repository_id=appended, title="Compatibility adapter",
            instruction="Add the adapter", acceptance=("integration passes",),
            depends_on=(existing,),
        ),),
    )
    preview = await service.append(
        AppendPlanTasksCommand(**base, mode="preview"), idempotency_key="append-1"
    )
    assert preview.status == "preview"
    assert (await plans.get(plan.id)).version == plan.version
    assert await revisions.history(plan.id) == ()

    command = AppendPlanTasksCommand(**base, mode="commit")
    committed = await service.append(command, idempotency_key="append-1")
    replay = await service.append(command, idempotency_key="append-1")
    assert replay.id == committed.id
    assert committed.revision == 1
    updated = await plans.get(plan.id)
    assert updated is not None and updated.version == plan.version + 1
    assert updated.batches[-1][0].repository_id == appended
    await database.dispose()


@pytest.mark.asyncio
async def test_scope_expansion_requires_approval(tmp_path) -> None:
    database = Database(
        f"sqlite+aiosqlite:///{tmp_path / 'scope.db'}",
        schema_translate_map={schema: None for schema in ALL_SCHEMAS},
    )
    await database.create_all_for_tests()
    plans = PostgresExecutionPlanStore(database)
    existing, outside = uuid4(), uuid4()
    plan = ExecutionPlan(uuid4(), uuid4(), uuid4(), ((_planned(existing),),))
    await plans.add(plan, idempotency_key=f"plan:{plan.id}")
    service = DynamicPlanRevisionService(
        plans, PostgresExecutionPlanRevisionStore(database),
        _Topology(plan.project_id, (existing,)),
    )
    with pytest.raises(TaskConflict, match="scope expansion requires approval"):
        await service.append(
            AppendPlanTasksCommand(
                plan.id, plan.version, plan.created_by_agent_id, "new repository",
                (AppendPlanTaskInput(outside, "Outside", "Modify outside", ("done",)),),
            ),
            idempotency_key="outside",
        )
    await database.dispose()
