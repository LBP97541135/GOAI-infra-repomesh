import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from repomesh.integrations.runner.gateway import RunnerControlGateway
from repomesh.modules.agent_runtime.runner_store import (
    PostgresRunnerGatewayStore,
    RunnerEventRecord,
    RunnerGatewayConflict,
    RunnerGatewayForbidden,
)
from repomesh.modules.task_orchestration.assignment import (
    AssignmentReason,
    PostgresTaskAssignmentStore,
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


async def _enqueue(
    gateway: RunnerControlGateway, *, worker: UUID, adapter: str, key: str
) -> UUID:
    run_id = uuid4()
    await gateway.enqueue(
        RunnerTask(
            organization_id=uuid4(),
            project_id=uuid4(),
            task_id=uuid4(),
            run_id=run_id,
            correlation_id=uuid4(),
            attempt=1,
            adapter_id=adapter,
            instruction="Do the work",
            repository=RepositoryCheckout(uuid4(), "https://example/repo.git", "main"),
            context_bundle=ContextBundleRef(uuid4(), 1, "file:///manifest.json", SHA),
            permissions=RunnerPermissions(),
            idempotency_key=key,
            issued_at=datetime.now(UTC),
            worker_agent_id=worker,
        )
    )
    return run_id


@pytest.mark.asyncio
async def test_a_lease_is_narrowed_by_adapter_and_kept_off_excluded_queues(tmp_path) -> None:
    """The store's half of the router's rule.

    ``adapterId`` is read from the frozen payload, an excluded queue is never
    drained by a subjectless lease, and a queue pinned by its own credential is
    untouched by either filter.
    """

    database = Database(
        f"sqlite+aiosqlite:///{tmp_path / 'runner.db'}",
        schema_translate_map={schema: None for schema in ALL_SCHEMAS},
    )
    await database.create_all_for_tests()
    gateway = RunnerControlGateway(
        PostgresRunnerGatewayStore(database), PostgresTaskStore(database)
    )
    bridge_worker, managed_worker = uuid4(), uuid4()
    codex_run = await _enqueue(gateway, worker=bridge_worker, adapter="codex", key="run-codex")
    mock_run = await _enqueue(gateway, worker=managed_worker, adapter="mock", key="run-mock")

    # A subjectless Runner that serves mock only, kept off the Bridge's queue.
    leased = await gateway.next_task(None, adapters={"mock"}, exclude_worker_ids={bridge_worker})
    assert leased is not None and leased["runId"] == str(mock_run)
    # Nothing left for it: the only other runnable dispatch sits in the excluded queue.
    assert (
        await gateway.next_task(
            None, adapters={"mock", "codex"}, exclude_worker_ids={bridge_worker}
        )
        is None
    )
    # The Bridge's own credential drains its queue regardless of any narrowing.
    own = await gateway.next_task(bridge_worker)
    assert own is not None and own["runId"] == str(codex_run)
    await database.dispose()


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
            "testCommand": "pytest -q",
            "artifacts": [{"kind": "log", "uri": "s3://run/log", "contentHash": "0" * 64}],
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
    # A-18: the event's verification facts reach the task. They used to stop
    # here — the write-back copied five keys and dropped testCommand and
    # artifacts, so a task could not say whether anything had been run.
    evidence = updated.to_view().evidence
    assert evidence is not None
    assert evidence.test_command == "pytest -q"
    assert evidence.artifact_count == 1
    assert evidence.verified is True
    await database.dispose()


@pytest.mark.asyncio
async def test_a_completed_run_that_executed_nothing_writes_back_unverified(tmp_path) -> None:
    """The live A-18 shape: ``runner.completed`` with an empty test list.

    Reaching ``runner.completed`` means the process finished, never that it
    checked anything. The agent said so in its own summary; that sentence and
    the empty test list both have to survive the write-back, because they are
    the only things standing between this row and an automatic merge.
    """

    database = Database(
        f"sqlite+aiosqlite:///{tmp_path / 'unverified.db'}",
        schema_translate_map={schema: None for schema in ALL_SCHEMAS},
    )
    await database.create_all_for_tests()
    task_store = PostgresTaskStore(database)
    gateway = RunnerControlGateway(PostgresRunnerGatewayStore(database), task_store)
    worker_id = uuid4()
    repository_id = uuid4()
    business_task = Task(
        organization_id=uuid4(),
        project_id=uuid4(),
        repository_id=repository_id,
        assigned_by_agent_id=uuid4(),
        assignee_agent_id=worker_id,
        title="Add the tax estimate",
        instruction="Add the tax estimate to checkout",
        acceptance=("code compiles / existing tests pass",),
    )
    await task_store.add(
        business_task, idempotency_key="task-2", request_fingerprint="sha256:" + "c" * 64
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
        idempotency_key="run-2",
        issued_at=datetime.now(UTC),
        worker_agent_id=worker_id,
    )
    await gateway.enqueue(runner_task)
    assert await gateway.next_task(worker_id) is not None

    summary = (
        "Implementation is complete. I could not execute anything to verify it — see below.\n"
        "Nothing was executed. Please re-run before merging."
    )
    assert (
        await gateway.receive_event(
            {
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
                    "status": "succeeded",
                    "summary": summary,
                    "testCommand": None,
                    "artifacts": [],
                    "changedFiles": ["src/checkout/tax_calculator.py"],
                    "commitSha": "5" * 40,
                    "testResults": [],
                },
            }
        )
        is True
    )

    updated = await task_store.get(business_task.id)
    assert updated is not None
    assert updated.status is TaskStatus.SUCCEEDED  # it did succeed, as a run
    evidence = updated.to_view().evidence
    assert evidence is not None
    assert evidence.verified is False
    assert evidence.summary_text == summary
    assert evidence.test_command is None
    assert evidence.test_results == ()
    assert evidence.artifact_count == 0
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


@pytest.mark.asyncio
async def test_an_event_from_another_worker_is_refused_and_writes_nothing(tmp_path) -> None:
    """The server side of PR 5's events guard, against a real store.

    The wire schema carries no worker id, so ownership can only come from the
    dispatch row this run belongs to — which is the point of joining rather
    than reading a field. A credential that owns a different worker gets
    ``RunnerGatewayForbidden`` (a 403 at the API, not the 409 its
    ``ValueError`` siblings wear) and the event does not reach the table: were
    it recorded, the refusal would be advice rather than a boundary, and the
    same event replayed under the right credential would then be a duplicate.
    """

    gateway, database, business_task, run_id = await _gateway_with_dispatch(
        tmp_path, "scoped-events.db"
    )
    store = PostgresRunnerGatewayStore(database)
    event = _event(business_task, run_id, "runner.accepted")

    with pytest.raises(RunnerGatewayForbidden):
        await store.record_event(event, expected_worker_agent_id=uuid4())

    dispatch = await store.get_dispatch(run_id)
    assert dispatch is not None and dispatch.status == "queued"

    # The same event, under the credential that owns the run, is recorded --
    # so the refusal above was about the caller and nothing else.
    assert (
        await store.record_event(
            event, expected_worker_agent_id=business_task.assignee_agent_id
        )
        is True
    )
    settled = await store.get_dispatch(run_id)
    assert settled is not None and settled.status == "accepted"
    await database.dispose()


@pytest.mark.asyncio
async def test_an_unscoped_credential_records_any_workers_event(tmp_path) -> None:
    """``None`` is the managed Runner, which reports for every worker it runs."""

    gateway, database, business_task, run_id = await _gateway_with_dispatch(
        tmp_path, "unscoped-events.db"
    )

    assert await gateway.receive_event(_event(business_task, run_id, "runner.accepted")) is True

    dispatch = await PostgresRunnerGatewayStore(database).get_dispatch(run_id)
    assert dispatch is not None and dispatch.status == "accepted"
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


@pytest.mark.asyncio
async def test_stale_assignment_generation_is_audited_without_task_writeback(tmp_path) -> None:
    gateway, database, task, _ = await _gateway_with_dispatch(
        tmp_path, "stale-generation.db"
    )
    # Replace the legacy dispatch with one carrying assignment fencing.
    assignments = PostgresTaskAssignmentStore(database)
    initial = await assignments.ensure_initial(task.id)
    fenced_run = uuid4()
    runner_task = RunnerTask(
        organization_id=task.organization_id, project_id=task.project_id,
        task_id=task.id, run_id=fenced_run, correlation_id=uuid4(), attempt=1,
        adapter_id="claude", instruction="fenced run",
        repository=RepositoryCheckout(task.repository_id, "https://example/repo.git", "main"),
        context_bundle=ContextBundleRef(uuid4(), 1, "file:///manifest.json", SHA),
        permissions=RunnerPermissions(), idempotency_key=f"fenced:{fenced_run}",
        issued_at=datetime.now(UTC), worker_agent_id=task.assignee_agent_id,
        assignment_attempt_id=initial.id, assignment_generation=initial.generation,
    )
    await gateway.enqueue(runner_task)
    await assignments.reassign(
        task.id, expected_task_version=task.version,
        expected_generation=initial.generation, replacement_worker_id=uuid4(),
        reason=AssignmentReason.WORKER_UNREACHABLE,
    )
    event = _event(task, fenced_run, "runner.completed")
    event["assignmentAttemptId"] = str(initial.id)
    event["assignmentGeneration"] = initial.generation

    assert await gateway.receive_event(event) is True
    current = await PostgresTaskStore(database).get(task.id)
    assert current is not None and current.status is TaskStatus.ASSIGNED
    async with database.transaction() as session:
        recorded = await session.scalar(
            select(RunnerEventRecord).where(
                RunnerEventRecord.run_id == fenced_run
            )
        )
        assert recorded is not None
        assert recorded.projection_status == "rejected"
        assert recorded.rejection_reason == "stale_assignment_generation"
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
