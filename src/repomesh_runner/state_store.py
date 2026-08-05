"""At-most-once execution across process restarts.

The dispatcher deduplicates by ``idempotencyKey``, but that only protects the control plane. A
Runner that is killed after finishing a task and restarted before the dispatcher observes the
terminal event would otherwise run the same coding task twice — with real side effects in a real
workspace. The ledger is the local half of that guarantee: a key is written with its terminal
status, and a key already present is never executed again.

The file is tiny (one line per task attempt) so the API is synchronous; the serve loop touches it
between tasks, never during one. Writes go through a temporary file and :func:`os.replace` so a
crash mid-write leaves the previous ledger intact rather than a truncated one.
"""

import json
import logging
import os
from pathlib import Path

_logger = logging.getLogger(__name__)

__all__ = ["TaskLedger"]

LEDGER_FILENAME = "task-ledger.json"
LEDGER_VERSION = 1


class TaskLedger:
    """Records the idempotency keys this Runner has already carried to a terminal status."""

    def __init__(self, state_dir: Path | str, *, filename: str = LEDGER_FILENAME) -> None:
        self._path = Path(state_dir) / filename
        self._entries = self._load()

    @property
    def path(self) -> Path:
        return self._path

    def seen(self, key: str) -> bool:
        return key in self._entries

    def record(self, key: str, status: str) -> None:
        self._entries[key] = status
        self._flush()

    def _load(self) -> dict[str, str]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as error:
            _logger.warning("task ledger could not be read (%s); starting empty", error)
            return {}

        try:
            document = json.loads(raw)
            tasks = document["tasks"]
            if not isinstance(tasks, dict):
                raise TypeError("tasks must be an object")
            return {str(key): str(value) for key, value in tasks.items()}
        except (ValueError, KeyError, TypeError) as error:
            _logger.warning("task ledger at %s is corrupt (%s); starting empty", self._path, error)
            return {}

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(self._path.name + ".tmp")
        document = {"version": LEDGER_VERSION, "tasks": self._entries}
        temporary.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self._path)
