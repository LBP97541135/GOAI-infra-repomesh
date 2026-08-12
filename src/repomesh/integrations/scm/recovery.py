import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from repomesh.modules.delivery import DeliveryService
from repomesh.modules.delivery.contracts import (
    ChangeSetView,
    RecordRecoveryActionCommand,
    RecoveryActionKind,
    RecoveryActionStatus,
    RecoveryActionView,
)

logger = logging.getLogger(__name__)


class RevertConflict(RuntimeError):
    pass


class RecoveryWaiting(RuntimeError):
    """The action cannot run yet, and not running yet is not a failure.

    Without this signal a handler has only two outcomes, and both are wrong for
    "the remote is not ready": returning success claims work that never
    happened, and raising anything else records the action ``FAILED``, which
    the Saga treats as terminal and never retries.

    Raising ``RecoveryWaiting`` means:

    * the action is recorded back as ``PENDING``, never ``FAILED``;
    * the Saga does not advance to the next action in the plan;
    * the same action is attempted again on the next executor interval.

    The ChangeSet therefore stays ``COMPENSATING`` rather than falling into
    ``MANUAL_INTERVENTION``. The message is stored as the action detail, so an
    operator can read what the rollback is waiting for. Handlers must only
    raise it for conditions a remote can still resolve on its own (a check that
    is still running); a condition that will never clear is a real failure and
    belongs in ``SCMConflict``.
    """


@dataclass(frozen=True, slots=True)
class RecoveryExecutionContext:
    change_set: ChangeSetView
    action: RecoveryActionView


class RecoveryActionHandler(Protocol):
    async def execute(self, context: RecoveryExecutionContext) -> str: ...


class RecoveryConflictTaskGateway(Protocol):
    async def create_for_conflict(self, context: RecoveryExecutionContext, error: str) -> UUID: ...


class RevertDeliveryGateway(Protocol):
    async def close_pull_request(self, context: RecoveryExecutionContext) -> str: ...

    async def create_revert_pull_request(self, context: RecoveryExecutionContext) -> str: ...

    async def merge_revert_pull_request(self, context: RecoveryExecutionContext) -> str: ...

    async def revalidate(self, context: RecoveryExecutionContext) -> str: ...


class GovernedRecoveryActionHandler:
    """Route Saga actions to a provider implementation with explicit idempotent methods."""

    def __init__(self, gateway: RevertDeliveryGateway) -> None:
        self._gateway = gateway

    async def execute(self, context: RecoveryExecutionContext) -> str:
        kind = context.action.kind
        if kind is RecoveryActionKind.CLOSE_PULL_REQUEST:
            return await self._gateway.close_pull_request(context)
        if kind is RecoveryActionKind.CREATE_REVERT_PULL_REQUEST:
            return await self._gateway.create_revert_pull_request(context)
        if kind is RecoveryActionKind.MERGE_REVERT_PULL_REQUEST:
            return await self._gateway.merge_revert_pull_request(context)
        if kind is RecoveryActionKind.REVALIDATE_CHANGESET:
            return await self._gateway.revalidate(context)
        raise ValueError(f"recovery action requires another executor: {kind.value}")


class RecoverySagaExecutor:
    """Execute one durable recovery action at a time in planned reverse order."""

    def __init__(
        self,
        delivery: DeliveryService,
        handler: RecoveryActionHandler,
        conflict_tasks: RecoveryConflictTaskGateway | None = None,
        *,
        interval_seconds: float = 15,
    ) -> None:
        self._delivery = delivery
        self._handler = handler
        self._conflict_tasks = conflict_tasks
        self._interval_seconds = interval_seconds
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="recovery-saga")

    async def close(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        await self._task
        self._task = None

    async def run_once(self) -> None:
        for change_set in await self._delivery.list_active():
            if not change_set.recovery_plans:
                continue
            plan = change_set.recovery_plans[-1]
            action = next(
                (
                    item
                    for item in sorted(plan.actions, key=lambda value: value.sequence)
                    if item.status
                    not in {
                        RecoveryActionStatus.SUCCEEDED,
                        RecoveryActionStatus.SKIPPED,
                    }
                ),
                None,
            )
            if action is not None and action.status in {
                RecoveryActionStatus.WAITING_WORKER,
                RecoveryActionStatus.FAILED,
            }:
                continue
            if action is None:
                continue
            await self._execute(change_set, plan.id, action)

    async def _execute(self, change_set, plan_id, action) -> None:
        if action.status is RecoveryActionStatus.PENDING:
            change_set = await self._delivery.record_recovery_action(
                RecordRecoveryActionCommand(
                    change_set.id,
                    plan_id,
                    action.id,
                    RecoveryActionStatus.RUNNING,
                    "Recovery action claimed by Saga executor.",
                )
            )
            plan = next(item for item in change_set.recovery_plans if item.id == plan_id)
            action = next(item for item in plan.actions if item.id == action.id)
        context = RecoveryExecutionContext(change_set, action)
        try:
            detail = await self._handler.execute(context)
        except RecoveryWaiting as error:
            # Not a failure: hand the action back as PENDING so this same
            # action is retried on the next interval instead of being skipped
            # forever, and leave the rest of the plan untouched.
            await self._delivery.record_recovery_action(
                RecordRecoveryActionCommand(
                    change_set.id,
                    plan_id,
                    action.id,
                    RecoveryActionStatus.PENDING,
                    f"Waiting: {error}",
                )
            )
            return
        except RevertConflict as error:
            if self._conflict_tasks is None:
                await self._record_failure(change_set.id, plan_id, action.id, str(error))
                return
            task_id = await self._conflict_tasks.create_for_conflict(context, str(error))
            await self._delivery.record_recovery_action(
                RecordRecoveryActionCommand(
                    change_set.id,
                    plan_id,
                    action.id,
                    RecoveryActionStatus.WAITING_WORKER,
                    f"Revert conflict assigned to Worker task {task_id}.",
                )
            )
            return
        except Exception as error:
            await self._record_failure(
                change_set.id, plan_id, action.id, f"{type(error).__name__}: {error}"
            )
            logger.exception("recovery action failed", extra={"action_id": str(action.id)})
            return
        await self._delivery.record_recovery_action(
            RecordRecoveryActionCommand(
                change_set.id,
                plan_id,
                action.id,
                RecoveryActionStatus.SUCCEEDED,
                detail,
            )
        )

    async def _record_failure(self, change_set_id, plan_id, action_id, detail) -> None:
        await self._delivery.record_recovery_action(
            RecordRecoveryActionCommand(
                change_set_id,
                plan_id,
                action_id,
                RecoveryActionStatus.FAILED,
                detail,
            )
        )

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception:
                logger.exception("recovery Saga scan failed")
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_seconds)


def requires_remote_confirmation(kind: RecoveryActionKind) -> bool:
    return kind in {
        RecoveryActionKind.CREATE_REVERT_PULL_REQUEST,
        RecoveryActionKind.MERGE_REVERT_PULL_REQUEST,
        RecoveryActionKind.CLOSE_PULL_REQUEST,
    }
