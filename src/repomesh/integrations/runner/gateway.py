import json
import logging
from collections.abc import Awaitable, Callable
from uuid import UUID

from repomesh.modules.agent_runtime.runner_store import PostgresRunnerGatewayStore
from repomesh.modules.task_orchestration.contracts import TaskAssignmentGenerationReader, TaskStatus
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
        assignments: TaskAssignmentGenerationReader | None = None,
    ) -> None:
        self._store = store
        self._tasks = tasks
        self._on_terminal = on_terminal
        self._assignments = assignments

    async def enqueue(self, task: RunnerTask) -> None:
        if task.worker_agent_id is None:
            raise ValueError("runner dispatch requires worker_agent_id")
        await self._store.enqueue(task.to_wire())

    async def next_task(self, worker_agent_id: UUID | None) -> dict[str, object] | None:
        return await self._store.lease_next(worker_agent_id)

    async def receive_event(
        self, event: dict[str, object], *, worker_agent_id: UUID | None = None
    ) -> bool:
        run_id = UUID(str(event["runId"]))
        dispatch = await self._store.get_dispatch(run_id)
        projection_allowed = True
        if dispatch is not None and dispatch.assignment_attempt_id is not None:
            projection_allowed = bool(
                self._assignments is not None
                and dispatch.assignment_generation is not None
                and await self._assignments.allows_projection(
                    dispatch.task_id,
                    dispatch.assignment_attempt_id,
                    dispatch.assignment_generation,
                )
            )
        inserted = await self._store.record_event(
            event,
            expected_worker_agent_id=worker_agent_id,
            projection_allowed=projection_allowed,
        )
        if str(event.get("eventType")) in {
            "runner.completed",
            "runner.failed",
            "runner.interrupted",
            "runner.input_required",
        }:
            if await self._store.projection_allowed(UUID(str(event["runId"]))):
                await self._write_back(event)
                if (
                    str(event.get("eventType")) == "runner.completed"
                    and dispatch is not None
                    and dispatch.assignment_attempt_id is not None
                    and dispatch.assignment_generation is not None
                    and self._assignments is not None
                ):
                    await self._assignments.complete_current(
                        dispatch.task_id,
                        dispatch.assignment_attempt_id,
                        dispatch.assignment_generation,
                    )
                if str(event.get("eventType")) in {
                    "runner.failed",
                    "runner.interrupted",
                    "runner.input_required",
                }:
                    await self._store.ensure_recovery_for_run(
                        run_id, event
                    )
            else:
                _logger.warning(
                    "Rejected stale Runner result run_id=%s task_id=%s",
                    event.get("runId"),
                    event.get("taskId"),
                )
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
            # A-18: the Runner event carries these two and this write-back used
            # to drop them, so "no test command was ever run" and "no artifact
            # was produced" existed only in ``runner_events`` -- a table no read
            # model joins. A task therefore could not say whether it had been
            # verified, which is why a run that executed nothing rendered as a
            # clean success right before the merge approval.
            "testCommand": details.get("testCommand"),
            "artifacts": details.get("artifacts", []),
            # Not emitted by any Runner today (RunnerExecutionResult declares no
            # such field). Copied when present so the day a Runner does declare
            # its blockers they reach the task without another migration; until
            # then the agent's words live in ``summary`` and are shown from
            # there, not mined out of it.
            "blockers": details.get("blockers", []),
            "commitSha": details.get("commitSha"),
            "runId": str(dispatch.run_id),
            "workspacePath": dict(dispatch.task_payload.get("workspace") or {}).get("path"),
            "baseSha": dict(dispatch.task_payload.get("workspace") or {}).get("baseSha"),
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
