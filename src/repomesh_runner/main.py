"""The Runner worker process: PID 1 of a ``repomesh-runner`` AgentTeams worker.

The process is a loop and nothing else. It asks the task source for work, runs one task at a time
through :class:`ExecuteRunnerTask`, and records the terminal status in the local ledger. There is no
heartbeat: the runtime contract states that liveness is not what keeps this worker alive, so
emitting one would invent a signal the control plane does not read.

Two failure rules shape the loop. A task that blows up must not take the worker with it — the
exception is logged, the key is recorded as failed if execution actually started, and the loop asks
for the next task. And a task already in the ledger is skipped without re-executing *and* without
re-emitting events, because the ledger's whole purpose is that a restart is not a second run.

Shutdown is cooperative. SIGTERM sets an event; the in-flight task finishes and its terminal event
(``interrupted`` when the driver was cut short) is published before the loop returns. The grace
period and the SIGKILL that follows belong to the container runtime.
"""

import asyncio
import logging
import os
import signal
import sys
from collections.abc import Mapping, Sequence

from .contracts import RunnerExecutionResult, RunnerResultStatus, RunnerTask
from .engine import ExecuteRunnerTask, RunnerEventSink, RunnerExecutor
from .event_sink import EventDeliveryError, HttpEventSink
from .executor import build_default_executor
from .runtime_env import RunnerRuntimeEnv, RuntimeEnvError, load_runtime_env
from .state_store import TaskLedger
from .task_source import HttpLongPollTaskSource, TaskSource

_logger = logging.getLogger(__name__)

__all__ = ["Shutdown", "install_signal_handlers", "run", "serve"]

EXIT_OK = 0
EXIT_ENVIRONMENT = 2


class Shutdown:
    """Cooperative stop signal shared by the signal handlers and the serve loop."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def set(self) -> None:
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await self._event.wait()


class _ExecutionWitness:
    """Wraps an executor to record whether the current task actually started executing.

    An ``EventDeliveryError`` on the ``accepted`` event means nothing ran and the task must stay
    unrecorded so it can be redelivered; the same error on the terminal event means the work is
    done and the key must be burned. From outside the use case the two look identical, so the
    witness observes the boundary the use case crosses.
    """

    def __init__(self, executor: RunnerExecutor) -> None:
        self._executor = executor
        self.started = False

    def reset(self) -> None:
        self.started = False

    async def execute(self, task: RunnerTask) -> RunnerExecutionResult:
        self.started = True
        return await self._executor.execute(task)


def install_signal_handlers(shutdown: Shutdown) -> None:
    """Route SIGTERM/SIGINT to the shutdown event, with a Windows fallback."""

    loop = asyncio.get_running_loop()
    for number in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(number, shutdown.set)
        except NotImplementedError:
            signal.signal(number, lambda *_: shutdown.set())


async def serve(
    *,
    source: TaskSource,
    sink: RunnerEventSink,
    executor: RunnerExecutor,
    ledger: TaskLedger,
    shutdown: Shutdown,
) -> None:
    """Run tasks until ``shutdown`` is set, finishing whatever is in flight."""

    witness = _ExecutionWitness(executor)
    service = ExecuteRunnerTask(witness, sink)

    while not shutdown.is_set():
        task = await source.next_task()
        if task is None:
            continue

        key = task.idempotency_key
        if ledger.seen(key):
            _logger.info("skipping task already recorded under idempotency key %s", key)
            continue

        witness.reset()
        try:
            result = await service.execute(task)
        except EventDeliveryError as error:
            _logger.error("event delivery failed for task %s: %s", key, error)
            _record_failure_if_started(ledger, witness, key)
        except Exception:
            _logger.exception("task %s failed", key)
            _record_failure_if_started(ledger, witness, key)
        else:
            ledger.record(key, result.status.value)
            _logger.info("task %s finished with status %s", key, result.status.value)

    _logger.info("shutdown requested; serve loop stopped")


def _record_failure_if_started(ledger: TaskLedger, witness: _ExecutionWitness, key: str) -> None:
    if witness.started:
        ledger.record(key, RunnerResultStatus.FAILED.value)
    else:
        _logger.warning("task %s never started; leaving it unrecorded for redelivery", key)


def run(environ: Mapping[str, str] = os.environ, argv: Sequence[str] | None = None) -> int:
    """Process entrypoint. Returns the exit code."""

    del argv  # the runtime contract configures the Runner through the environment only
    _configure_logging()

    try:
        env = load_runtime_env(environ)
    except RuntimeEnvError as error:
        print(f"repomesh-runner: {error}", file=sys.stderr)
        return EXIT_ENVIRONMENT

    _logger.info(
        "starting runner: workspace_root=%s state_dir=%s labels=%s",
        env.workspace_root,
        env.state_dir,
        ",".join(sorted(env.labels)) or "-",
    )
    asyncio.run(_serve_forever(env))
    return EXIT_OK


async def _serve_forever(env: RunnerRuntimeEnv) -> None:
    shutdown = Shutdown()
    install_signal_handlers(shutdown)

    source = HttpLongPollTaskSource(
        env.task_source_url,
        timeout_seconds=env.poll_timeout_seconds,
    )
    sink = HttpEventSink(env.event_sink_url)
    try:
        await serve(
            source=source,
            sink=sink,
            executor=build_default_executor(env.workspace_root),
            ledger=TaskLedger(env.state_dir),
            shutdown=shutdown,
        )
    finally:
        await source.aclose()
        await sink.aclose()


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
