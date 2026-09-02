import asyncio
import logging
from contextlib import suppress

from .bootstrap import (
    BootstrapErrorCode,
    BootstrapExecutionError,
    BootstrapExecutor,
    BootstrapOperation,
    BootstrapOperationStore,
    BootstrapPhase,
    BootstrapState,
    BootstrapUserInputRequired,
)

_LOGGER = logging.getLogger("repomesh.bootstrap")


class BootstrapLeaseLost(RuntimeError):
    pass


class BootstrapReconciler:
    def __init__(
        self,
        store: BootstrapOperationStore,
        executor: BootstrapExecutor,
        *,
        instance_id: str,
        poll_seconds: float = 2,
        lease_seconds: int = 300,
        heartbeat_seconds: float | None = None,
    ) -> None:
        if not instance_id.strip() or len(instance_id) > 128:
            raise ValueError("bootstrap instance id must contain 1-128 characters")
        if not 1 <= poll_seconds <= 60:
            raise ValueError("bootstrap poll seconds must be between 1 and 60")
        if not 30 <= lease_seconds <= 3600:
            raise ValueError("bootstrap lease seconds must be between 30 and 3600")
        if heartbeat_seconds is not None and heartbeat_seconds <= 0:
            raise ValueError("bootstrap heartbeat seconds must be positive")
        self._store = store
        self._executor = executor
        self._instance_id = instance_id
        self._poll_seconds = poll_seconds
        self._lease_seconds = lease_seconds
        self._heartbeat_seconds = heartbeat_seconds

    async def run_once(self) -> bool:
        operation = await self._store.claim(
            self._instance_id,
            lease_seconds=self._lease_seconds,
        )
        if operation is None:
            return False
        _LOGGER.info(
            "claimed bootstrap operation id=%s phase=%s attempt=%s",
            operation.id,
            operation.phase,
            operation.attempt,
        )
        try:
            await self._execute_with_heartbeat(operation)
            await self._store.transition(
                operation.id,
                target=BootstrapState.COMPLETED,
                phase=BootstrapPhase.COMPLETE,
                lease_owner=self._instance_id,
            )
            _LOGGER.info("completed bootstrap operation id=%s", operation.id)
        except BootstrapLeaseLost:
            _LOGGER.warning("lost bootstrap lease id=%s", operation.id)
        except BootstrapUserInputRequired as error:
            await self._store.transition(
                operation.id,
                target=BootstrapState.WAITING_FOR_USER,
                phase=BootstrapPhase.WAITING_FOR_MODEL,
                lease_owner=self._instance_id,
                error_code=error.code,
                error_detail=error.safe_detail,
            )
            _LOGGER.info(
                "bootstrap operation waiting for user id=%s code=%s",
                operation.id,
                error.code,
            )
        except BootstrapExecutionError as error:
            target = (
                BootstrapState.RETRYABLE_FAILURE
                if error.retryable
                else BootstrapState.TERMINAL_FAILURE
            )
            phase = await self._current_phase(operation)
            await self._store.transition(
                operation.id,
                target=target,
                phase=phase,
                lease_owner=self._instance_id,
                error_code=error.code,
                error_detail=error.safe_detail,
            )
            _LOGGER.warning(
                "bootstrap operation failed id=%s code=%s retryable=%s",
                operation.id,
                error.code,
                error.retryable,
            )
        except Exception:
            phase = await self._current_phase(operation)
            await self._store.transition(
                operation.id,
                target=BootstrapState.RETRYABLE_FAILURE,
                phase=phase,
                lease_owner=self._instance_id,
                error_code=BootstrapErrorCode.PLATFORM_VERIFICATION_FAILED,
                error_detail="bootstrap executor failed unexpectedly",
            )
            _LOGGER.error(
                "unexpected bootstrap executor failure id=%s code=%s",
                operation.id,
                BootstrapErrorCode.PLATFORM_VERIFICATION_FAILED,
            )
        return True

    async def _current_phase(self, operation: BootstrapOperation) -> BootstrapPhase:
        latest = await self._store.latest()
        return latest.phase if latest is not None and latest.id == operation.id else operation.phase

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            handled = await self.run_once()
            if handled:
                continue
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self._poll_seconds)

    async def _execute_with_heartbeat(self, operation: BootstrapOperation) -> None:
        heartbeat_stop = asyncio.Event()
        execution = asyncio.create_task(
            self._executor.execute(operation, self._instance_id),
            name=f"bootstrap-executor-{operation.id}",
        )
        heartbeat = asyncio.create_task(
            self._heartbeat(operation, heartbeat_stop),
            name=f"bootstrap-heartbeat-{operation.id}",
        )
        done, _ = await asyncio.wait(
            {execution, heartbeat},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if heartbeat in done:
            heartbeat_error = heartbeat.exception()
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
            if heartbeat_error is not None:
                raise BootstrapLeaseLost from heartbeat_error
            raise BootstrapLeaseLost("bootstrap heartbeat stopped unexpectedly")
        try:
            await execution
        finally:
            heartbeat_stop.set()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _heartbeat(
        self,
        operation: BootstrapOperation,
        stop: asyncio.Event,
    ) -> None:
        interval = self._heartbeat_seconds or max(
            1.0,
            min(self._lease_seconds / 3, 30.0),
        )
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                await self._store.renew(
                    operation.id,
                    self._instance_id,
                    lease_seconds=self._lease_seconds,
                )


class DryRunBootstrapExecutor:
    async def execute(
        self,
        operation: BootstrapOperation,
        lease_owner: str,
    ) -> None:
        _LOGGER.info(
            "dry-run bootstrap executor id=%s phase=%s owner=%s",
            operation.id,
            operation.phase,
            lease_owner,
        )
