import json
from uuid import UUID

from repomesh.modules.agent_runtime.runner_store import PostgresRunnerGatewayStore
from repomesh.modules.task_orchestration.contracts import TaskStatus
from repomesh.modules.task_orchestration.ports import TaskStore
from repomesh_runner.contracts import RunnerTask


class RunnerControlGateway:
    """Durable bridge between governed business tasks and the execution plane."""

    def __init__(self, store: PostgresRunnerGatewayStore, tasks: TaskStore) -> None:
        self._store = store
        self._tasks = tasks

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
