from datetime import UTC, datetime
from uuid import UUID

import pytest

from repomesh_runner import (
    ContextBundleRef,
    ExecuteRunnerTask,
    RepositoryCheckout,
    RunnerEvent,
    RunnerEventType,
    RunnerExecutionError,
    RunnerExecutionResult,
    RunnerPermissions,
    RunnerResultStatus,
    RunnerTask,
)
from repomesh_runner import (
    TestCommandResult as CommandOutcome,
)

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
SHA256 = "sha256:" + "a" * 64


def make_task() -> RunnerTask:
    return RunnerTask(
        organization_id=UUID("00000000-0000-0000-0000-000000000001"),
        project_id=UUID("00000000-0000-0000-0000-000000000002"),
        task_id=UUID("00000000-0000-0000-0000-000000000003"),
        run_id=UUID("00000000-0000-0000-0000-000000000004"),
        correlation_id=UUID("00000000-0000-0000-0000-000000000005"),
        attempt=1,
        adapter_id="codex",
        instruction="Implement the accepted specification",
        repository=RepositoryCheckout(
            repository_id=UUID("00000000-0000-0000-0000-000000000006"),
            url="https://github.com/example/service.git",
            base_revision="main",
        ),
        context_bundle=ContextBundleRef(
            bundle_id=UUID("00000000-0000-0000-0000-000000000007"),
            version=3,
            manifest_uri="s3://repomesh-context/bundles/7/manifest.json",
            content_hash=SHA256,
        ),
        permissions=RunnerPermissions(allowed_tools=("git", "pytest")),
        idempotency_key="run-4-attempt-1",
        issued_at=NOW,
        credential_refs=("credential://github/project-2",),
    )


class RecordingSink:
    def __init__(self) -> None:
        self.records: list[tuple[RunnerEvent, str]] = []

    async def publish(self, event: RunnerEvent, *, idempotency_key: str) -> None:
        self.records.append((event, idempotency_key))


class ResultExecutor:
    def __init__(self, result: RunnerExecutionResult) -> None:
        self.result = result

    async def execute(self, task: RunnerTask) -> RunnerExecutionResult:
        return self.result


class FailingExecutor:
    async def execute(self, task: RunnerTask) -> RunnerExecutionResult:
        raise OSError("coding process exited unexpectedly")


@pytest.mark.asyncio
async def test_success_publishes_ordered_idempotent_events() -> None:
    result = RunnerExecutionResult(
        status=RunnerResultStatus.SUCCEEDED,
        summary="Implementation and tests completed",
        native_session_id="session-123",
        test_command="pytest -q",
    )
    sink = RecordingSink()
    service = ExecuteRunnerTask(ResultExecutor(result), sink, clock=lambda: NOW)

    returned = await service.execute(make_task())

    assert returned is result
    assert [record[0].event_type for record in sink.records] == [
        RunnerEventType.ACCEPTED,
        RunnerEventType.COMPLETED,
    ]
    assert [record[0].sequence for record in sink.records] == [1, 2]
    assert [record[1] for record in sink.records] == [
        "run-4-attempt-1:event:1",
        "run-4-attempt-1:event:2",
    ]
    assert sink.records[1][0].native_session_id == "session-123"


@pytest.mark.asyncio
async def test_completion_payload_carries_structured_execution_evidence() -> None:
    result = RunnerExecutionResult(
        status=RunnerResultStatus.SUCCEEDED,
        summary="Implementation and tests completed",
        changed_files=("src/app.py", "tests/test_app.py"),
        test_results=(
            CommandOutcome(command="pytest -q", exit_code=0),
            CommandOutcome(command="ruff check src", exit_code=0),
        ),
        commit_sha="a" * 40,
    )
    sink = RecordingSink()
    service = ExecuteRunnerTask(ResultExecutor(result), sink, clock=lambda: NOW)

    await service.execute(make_task())

    payload = sink.records[1][0].payload
    assert payload["changedFiles"] == ["src/app.py", "tests/test_app.py"]
    assert payload["testResults"] == [
        {"command": "pytest -q", "exitCode": 0},
        {"command": "ruff check src", "exitCode": 0},
    ]
    assert payload["commitSha"] == "a" * 40
    assert sink.records[1][0].to_wire()["payload"]["changedFiles"] == [
        "src/app.py",
        "tests/test_app.py",
    ]


@pytest.mark.asyncio
async def test_failing_test_evidence_travels_on_the_failed_event() -> None:
    result = RunnerExecutionResult(
        status=RunnerResultStatus.FAILED,
        summary="test_command_failed: pytest -q (exit code 1)",
        test_results=(CommandOutcome(command="pytest -q", exit_code=1),),
    )
    sink = RecordingSink()
    service = ExecuteRunnerTask(ResultExecutor(result), sink, clock=lambda: NOW)

    await service.execute(make_task())

    assert sink.records[1][0].event_type is RunnerEventType.FAILED
    assert sink.records[1][0].payload["testResults"] == [{"command": "pytest -q", "exitCode": 1}]
    assert sink.records[1][0].payload["changedFiles"] == []


@pytest.mark.asyncio
async def test_same_attempt_produces_the_same_event_ids() -> None:
    result = RunnerExecutionResult(RunnerResultStatus.INTERRUPTED, "Interrupted by operator")
    first_sink = RecordingSink()
    second_sink = RecordingSink()

    first_service = ExecuteRunnerTask(ResultExecutor(result), first_sink, clock=lambda: NOW)
    second_service = ExecuteRunnerTask(ResultExecutor(result), second_sink, clock=lambda: NOW)

    await first_service.execute(make_task())
    await second_service.execute(make_task())

    assert [record[0].event_id for record in first_sink.records] == [
        record[0].event_id for record in second_sink.records
    ]
    assert first_sink.records[1][0].event_type is RunnerEventType.INTERRUPTED


@pytest.mark.asyncio
async def test_executor_exception_is_observed_and_wrapped() -> None:
    sink = RecordingSink()
    service = ExecuteRunnerTask(FailingExecutor(), sink, clock=lambda: NOW)

    with pytest.raises(RunnerExecutionError):
        await service.execute(make_task())

    assert sink.records[-1][0].event_type is RunnerEventType.FAILED
    assert sink.records[-1][0].payload == {
        "errorType": "OSError",
        "message": "coding process exited unexpectedly",
    }


def test_runner_task_rejects_unversioned_context_content() -> None:
    with pytest.raises(ValueError, match="sha256"):
        ContextBundleRef(
            bundle_id=UUID("00000000-0000-0000-0000-000000000007"),
            version=1,
            manifest_uri="s3://repomesh-context/manifest.json",
            content_hash="latest",
        )


def test_runner_task_wire_payload_contains_references_not_secrets() -> None:
    payload = make_task().to_wire()

    assert payload["schemaVersion"] == "runtime.v1"
    assert payload["contextBundle"]["contentHash"] == SHA256
    assert payload["credentialRefs"] == ["credential://github/project-2"]
    assert "credentials" not in payload
    assert "token" not in payload


def test_runner_permissions_reject_duplicate_values() -> None:
    with pytest.raises(ValueError, match="unique"):
        RunnerPermissions(allowed_tools=("git", "git"))
