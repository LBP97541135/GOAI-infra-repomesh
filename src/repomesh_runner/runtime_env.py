"""The worker runtime environment contract of ``contracts/runtime/v1/worker-runtime.md``.

Environment variables fall into exactly three classes and this module is the single place that
decides which class a name belongs to:

* **Consumed** — the task source endpoint, the event delivery endpoint, the workspace root, and the
  optional state directory / poll timeout, plus the pass-through ``REPOMESH_LABEL_*`` values that
  carry the ``repomesh.dev/*`` labels of the worker.
* **Rejected** — permission-bearing variables. Permissions reach the Runner only through the
  ``permissions`` block of a RunnerTask, never through the process environment. Presence of such a
  variable stops the process before it serves anything.
* **Ignored** — everything else, silently: Matrix credentials and the OpenClaw-family variables of
  the surrounding AgentTeams image.

:func:`load_runtime_env` is a pure function of the mapping it is handed (apart from the workspace
directory existence check), so callers pass ``os.environ`` explicitly and tests pass a dict.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

_logger = logging.getLogger(__name__)

__all__ = [
    "CONSUMED_VARIABLES",
    "LABEL_PREFIX",
    "REJECTED_VARIABLES",
    "RunnerRuntimeEnv",
    "RuntimeEnvError",
    "load_runtime_env",
]

TASK_SOURCE_URL = "REPOMESH_RUNNER_TASK_SOURCE_URL"
EVENT_SINK_URL = "REPOMESH_RUNNER_EVENT_SINK_URL"
WORKSPACE_ROOT = "REPOMESH_RUNNER_WORKSPACE_ROOT"
STATE_DIR = "REPOMESH_RUNNER_STATE_DIR"
POLL_TIMEOUT_SECONDS = "REPOMESH_RUNNER_POLL_TIMEOUT_SECONDS"
CONTROL_TOKEN = "REPOMESH_RUNNER_CONTROL_TOKEN"
WORKSPACE_PATH_FROM = "REPOMESH_RUNNER_WORKSPACE_PATH_FROM"
WORKSPACE_PATH_TO = "REPOMESH_RUNNER_WORKSPACE_PATH_TO"
ADAPTERS = "REPOMESH_RUNNER_ADAPTERS"

LABEL_PREFIX = "REPOMESH_LABEL_"

CONSUMED_VARIABLES = frozenset(
    {
        TASK_SOURCE_URL,
        EVENT_SINK_URL,
        WORKSPACE_ROOT,
        STATE_DIR,
        POLL_TIMEOUT_SECONDS,
        CONTROL_TOKEN,
        WORKSPACE_PATH_FROM,
        WORKSPACE_PATH_TO,
        ADAPTERS,
    }
)

#: Permission-bearing variables. The contract forbids reading permissions from the environment, so
#: rather than ignoring these the Runner refuses to start: a deployment that sets one believes it is
#: granting something, and silently dropping it would be worse than failing loudly.
REJECTED_VARIABLES = frozenset(
    {
        "AGENTTEAMS_YOLO",
        "AGENTTEAMS_DANGEROUSLY_SKIP_PERMISSIONS",
        "CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS",
        "REPOMESH_RUNNER_ALLOWED_TOOLS",
        "REPOMESH_RUNNER_BYPASS_PERMISSIONS",
        "REPOMESH_RUNNER_PERMISSION_MODE",
    }
)

DEFAULT_POLL_TIMEOUT_SECONDS = 30.0
DEFAULT_STATE_DIRNAME = ".runner-state"


class RuntimeEnvError(RuntimeError):
    """Raised when the process environment does not satisfy the worker runtime contract."""


@dataclass(frozen=True, slots=True)
class RunnerRuntimeEnv:
    """The consumed slice of the environment, resolved and validated."""

    task_source_url: str
    event_sink_url: str
    workspace_root: Path
    state_dir: Path
    poll_timeout_seconds: float = DEFAULT_POLL_TIMEOUT_SECONDS
    labels: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    control_token: str | None = None
    workspace_path_from: str | None = None
    workspace_path_to: str | None = None
    #: Profile ids this Runner advertises when it leases. Empty means "decide at
    #: startup": every launchable profile whose binary is on this host.
    adapters: tuple[str, ...] = ()


def load_runtime_env(environ: Mapping[str, str]) -> RunnerRuntimeEnv:
    """Resolve the runtime environment.

    Raises:
        RuntimeEnvError: a required variable is missing or malformed, or a rejected
            permission-bearing variable is present.
    """

    _reject_permission_variables(environ)

    workspace_root = Path(_required(environ, WORKSPACE_ROOT))
    if not workspace_root.is_dir():
        raise RuntimeEnvError(f"{WORKSPACE_ROOT} must point at an existing directory")

    raw_state_dir = _optional(environ, STATE_DIR)
    state_dir = Path(raw_state_dir) if raw_state_dir else workspace_root / DEFAULT_STATE_DIRNAME

    path_from = _optional(environ, WORKSPACE_PATH_FROM)
    path_to = _optional(environ, WORKSPACE_PATH_TO)
    if bool(path_from) != bool(path_to):
        raise RuntimeEnvError(
            f"{WORKSPACE_PATH_FROM} and {WORKSPACE_PATH_TO} must be configured together"
        )
    return RunnerRuntimeEnv(
        task_source_url=_required(environ, TASK_SOURCE_URL),
        event_sink_url=_required(environ, EVENT_SINK_URL),
        workspace_root=workspace_root,
        state_dir=state_dir,
        poll_timeout_seconds=_poll_timeout(environ),
        labels=_labels(environ),
        control_token=_optional(environ, CONTROL_TOKEN),
        workspace_path_from=path_from,
        workspace_path_to=path_to,
        adapters=_adapters(environ),
    )


def _reject_permission_variables(environ: Mapping[str, str]) -> None:
    present = sorted(name for name in environ if name in REJECTED_VARIABLES)
    if present:
        raise RuntimeEnvError(
            "permission-bearing environment variables are rejected by the runtime contract: "
            + ", ".join(present)
            + "; permissions are carried by the RunnerTask only"
        )


def _required(environ: Mapping[str, str], name: str) -> str:
    value = _optional(environ, name)
    if not value:
        raise RuntimeEnvError(f"{name} is required")
    return value


def _optional(environ: Mapping[str, str], name: str) -> str | None:
    value = environ.get(name)
    return value.strip() if value is not None else None


def _adapters(environ: Mapping[str, str]) -> tuple[str, ...]:
    """``REPOMESH_RUNNER_ADAPTERS``: the profile ids this Runner advertises when leasing.

    Comma separated, trimmed, first occurrence wins. Unset or blank means "work it out at
    startup" (every launchable profile whose binary is on this host); a value that names nothing
    usable is a misconfiguration rather than an empty list, because a Runner that advertises
    nothing is refused every lease.
    """

    raw = _optional(environ, ADAPTERS)
    if not raw:
        return ()
    names = tuple(dict.fromkeys(part.strip() for part in raw.split(",") if part.strip()))
    if not names:
        raise RuntimeEnvError(f"{ADAPTERS} must list at least one adapter id")
    return names


def _poll_timeout(environ: Mapping[str, str]) -> float:
    raw = _optional(environ, POLL_TIMEOUT_SECONDS)
    if not raw:
        return DEFAULT_POLL_TIMEOUT_SECONDS
    try:
        timeout = float(raw)
    except ValueError as error:
        raise RuntimeEnvError(f"{POLL_TIMEOUT_SECONDS} must be a number") from error
    if timeout <= 0:
        raise RuntimeEnvError(f"{POLL_TIMEOUT_SECONDS} must be positive")
    return timeout


def _labels(environ: Mapping[str, str]) -> Mapping[str, str]:
    """Collect ``REPOMESH_LABEL_*`` pass-through values.

    The suffix is lowercased and underscores become hyphens, which is the round trip of the
    ``repomesh.dev/<name>`` label names an environment variable cannot spell verbatim.
    """

    labels = {
        name[len(LABEL_PREFIX) :].lower().replace("_", "-"): value
        for name, value in environ.items()
        if name.startswith(LABEL_PREFIX) and len(name) > len(LABEL_PREFIX)
    }
    if labels:
        _logger.debug("runtime labels resolved: %s", ", ".join(sorted(labels)))
    return MappingProxyType(labels)
