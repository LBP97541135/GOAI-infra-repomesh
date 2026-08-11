import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from repomesh.integrations.runner.gateway import RunnerControlGateway
from repomesh.modules.agent_runtime.runner_store import (
    PostgresRunnerGatewayStore,
    RunnerGatewayConflict,
)
from repomesh.modules.task_orchestration.contracts import TaskStatus
from repomesh.modules.task_orchestration.domain import Task
from repomesh.modules.task_orchestration.infrastructure import PostgresTaskStore
from repomesh.persistence import Database
from repomesh.persistence.base import ALL_SCHEMAS
from repomesh_runner.contracts import (
    ContextBundleRef,
    RepositoryCheckout,
    RunnerPermissions,
    RunnerTask,
)

SHA = "sha256:" + "a" * 64


@pytest.mark.asyncio
async def test_runner_store_rejects_empty_idempotency_key_before_persistence() -> None:
    store = PostgresRunnerGatewayStore(None)  # type: ignore[arg-type]
    with pytest.raises(RunnerGatewayConflict, match="idempotency key"):
        await store.enqueue({"idempotencyKey": "   "})


@pytest.mark.asyncio
async def test_dispatch_event_and_business_task_writeback(tmp_path) -> None:
    database = Database(
        f"sqlite+aiosqlite:///{tmp_path / 'runner.db'}",
        schema_translate_map={schema: None for schema in ALL_SCHEMAS},
    )
    await database.create_all_for_tests()
    task_store = PostgresTaskStore(database)
    runner_store = PostgresRunnerGatewayStore(database)
    gateway = RunnerControlGateway(runner_store, task_store)
    worker_id = uuid4()
    repository_id = uuid4()
    business_task = Task(
        organization_id=uuid4(),
        project_id=uuid4(),
        repository_id=repository_id,
        assigned_by_agent_id=uuid4(),
        assignee_agent_id=worker_id,
        title="Fix pricing",
        instruction="Fix pricing API",
        acceptance=("tests pass",),
    )
    await task_store.add(
        business_task, idempotency_key="task-1", request_fingerprint="sha256:" + "b" * 64
    )
    run_id = uuid4()
    runner_task = RunnerTask(
        organization_id=business_task.organization_id,
        project_id=business_task.project_id,
        task_id=business_task.id,
        run_id=run_id,
        correlation_id=uuid4(),
        attempt=1,
        adapter_id="claude",
        instruction="Read task context and implement",
        repository=RepositoryCheckout(repository_id, "https://example/repo.git", "main"),
        context_bundle=ContextBundleRef(uuid4(), 1, "file:///manifest.json", SHA),
        permissions=RunnerPermissions(),
        idempotency_key="run-1",
        issued_at=datetime.now(UTC),
        worker_agent_id=worker_id,
    )

    await gateway.enqueue(runner_task)
    await gateway.enqueue(runner_task)
    leased = await gateway.next_task(worker_id)
    assert leased is not None and leased["runId"] == str(run_id)

    completed = {
        "schemaVersion": "runtime.v1",
        "eventId": str(uuid4()),
        "eventType": "runner.completed",
        "organizationId": str(business_task.organization_id),
        "projectId": str(business_task.project_id),
        "taskId": str(business_task.id),
        "runId": str(run_id),
        "correlationId": str(runner_task.correlation_id),
        "attempt": 1,
        "sequence": 3,
        "occurredAt": datetime.now(UTC).isoformat(),
        "nativeSessionId": "session-1",
        "payload": {
            "summary": "pricing fixed",
            "changedFiles": ["src/pricing.py"],
            "testResults": [{"command": "pytest", "exitCode": 0}],
            "commitSha": "a" * 40,
        },
    }
    assert await gateway.receive_event(completed) is True
    assert await gateway.receive_event(completed) is False

    updated = await task_store.get(business_task.id)
    assert updated is not None
    assert updated.status is TaskStatus.SUCCEEDED
    assert "pricing fixed" in (updated.result_summary or "")
    assert '"commitSha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"' in (
        updated.result_summary or ""
    )
    await database.dispose()


@pytest.mark.asyncio
async def test_event_binding_mismatch_is_rejected(tmp_path) -> None:
    database = Database(
        f"sqlite+aiosqlite:///{tmp_path / 'binding.db'}",
        schema_translate_map={schema: None for schema in ALL_SCHEMAS},
    )
    await database.create_all_for_tests()
    store = PostgresRunnerGatewayStore(database)
    payload = _wire_payload()
    await store.enqueue(payload)
    event = {
        "eventId": str(uuid4()),
        "eventType": "runner.accepted",
        "organizationId": str(uuid4()),
        "projectId": payload["projectId"],
        "taskId": payload["taskId"],
        "runId": payload["runId"],
        "attempt": 1,
        "sequence": 1,
        "occurredAt": datetime.now(UTC).isoformat(),
        "payload": {},
    }
    with pytest.raises(ValueError, match="binding mismatch"):
        await store.record_event(event)
    await database.dispose()


async def _gateway_with_dispatch(
    tmp_path,
    name: str,
    on_terminal=None,
) -> tuple[RunnerControlGateway, Database, Task, UUID]:
    """Enqueue one dispatch for a fresh business task and return the gateway."""

    database = Database(
        f"sqlite+aiosqlite:///{tmp_path / name}",
        schema_translate_map={schema: None for schema in ALL_SCHEMAS},
    )
    await database.create_all_for_tests()
    task_store = PostgresTaskStore(database)
    gateway = RunnerControlGateway(
        PostgresRunnerGatewayStore(database), task_store, on_terminal
    )
    worker_id = uuid4()
    business_task = Task(
        organization_id=uuid4(),
        project_id=uuid4(),
        repository_id=uuid4(),
        assigned_by_agent_id=uuid4(),
        assignee_agent_id=worker_id,
        title="Fix pricing",
        instruction="Fix pricing API",
        acceptance=("tests pass",),
    )
    await task_store.add(
        business_task, idempotency_key=name, request_fingerprint="sha256:" + "c" * 64
    )
    run_id = uuid4()
    await gateway.enqueue(
        RunnerTask(
            organization_id=business_task.organization_id,
            project_id=business_task.project_id,
            task_id=business_task.id,
            run_id=run_id,
            correlation_id=uuid4(),
            attempt=1,
            adapter_id="claude",
            instruction="Read task context and implement",
            repository=RepositoryCheckout(
                business_task.repository_id, "https://example/repo.git", "main"
            ),
            context_bundle=ContextBundleRef(uuid4(), 1, "file:///manifest.json", SHA),
            permissions=RunnerPermissions(),
            idempotency_key=name,
            issued_at=datetime.now(UTC),
            worker_agent_id=worker_id,
        )
    )
    return gateway, database, business_task, run_id


def _event(business_task: Task, run_id: UUID, event_type: str) -> dict[str, object]:
    return {
        "schemaVersion": "runtime.v1",
        "eventId": str(uuid4()),
        "eventType": event_type,
        "organizationId": str(business_task.organization_id),
        "projectId": str(business_task.project_id),
        "taskId": str(business_task.id),
        "runId": str(run_id),
        "correlationId": str(uuid4()),
        "attempt": 1,
        "sequence": 3,
        "occurredAt": datetime.now(UTC).isoformat(),
        "payload": {"summary": "runner reported"},
    }


@pytest.mark.asyncio
async def test_terminal_result_advances_the_execution_plan(tmp_path) -> None:
    advanced: list[UUID] = []

    async def on_terminal(task_id: UUID) -> None:
        advanced.append(task_id)

    gateway, database, business_task, run_id = await _gateway_with_dispatch(
        tmp_path, "advance.db", on_terminal
    )

    assert await gateway.receive_event(_event(business_task, run_id, "runner.completed")) is True

    assert advanced == [business_task.id]
    await database.dispose()


@pytest.mark.asyncio
async def test_blocked_result_does_not_advance_the_execution_plan(tmp_path) -> None:
    advanced: list[UUID] = []

    async def on_terminal(task_id: UUID) -> None:
        advanced.append(task_id)

    gateway, database, business_task, run_id = await _gateway_with_dispatch(
        tmp_path, "blocked.db", on_terminal
    )

    event = _event(business_task, run_id, "runner.input_required")
    assert await gateway.receive_event(event) is True

    assert advanced == []
    await database.dispose()


@pytest.mark.asyncio
async def test_replaying_a_terminal_event_retries_the_plan_advance(tmp_path) -> None:
    advanced: list[UUID] = []

    async def on_terminal(task_id: UUID) -> None:
        advanced.append(task_id)

    gateway, database, business_task, run_id = await _gateway_with_dispatch(
        tmp_path, "advance-replay.db", on_terminal
    )

    event = _event(business_task, run_id, "runner.completed")
    assert await gateway.receive_event(event) is True
    # The duplicate is not inserted again, but it is the operator's lever to
    # re-trigger an advance that previously failed — scheduling must re-run.
    assert await gateway.receive_event(event) is False

    assert advanced == [business_task.id, business_task.id]
    await database.dispose()


@pytest.mark.asyncio
async def test_failing_advancer_does_not_break_event_ingestion(tmp_path, caplog) -> None:
    async def on_terminal(task_id: UUID) -> None:
        raise RuntimeError("scheduling backend is down")

    gateway, database, business_task, run_id = await _gateway_with_dispatch(
        tmp_path, "advance-failure.db", on_terminal
    )

    with caplog.at_level(logging.ERROR):
        inserted = await gateway.receive_event(
            _event(business_task, run_id, "runner.failed")
        )

    assert inserted is True
    assert "Failed to advance the execution plan" in caplog.text
    stored = await PostgresTaskStore(database).get(business_task.id)
    assert stored is not None and stored.status is TaskStatus.FAILED
    await database.dispose()


def _wire_payload() -> dict[str, object]:
    return RunnerTask(
        organization_id=uuid4(),
        project_id=uuid4(),
        task_id=uuid4(),
        run_id=uuid4(),
        correlation_id=uuid4(),
        attempt=1,
        adapter_id="codex",
        instruction="work",
        repository=RepositoryCheckout(uuid4(), "https://example/repo.git", "main"),
        context_bundle=ContextBundleRef(uuid4(), 1, "file:///manifest.json", SHA),
        permissions=RunnerPermissions(),
        idempotency_key=str(uuid4()),
        issued_at=datetime.now(UTC),
        worker_agent_id=uuid4(),
    ).to_wire()
