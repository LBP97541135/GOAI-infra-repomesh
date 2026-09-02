from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from .contracts import (
    RunnerEvent,
    RunnerEventType,
    RunnerExecutionResult,
    RunnerResultStatus,
    RunnerTask,
)


class RunnerExecutor(Protocol):
    async def execute(self, task: RunnerTask) -> RunnerExecutionResult: ...


class RunnerEventSink(Protocol):
    async def publish(self, event: RunnerEvent, *, idempotency_key: str) -> None: ...


class RunnerExecutionError(RuntimeError):
    pass


_FINAL_EVENT_TYPES = {
    RunnerResultStatus.SUCCEEDED: RunnerEventType.COMPLETED,
    RunnerResultStatus.FAILED: RunnerEventType.FAILED,
    RunnerResultStatus.INTERRUPTED: RunnerEventType.INTERRUPTED,
    RunnerResultStatus.INPUT_REQUIRED: RunnerEventType.INPUT_REQUIRED,
}


class ExecuteRunnerTask:
    def __init__(
        self,
        executor: RunnerExecutor,
        event_sink: RunnerEventSink,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._executor = executor
        self._event_sink = event_sink
        self._clock = clock or (lambda: datetime.now(UTC))

    async def execute(self, task: RunnerTask) -> RunnerExecutionResult:
        await self._publish(
            task,
            sequence=1,
            event_type=RunnerEventType.ACCEPTED,
            payload={"adapterId": task.adapter_id},
        )
        try:
            result = await self._executor.execute(task)
        except Exception as error:
            await self._publish(
                task,
                sequence=2,
                event_type=RunnerEventType.FAILED,
                payload={
                    "errorType": type(error).__name__,
                    "message": str(error)[:500],
                },
            )
            raise RunnerExecutionError(f"runner execution failed for run {task.run_id}") from error

        await self._publish(
            task,
            sequence=2,
            event_type=_FINAL_EVENT_TYPES[result.status],
            native_session_id=result.native_session_id,
            payload={
                "status": result.status.value,
                "summary": result.summary,
                "testCommand": result.test_command,
                "artifacts": [artifact.to_wire() for artifact in result.artifacts],
                "changedFiles": list(result.changed_files),
                "commitSha": result.commit_sha,
                "testResults": [
                    {"command": entry.command, "exitCode": entry.exit_code}
                    for entry in result.test_results
                ],
                "databaseChange": (
                    dict(result.database_change) if result.database_change is not None else None
                ),
            },
        )
        return result

    async def _publish(
        self,
        task: RunnerTask,
        *,
        sequence: int,
        event_type: RunnerEventType,
        payload: dict[str, object],
        native_session_id: str | None = None,
    ) -> None:
        event = RunnerEvent(
            event_id=self._event_id(task.run_id, task.attempt, sequence, event_type),
            event_type=event_type,
            task=task,
            sequence=sequence,
            occurred_at=self._clock(),
            native_session_id=native_session_id,
            payload=payload,
        )
        await self._event_sink.publish(
            event,
            idempotency_key=f"{task.idempotency_key}:event:{sequence}",
        )

    @staticmethod
    def _event_id(
        run_id: UUID,
        attempt: int,
        sequence: int,
        event_type: RunnerEventType,
    ) -> UUID:
        name = f"repomesh-runtime-v1:{run_id}:{attempt}:{sequence}:{event_type.value}"
        return uuid5(NAMESPACE_URL, name)
