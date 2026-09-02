import asyncio
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from repomesh.bootstrap_worker import BootstrapWorkerSettings
from repomesh.modules.platform_config import (
    BootstrapErrorCode,
    BootstrapExecutionError,
    BootstrapKind,
    BootstrapOperation,
    BootstrapPhase,
    BootstrapState,
    BootstrapTransitionError,
)
from repomesh.modules.platform_config.reconciler import BootstrapReconciler


def operation() -> BootstrapOperation:
    now = datetime.now(UTC)
    return BootstrapOperation(
        id=uuid4(),
        kind=BootstrapKind.CONFIGURE_EXECUTION_PLANE,
        state=BootstrapState.PENDING,
        phase=BootstrapPhase.INSTALLING_AGENTTEAMS,
        attempt=0,
        requested_by=None,
        lease_owner=None,
        lease_expires_at=None,
        error_code=None,
        error_detail=None,
        requested_at=now,
        started_at=None,
        updated_at=now,
        finished_at=None,
    )


class FakeStore:
    def __init__(self, current: BootstrapOperation | None) -> None:
        self.current = current
        self.claim_lock = asyncio.Lock()
        self.renewals = 0
        self.lose_lease = False

    async def claim(self, owner: str, *, lease_seconds: int = 300):
        async with self.claim_lock:
            if self.current is None or self.current.state is not BootstrapState.PENDING:
                return None
            now = datetime.now(UTC)
            self.current = replace(
                self.current,
                state=BootstrapState.RUNNING,
                attempt=self.current.attempt + 1,
                lease_owner=owner,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                started_at=now,
                updated_at=now,
            )
            return self.current

    async def latest(self):
        return self.current

    async def renew(self, operation_id, lease_owner, *, lease_seconds=300):
        assert self.current is not None and self.current.id == operation_id
        assert self.current.lease_owner == lease_owner
        if self.lose_lease:
            raise BootstrapTransitionError("lease owner changed")
        self.renewals += 1
        self.current = replace(
            self.current,
            lease_expires_at=datetime.now(UTC) + timedelta(seconds=lease_seconds),
        )
        return self.current

    async def transition(
        self,
        operation_id,
        *,
        target,
        phase,
        lease_owner=None,
        error_code=None,
        error_detail=None,
    ):
        assert self.current is not None and self.current.id == operation_id
        assert self.current.lease_owner == lease_owner
        self.current = replace(
            self.current,
            state=target,
            phase=phase,
            lease_owner=None if target is not BootstrapState.RUNNING else lease_owner,
            lease_expires_at=(
                None
                if target is not BootstrapState.RUNNING
                else self.current.lease_expires_at
            ),
            error_code=error_code,
            error_detail=error_detail,
            finished_at=(
                datetime.now(UTC)
                if target in {BootstrapState.COMPLETED, BootstrapState.TERMINAL_FAILURE}
                else None
            ),
        )
        return self.current


class RecordingExecutor:
    def __init__(self, error: Exception | None = None, delay: float = 0) -> None:
        self.calls = 0
        self.error = error
        self.delay = delay
        self.cancelled = False

    async def execute(self, operation, lease_owner) -> None:
        self.calls += 1
        if self.delay:
            try:
                await asyncio.sleep(self.delay)
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        if self.error is not None:
            raise self.error


def reconciler(store, executor, *, instance="bootstrap-a", heartbeat=None):
    return BootstrapReconciler(
        store,
        executor,
        instance_id=instance,
        poll_seconds=1,
        lease_seconds=30,
        heartbeat_seconds=heartbeat,
    )


@pytest.mark.asyncio
async def test_no_operation_does_not_call_executor() -> None:
    store = FakeStore(None)
    executor = RecordingExecutor()
    assert await reconciler(store, executor).run_once() is False
    assert executor.calls == 0


@pytest.mark.asyncio
async def test_dry_execution_claims_and_completes() -> None:
    store = FakeStore(operation())
    executor = RecordingExecutor()
    assert await reconciler(store, executor).run_once() is True
    assert executor.calls == 1
    assert store.current is not None
    assert store.current.state is BootstrapState.COMPLETED
    assert store.current.phase is BootstrapPhase.COMPLETE


@pytest.mark.asyncio
async def test_two_reconcilers_execute_one_operation_once() -> None:
    store = FakeStore(operation())
    first = RecordingExecutor(delay=0.02)
    second = RecordingExecutor()
    handled = await asyncio.gather(
        reconciler(store, first, instance="bootstrap-a").run_once(),
        reconciler(store, second, instance="bootstrap-b").run_once(),
    )
    assert sorted(handled) == [False, True]
    assert first.calls + second.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("retryable", [True, False])
async def test_declared_execution_error_maps_safe_state(retryable: bool) -> None:
    store = FakeStore(operation())
    executor = RecordingExecutor(
        BootstrapExecutionError(
            BootstrapErrorCode.IMAGE_PULL_FAILED,
            "image registry unavailable",
            retryable=retryable,
        )
    )
    await reconciler(store, executor, heartbeat=0.01).run_once()
    renewals_after_failure = store.renewals
    await asyncio.sleep(0.03)
    assert store.renewals == renewals_after_failure
    assert store.current is not None
    assert store.current.state is (
        BootstrapState.RETRYABLE_FAILURE
        if retryable
        else BootstrapState.TERMINAL_FAILURE
    )
    assert store.current.error_code is BootstrapErrorCode.IMAGE_PULL_FAILED
    assert store.current.error_detail == "image registry unavailable"


@pytest.mark.asyncio
async def test_unexpected_error_never_persists_or_logs_exception_text(caplog) -> None:
    sentinel = "secret-sentinel-must-not-escape"
    store = FakeStore(operation())
    executor = RecordingExecutor(RuntimeError(sentinel))
    with caplog.at_level(logging.ERROR, logger="repomesh.bootstrap"):
        await reconciler(store, executor).run_once()
    assert store.current is not None
    assert store.current.error_detail == "bootstrap executor failed unexpectedly"
    assert sentinel not in caplog.text


@pytest.mark.asyncio
async def test_heartbeat_renews_lease_during_execution() -> None:
    store = FakeStore(operation())
    executor = RecordingExecutor(delay=0.08)
    await reconciler(store, executor, heartbeat=0.02).run_once()
    assert store.renewals >= 2


@pytest.mark.asyncio
async def test_idle_loop_stops_without_waiting_for_poll_timeout() -> None:
    stop = asyncio.Event()
    runner = asyncio.create_task(
        reconciler(FakeStore(None), RecordingExecutor()).run_forever(stop)
    )
    await asyncio.sleep(0.02)
    stop.set()
    await asyncio.wait_for(runner, timeout=0.2)


@pytest.mark.asyncio
async def test_lost_lease_cancels_executor_without_stale_transition() -> None:
    store = FakeStore(operation())
    store.lose_lease = True
    executor = RecordingExecutor(delay=1)
    await reconciler(store, executor, heartbeat=0.01).run_once()
    assert executor.cancelled is True
    assert store.current is not None
    assert store.current.state is BootstrapState.RUNNING
    assert store.current.phase is BootstrapPhase.INSTALLING_AGENTTEAMS


def test_worker_settings_reject_unknown_mode(monkeypatch) -> None:
    monkeypatch.setenv("REPOMESH_BOOTSTRAP_MODE", "unknown")
    with pytest.raises(ValueError, match="disabled, dry-run, or production"):
        BootstrapWorkerSettings.from_environment()


def test_worker_settings_parse_test_only_once(monkeypatch) -> None:
    monkeypatch.setenv("REPOMESH_BOOTSTRAP_MODE", "dry-run")
    monkeypatch.setenv("REPOMESH_BOOTSTRAP_ONCE", "true")
    settings = BootstrapWorkerSettings.from_environment()
    assert settings.mode == "dry-run"
    assert settings.once is True
