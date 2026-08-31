"""One live Bridge per worker identity, enforced by the operating system.

Two Bridge processes serving the same worker would each answer the same Matrix
mentions and each lease the same worker's tasks, so the second one must fail
fast rather than compete (plan risk R8).

The mechanism is an OS lock on an open file handle, not an ``O_EXCL`` sentinel
file: a sentinel outlives the process that crashed and needs a liveness heuristic
to clean up, while a lock is released by the kernel the moment the holder's
handle closes for any reason, crash included. The handle therefore stays open for
as long as ``run`` is alive; releasing it is what ends the claim.

The lock file's contents are deliberately empty. A pid written into it would be
a fact about a process that may no longer exist, and nothing here would ever
read it back — the lock itself already answers the only question being asked.

Platform note: ``msvcrt.locking(LK_NBLCK)`` on Windows and
``fcntl.flock(LOCK_EX | LOCK_NB)`` elsewhere are both per-handle, so a second
handle conflicts even inside the same process. That is what lets the mutual
exclusion be tested with two real handles instead of a mocked lock function.
"""

import os
import sys
from pathlib import Path
from uuid import UUID

from .contracts import BridgeStartupError

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

__all__ = [
    "InstanceAlreadyRunning",
    "InstanceLock",
    "default_state_dir",
    "instance_lock_path",
]

_LOCK_BYTES = 1


class InstanceAlreadyRunning(BridgeStartupError):
    """Another Bridge process already serves this worker."""


def default_state_dir() -> Path:
    """Per-user state, never the repository.

    Hand-rolled rather than taken from ``platformdirs``: two branches do not
    justify a dependency in a process an operator installs on their own machine.
    """

    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return base / "repomesh-agent-bridge"
    return Path.home() / ".local" / "state" / "repomesh-agent-bridge"


def instance_lock_path(worker_agent_id: UUID, state_dir: Path | None = None) -> Path:
    """Derive the lock path from the worker identity, which is what is exclusive."""

    return (state_dir or default_state_dir()) / "locks" / f"{worker_agent_id}.lock"


class InstanceLock:
    """A non-blocking exclusive claim on one worker identity."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self) -> None:
        """Claim the worker, or raise :class:`InstanceAlreadyRunning`."""

        if self._fd is not None:
            raise InstanceAlreadyRunning(f"this instance already holds {self._path}")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            _take(fd)
        except OSError as busy:
            os.close(fd)
            raise InstanceAlreadyRunning(
                f"another bridge instance already holds {self._path}"
            ) from busy
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd

    def release(self) -> None:
        """Give the claim back. A no-op when this instance never held it."""

        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            _give_back(fd)
        finally:
            os.close(fd)


def _take(fd: int) -> None:
    if sys.platform == "win32":
        msvcrt.locking(fd, msvcrt.LK_NBLCK, _LOCK_BYTES)
    else:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _give_back(fd: int) -> None:
    if sys.platform == "win32":
        msvcrt.locking(fd, msvcrt.LK_UNLCK, _LOCK_BYTES)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
