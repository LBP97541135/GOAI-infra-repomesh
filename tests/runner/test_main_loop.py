"""The serve loop: one task at a time, no repeats, no task fatal to the worker."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from repomesh_runner import (
    ContextBundleRef,
    RepositoryCheckout,
    RunnerEvent,
    RunnerEventType,
    RunnerExecutionResult,
    RunnerPermissions,
    RunnerResultStatus,
    RunnerTask,
)
from repomesh_runner.event_sink import EventDeliveryError
from repomesh_runner.main import EXIT_ENVIRONMENT, Shutdown, run, serve
from repomesh_runner.state_store import TaskLedger

SHA256 = "sha256:" + "a" * 64
NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


def make_task(idempotency_key: str = "run-4-attempt-1") -> RunnerTask:
    return RunnerTask(
        organization_id=UUID("00000000-0000-0000-0000-000000000001"),
        project_id=UUID("00000000-0000-0000-0000-000000000002"),
        task_id=UUID("00000000-0000-0000-0000-000000000003"),
        run_id=UUID("00000000-0000-0000-0000-000000000004"),
        correlation_id=UUID("00000000-0000-0000-0000-000000000005"),
        attempt=1,
        adapter_id="codex",
        instruction="Do the work",
        repository=RepositoryCheckout(
            repository_id=UUID("00000000-0000-0000-0000-000000000006"),
            url="https://github.com/example/service.git",
            base_revision="main",
        ),
        context_bundle=ContextBundleRef(
            bundle_id=UUID("00000000-0000-0000-0000-000000000007"),
            version=1,
            manifest_uri="s3://repomesh-context/manifest.json",
            content_hash=SHA256,
        ),
        permissions=RunnerPermissions(),
        idempotency_key=idempotency_key,
        issued_at=NOW,
    )


class ScriptedSource:
    """Yields the scripted items, then stops the loop rather than blocking forever."""

    def __init__(self, script: list[RunnerTask | None], shutdown: Shutdown) -> None:
        self._script = list(script)
        self._shutdown = shutdown
        self.polls = 0

    async def next_task(self) -> RunnerTask | None:
        self.polls += 1
        if not self._script:
            self._shutdown.set()
            return None
        return self._script.pop(0)


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[RunnerEvent] = []

    async def publish(self, event: RunnerEvent, *, idempotency_key: str) -> None:
        self.events.append(event)


class RejectingSink:
    """Delivers the accepted event, then refuses everything after it."""

    def __init__(self, *, fail_from_sequence: int) -> None:
        self._fail_from_sequence = fail_from_sequence
        self.events: list[RunnerEvent] = []

    async def publish(self, event: RunnerEvent, *, idempotency_key: str) -> None:
        if event.sequence >= self._fail_from_sequence:
            raise EventDeliveryError("receiver said no")
        self.events.append(event)


class StubExecutor:
    def __init__(self, result: RunnerExecutionResult) -> None:
        self._result = result
        self.tasks: list[RunnerTask] = []

    async def execute(self, task: RunnerTask) -> RunnerExecutionResult:
        self.tasks.append(task)
        return self._result


class CrashingExecutor:
    def __init__(self) -> None:
        self.tasks: list[RunnerTask] = []

    async def execute(self, task: RunnerTask) -> RunnerExecutionResult:
        self.tasks.append(task)
        raise OSError("driver died")


def succeeded() -> RunnerExecutionResult:
    return RunnerExecutionResult(RunnerResultStatus.SUCCEEDED, "done")


@pytest.mark.asyncio
async def test_a_task_is_executed_and_observed_end_to_end(tmp_path: Path) -> None:
    shutdown = Shutdown()
    executor = StubExecutor(succeeded())
    sink = RecordingSink()
    ledger = TaskLedger(tmp_path)

    await serve(
        source=ScriptedSource([make_task()], shutdown),
        sink=sink,
        executor=executor,
        ledger=ledger,
        shutdown=shutdown,
    )

    assert [task.idempotency_key for task in executor.tasks] == ["run-4-attempt-1"]
    assert [event.event_type for event in sink.events] == [
        RunnerEventType.ACCEPTED,
        RunnerEventType.COMPLETED,
    ]
    assert ledger.seen("run-4-attempt-1") is True


@pytest.mark.asyncio
async def test_the_terminal_status_is_recorded(tmp_path: Path) -> None:
    shutdown = Shutdown()
    ledger = TaskLedger(tmp_path)

    await serve(
        source=ScriptedSource([make_task()], shutdown),
        sink=RecordingSink(),
        executor=StubExecutor(RunnerExecutionResult(RunnerResultStatus.INTERRUPTED, "cut short")),
        ledger=ledger,
        shutdown=shutdown,
    )

    document = json.loads(ledger.path.read_text(encoding="utf-8"))
    assert document["tasks"] == {"run-4-attempt-1": "interrupted"}


@pytest.mark.asyncio
async def test_an_empty_poll_is_not_an_error(tmp_path: Path) -> None:
    shutdown = Shutdown()
    executor = StubExecutor(succeeded())

    source = ScriptedSource([None, None, make_task()], shutdown)
    await serve(
        source=source,
        sink=RecordingSink(),
        executor=executor,
        ledger=TaskLedger(tmp_path),
        shutdown=shutdown,
    )

    assert len(executor.tasks) == 1
    assert source.polls == 4


@pytest.mark.asyncio
async def test_a_key_already_in_the_ledger_is_never_executed_again(tmp_path: Path) -> None:
    shutdown = Shutdown()
    ledger = TaskLedger(tmp_path)
    ledger.record("run-4-attempt-1", "succeeded")
    executor = StubExecutor(succeeded())
    sink = RecordingSink()

    await serve(
        source=ScriptedSource([make_task()], shutdown),
        sink=sink,
        executor=executor,
        ledger=ledger,
        shutdown=shutdown,
    )

    assert executor.tasks == []
    assert sink.events == []


@pytest.mark.asyncio
async def test_a_redelivered_task_is_skipped_within_one_run(tmp_path: Path) -> None:
    shutdown = Shutdown()
    executor = StubExecutor(succeeded())

    await serve(
        source=ScriptedSource([make_task(), make_task()], shutdown),
        sink=RecordingSink(),
        executor=executor,
        ledger=TaskLedger(tmp_path),
        shutdown=shutdown,
    )

    assert len(executor.tasks) == 1


@pytest.mark.asyncio
async def test_a_crashing_task_does_not_stop_the_loop(tmp_path: Path) -> None:
    shutdown = Shutdown()
    crashing = CrashingExecutor()
    ledger = TaskLedger(tmp_path)

    await serve(
        source=ScriptedSource([make_task("bad"), make_task("good")], shutdown),
        sink=RecordingSink(),
        executor=crashing,
        ledger=ledger,
        shutdown=shutdown,
    )

    assert [task.idempotency_key for task in crashing.tasks] == ["bad", "good"]
    assert ledger.seen("bad") is True
    assert ledger.seen("good") is True


@pytest.mark.asyncio
async def test_a_crashed_task_is_recorded_as_failed(tmp_path: Path) -> None:
    shutdown = Shutdown()
    ledger = TaskLedger(tmp_path)

    await serve(
        source=ScriptedSource([make_task()], shutdown),
        sink=RecordingSink(),
        executor=CrashingExecutor(),
        ledger=ledger,
        shutdown=shutdown,
    )

    document = json.loads(ledger.path.read_text(encoding="utf-8"))
    assert document["tasks"] == {"run-4-attempt-1": "failed"}


@pytest.mark.asyncio
async def test_undeliverable_terminal_event_still_burns_the_key(tmp_path: Path) -> None:
    shutdown = Shutdown()
    ledger = TaskLedger(tmp_path)
    executor = StubExecutor(succeeded())

    await serve(
        source=ScriptedSource([make_task()], shutdown),
        sink=RejectingSink(fail_from_sequence=2),
        executor=executor,
        ledger=ledger,
        shutdown=shutdown,
    )

    assert len(executor.tasks) == 1
    assert ledger.seen("run-4-attempt-1") is True


@pytest.mark.asyncio
async def test_a_task_that_never_started_stays_available_for_redelivery(tmp_path: Path) -> None:
    shutdown = Shutdown()
    ledger = TaskLedger(tmp_path)
    executor = StubExecutor(succeeded())

    await serve(
        source=ScriptedSource([make_task()], shutdown),
        sink=RejectingSink(fail_from_sequence=1),
        executor=executor,
        ledger=ledger,
        shutdown=shutdown,
    )

    assert executor.tasks == []
    assert ledger.seen("run-4-attempt-1") is False


@pytest.mark.asyncio
async def test_shutdown_stops_the_loop_after_the_in_flight_task(tmp_path: Path) -> None:
    shutdown = Shutdown()
    ledger = TaskLedger(tmp_path)
    finished: list[str] = []

    class ShutdownDuringExecution:
        async def execute(self, task: RunnerTask) -> RunnerExecutionResult:
            shutdown.set()
            finished.append(task.idempotency_key)
            return succeeded()

    source = ScriptedSource([make_task("first"), make_task("second")], shutdown)

    await serve(
        source=source,
        sink=RecordingSink(),
        executor=ShutdownDuringExecution(),
        ledger=ledger,
        shutdown=shutdown,
    )

    assert finished == ["first"]
    assert ledger.seen("first") is True
    assert ledger.seen("second") is False
    assert source.polls == 1


@pytest.mark.asyncio
async def test_a_shutdown_set_before_the_first_poll_serves_nothing(tmp_path: Path) -> None:
    shutdown = Shutdown()
    shutdown.set()
    source = ScriptedSource([make_task()], shutdown)
    executor = StubExecutor(succeeded())

    await serve(
        source=source,
        sink=RecordingSink(),
        executor=executor,
        ledger=TaskLedger(tmp_path),
        shutdown=shutdown,
    )

    assert source.polls == 0
    assert executor.tasks == []


def test_a_rejected_environment_variable_stops_the_process(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    environ = {
        "REPOMESH_RUNNER_TASK_SOURCE_URL": "https://control.example/tasks",
        "REPOMESH_RUNNER_EVENT_SINK_URL": "https://control.example/events",
        "REPOMESH_RUNNER_WORKSPACE_ROOT": str(tmp_path),
        "AGENTTEAMS_YOLO": "1",
    }

    assert run(environ) == EXIT_ENVIRONMENT
    assert "AGENTTEAMS_YOLO" in capsys.readouterr().err


def test_a_missing_environment_variable_stops_the_process(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run({}) == EXIT_ENVIRONMENT
    assert "REPOMESH_RUNNER_WORKSPACE_ROOT" in capsys.readouterr().err
