"""Frozen driver contract for the RepoMesh Runner execution layer.

See docs/adr/0003-runner-protocol-drivers.md and
docs/development/runner-driver-spec.md. Interface changes here require a spec
update in the same pull request.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from repomesh_runner.profiles import CliProfile

MAX_SESSION_ID_LENGTH = 512


class DriverFamily(StrEnum):
    STREAM_JSON = "stream_json"
    ACP = "acp"
    APP_SERVER = "app_server"
    ONE_SHOT = "one_shot"


class DriverResultStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    INPUT_REQUIRED = "input_required"
    TIMEOUT = "timeout"


class PermissionDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ESCALATE = "escalate"


class PermissionPolicy(Protocol):
    def decide(self, tool_name: str, tool_input: Mapping[str, object]) -> PermissionDecision: ...


class DriverEventKind(StrEnum):
    SESSION_STARTED = "session_started"
    TEXT = "text"
    THINKING = "thinking"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    PERMISSION_REQUEST = "permission_request"
    LOG = "log"


@dataclass(frozen=True, slots=True)
class DriverEvent:
    kind: DriverEventKind
    payload: Mapping[str, object] = field(default_factory=dict)


DriverObserver = Callable[[DriverEvent], None]


@dataclass(frozen=True, slots=True)
class DriverRequest:
    executable: str
    workspace: Path
    prompt: str
    permission_policy: PermissionPolicy
    environment: Mapping[str, str] = field(default_factory=dict)
    model: str | None = None
    system_prompt: str | None = None
    resume_session_id: str | None = None
    extra_arguments: tuple[str, ...] = ()
    idle_window_seconds: float = 180.0
    tool_window_seconds: float = 900.0

    def __post_init__(self) -> None:
        if not self.executable.strip():
            raise ValueError("executable is required")
        if not self.prompt.strip():
            raise ValueError("prompt is required")
        if self.idle_window_seconds <= 0 or self.tool_window_seconds <= 0:
            raise ValueError("watchdog windows must be positive")


@dataclass(frozen=True, slots=True)
class DriverResult:
    """Terminal outcome of one driver execution.

    Fail-closed invariant: ``summary`` carries deliverable text only when
    ``status is SUCCEEDED``; every other status must leave it empty and put
    explanation into ``diagnostics``.
    """

    status: DriverResultStatus
    summary: str = ""
    native_session_id: str | None = None
    transcript_path: str | None = None
    diagnostics: str = ""
    tool_call_count: int = 0

    def __post_init__(self) -> None:
        if self.status is not DriverResultStatus.SUCCEEDED and self.summary:
            raise ValueError("non-succeeded results must not carry a summary")
        if self.tool_call_count < 0:
            raise ValueError("tool_call_count cannot be negative")


class DriverError(RuntimeError):
    """Unrecoverable driver-level failure raised before a result can form."""


class ProtocolDriver(Protocol):
    @property
    def family(self) -> DriverFamily: ...

    async def execute(
        self,
        request: DriverRequest,
        profile: "CliProfile",
        observer: DriverObserver,
    ) -> DriverResult: ...


def sanitize_session_id(value: object) -> str | None:
    """Normalize a provider-reported session id, or drop it.

    Ids get interpolated into resume command lines later, so anything that
    could read as a flag or corrupt a terminal is rejected rather than kept.
    """

    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > MAX_SESSION_ID_LENGTH:
        return None
    if candidate.startswith("-"):
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in candidate):
        return None
    return candidate
