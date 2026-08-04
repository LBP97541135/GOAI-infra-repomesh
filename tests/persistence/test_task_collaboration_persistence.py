from uuid import uuid4

import pytest
import pytest_asyncio

from repomesh.modules.collaboration.contracts import (
    CollaborationDeliveryStatus,
    CollaborationMessageKind,
)
from repomesh.modules.collaboration.domain import CollaborationMessage
from repomesh.modules.collaboration.infrastructure import (
    PostgresCollaborationMessageStore,
)
from repomesh.modules.task_orchestration.contracts import TaskStatus
from repomesh.modules.task_orchestration.domain import Task, TaskConflict
from repomesh.modules.task_orchestration.infrastructure import PostgresTaskStore
from repomesh.persistence import Database
from repomesh.persistence.base import ALL_SCHEMAS


@pytest_asyncio.fixture
async def database(tmp_path: object) -> Database:
    database_path = tmp_path.joinpath("repomesh-orchestration.db")
    instance = Database(
        f"sqlite+aiosqlite:///{database_path}",
        schema_translate_map={schema: None for schema in ALL_SCHEMAS},
    )
    await instance.create_all_for_tests()
    yield instance
    await instance.dispose()


@pytest.mark.asyncio
async def test_task_store_persists_state_and_uses_optimistic_version(database: Database) -> None:
    store = PostgresTaskStore(database)
    project_id = uuid4()
    task = Task(
        organization_id=uuid4(),
        project_id=project_id,
        repository_id=uuid4(),
        assigned_by_agent_id=uuid4(),
        assignee_agent_id=uuid4(),
        title="Pricing task",
        instruction="Implement the pricing contract.",
        acceptance=("Tests pass",),
    )
    await store.add(
        task,
        idempotency_key="persistent-task",
        request_fingerprint="sha256:task",
    )
    started = task.start()
    await store.update(started, expected_version=task.version)

    stored = await store.get(task.id)
    replay = await store.get_by_idempotency_key("persistent-task")
    project_tasks = await store.list_by_project(project_id)
    assert stored is not None and stored.status is TaskStatus.IN_PROGRESS
    assert replay is not None and replay[0].id == task.id
    assert project_tasks == (stored,)

    with pytest.raises(TaskConflict, match="version changed"):
        await store.update(started.report(TaskStatus.SUCCEEDED, "done"), expected_version=1)


@pytest.mark.asyncio
async def test_collaboration_store_persists_delivery_receipt(database: Database) -> None:
    store = PostgresCollaborationMessageStore(database)
    message = CollaborationMessage(
        organization_id=uuid4(),
        project_id=uuid4(),
        repository_id=uuid4(),
        task_id=uuid4(),
        sender_agent_id=uuid4(),
        recipient_agent_id=uuid4(),
        kind=CollaborationMessageKind.TASK_ASSIGNMENT,
        subject="Worker task",
        body="Implement the approved spec.",
        room_id="!team:matrix.local",
    )
    await store.add(
        message,
        idempotency_key="persistent-message",
        request_fingerprint="sha256:message",
    )
    await store.update(message.delivered("$matrix-event"))

    replay = await store.get_by_idempotency_key("persistent-message")
    assert replay is not None
    stored, fingerprint = replay
    assert fingerprint == "sha256:message"
    assert stored.status is CollaborationDeliveryStatus.DELIVERED
    assert stored.event_id == "$matrix-event"
