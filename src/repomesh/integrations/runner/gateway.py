import json
import logging
from collections.abc import Awaitable, Callable
from uuid import UUID

from repomesh.modules.agent_runtime.runner_store import PostgresRunnerGatewayStore
from repomesh.modules.task_orchestration.contracts import TaskStatus
from repomesh.modules.task_orchestration.ports import TaskStore
from repomesh_runner.contracts import RunnerTask

_logger = logging.getLogger(__name__)

_TERMINAL_TASK_STATUSES = frozenset({TaskStatus.SUCCEEDED, TaskStatus.FAILED})


class RunnerControlGateway:
    """Durable bridge between governed business tasks and the execution plane."""

    def __init__(
        self,
        store: PostgresRunnerGatewayStore,
        tasks: TaskStore,
        on_terminal: Callable[[UUID], Awaitable[None]] | None = None,
    ) -> None:
        self._store = store
        self._tasks = tasks
        self._on_terminal = on_terminal

    async def enqueue(self, task: RunnerTask) -> None:
        if task.worker_agent_id is None:
            raise ValueError("runner dispatch requires worker_agent_id")
        await self._store.enqueue(task.to_wire())

    async def next_task(self, worker_agent_id: UUID | None) -> dict[str, object] | None:
        return await self._store.lease_next(worker_agent_id)

    async def receive_event(self, event: dict[str, object]) -> bool:
        inserted = await self._store.record_event(event)
        if str(event.get("eventType")) in {
            "runner.completed",
            "runner.failed",
            "runner.interrupted",
            "runner.input_required",
        }:
            await self._write_back(event)
        return inserted

    async def _write_back(self, event: dict[str, object]) -> None:
        dispatch = await self._store.get_dispatch(UUID(str(event["runId"])))
        if dispatch is None:
            raise ValueError("runner dispatch disappeared during result projection")
        task = await self._tasks.get(dispatch.task_id)
        if task is None:
            raise ValueError("runner result references an unknown business task")
        if task.assignee_agent_id != dispatch.worker_agent_id:
            raise ValueError("runner result worker binding mismatch")
        status = {
            "runner.completed": TaskStatus.SUCCEEDED,
            "runner.failed": TaskStatus.FAILED,
            "runner.interrupted": TaskStatus.FAILED,
            "runner.input_required": TaskStatus.BLOCKED,
        }[str(event["eventType"])]
        if task.status is status:
            # The business task is already settled, but a replayed terminal event is
            # also the operator's lever to re-trigger a plan advance that previously
            # failed inside on_terminal — so scheduling still runs on duplicates.
            if status in _TERMINAL_TASK_STATUSES:
                await self._advance_execution_plan(task.id)
            return
        payload = event.get("payload")
        details = dict(payload) if isinstance(payload, dict) else {}
        summary = str(details.get("summary") or event["eventType"])
        evidence = {
            "summary": summary,
            "changedFiles": details.get("changedFiles", []),
            "testResults": details.get("testResults", []),
            "commitSha": details.get("commitSha"),
            "runId": str(dispatch.run_id),
        }
        updated = task.report(status, json.dumps(evidence, ensure_ascii=False, sort_keys=True))
        await self._tasks.update(updated, expected_version=task.version)
        if status in _TERMINAL_TASK_STATUSES:
            await self._advance_execution_plan(task.id)

    async def _advance_execution_plan(self, task_id: UUID) -> None:
        """Let the execution plan schedule follow-up work, best effort.

        The Runner event is already accepted and durably recorded at this
        point, so a scheduling failure must never fail ingestion: the next
        terminal event, or an operator replay, re-triggers the advance.
        """

        if self._on_terminal is None:
            return
        try:
            await self._on_terminal(task_id)
        except Exception:
            _logger.exception("Failed to advance the execution plan for task %s", task_id)
