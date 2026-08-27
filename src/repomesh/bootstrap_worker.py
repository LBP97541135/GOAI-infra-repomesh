import asyncio
import logging
import os
import signal
import socket
from dataclasses import dataclass
from pathlib import Path

from repomesh.integrations.bootstrap import (
    AgentTeamsBootstrapExecutor,
    AsyncDockerCommandRunner,
    BootstrapCommandRunner,
    DockerComposeApiTargetSelector,
)
from repomesh.modules.platform_config import (
    PostgresBootstrapOperationStore,
    PostgresPlatformCredentialStore,
)
from repomesh.modules.platform_config.reconciler import (
    BootstrapReconciler,
    DryRunBootstrapExecutor,
)
from repomesh.persistence import Database
from repomesh.settings import get_settings

_LOGGER = logging.getLogger("repomesh.bootstrap")


@dataclass(frozen=True, slots=True)
class BootstrapWorkerSettings:
    mode: str
    poll_seconds: float
    lease_seconds: int
    instance_id: str
    health_file: Path
    once: bool

    @classmethod
    def from_environment(cls) -> "BootstrapWorkerSettings":
        mode = os.environ.get("REPOMESH_BOOTSTRAP_MODE", "disabled").strip().lower()
        if mode not in {"disabled", "dry-run", "production"}:
            raise ValueError("REPOMESH_BOOTSTRAP_MODE must be disabled, dry-run, or production")
        return cls(
            mode=mode,
            poll_seconds=float(os.environ.get("REPOMESH_BOOTSTRAP_POLL_SECONDS", "2")),
            lease_seconds=int(os.environ.get("REPOMESH_BOOTSTRAP_LEASE_SECONDS", "300")),
            instance_id=os.environ.get("REPOMESH_BOOTSTRAP_INSTANCE_ID", socket.gethostname()),
            health_file=Path(
                os.environ.get(
                    "REPOMESH_BOOTSTRAP_HEALTH_FILE",
                    "/tmp/repomesh-bootstrap-ready",
                )
            ),
            once=os.environ.get("REPOMESH_BOOTSTRAP_ONCE", "false").lower()
            in {"1", "true", "yes"},
        )


async def run_worker() -> None:
    worker = BootstrapWorkerSettings.from_environment()
    database = Database(get_settings().database_url)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for event in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(event, stop.set)
    try:
        if not await database.is_ready():
            raise RuntimeError("bootstrap database is not ready")
        worker.health_file.write_text("ready\n", encoding="ascii")
        _LOGGER.info("bootstrap worker ready mode=%s instance=%s", worker.mode, worker.instance_id)
        if worker.mode == "disabled":
            await stop.wait()
            return
        operation_store = PostgresBootstrapOperationStore(database)
        executor = (
            DryRunBootstrapExecutor()
            if worker.mode == "dry-run"
            else AgentTeamsBootstrapExecutor(
                PostgresPlatformCredentialStore(database),
                operation_store,
                BootstrapCommandRunner(),
                DockerComposeApiTargetSelector(AsyncDockerCommandRunner()),
            )
        )
        reconciler = BootstrapReconciler(
            operation_store,
            executor,
            instance_id=worker.instance_id,
            poll_seconds=worker.poll_seconds,
            lease_seconds=worker.lease_seconds,
        )
        if worker.once:
            await reconciler.run_once()
            return
        await reconciler.run_forever(stop)
    finally:
        worker.health_file.unlink(missing_ok=True)
        await database.dispose()


def main() -> None:
    logging.basicConfig(level=os.environ.get("REPOMESH_LOG_LEVEL", "INFO"))
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
