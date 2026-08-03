from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol


class AuthStatus(StrEnum):
    AUTHORIZED = "authorized"
    UNAUTHORIZED = "unauthorized"
    UNKNOWN = "unknown"


class PermissionMode(StrEnum):
    DEFAULT = "default"
    ACCEPT_EDITS = "accept_edits"
    AUTO = "auto"
    BYPASS_PERMISSIONS = "bypass_permissions"


class SessionKind(StrEnum):
    WORKER = "worker"
    REVIEWER = "reviewer"
    MANAGER = "manager"


class PromptDelivery(StrEnum):
    IN_COMMAND = "in_command"
    AFTER_START = "after_start"
    CUSTOM_AGENT = "custom_agent"


class FeedbackKind(StrEnum):
    CI = "ci"
    REVIEW = "review"
    CONFLICT = "conflict"
    COORDINATION = "coordination"


class AdapterCapability(StrEnum):
    AUTH_PROBE = "auth_probe"
    NATIVE_SESSION = "native_session"
    RESTORE = "restore"
    FEEDBACK = "feedback"


@dataclass(frozen=True, slots=True)
class AdapterManifest:
    id: str
    display_name: str
    binary_names: tuple[str, ...]
    prompt_delivery: PromptDelivery
    capabilities: frozenset[AdapterCapability]
    source_revision: str
    # Whether this adapter's launch shape has been validated against the real
    # CLI, or is superseded by a verified Runner driver profile. Consumers must
    # not treat a listed adapter as executable without checking this.
    execution_status: str = "unverified"


@dataclass(frozen=True, slots=True)
class AdapterProbe:
    adapter_id: str
    installed: bool
    executable: str | None
    auth_status: AuthStatus
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class AgentLaunchRequest:
    workspace_path: Path
    prompt: str
    session_id: str
    permission_mode: PermissionMode = PermissionMode.DEFAULT
    session_kind: SessionKind = SessionKind.WORKER
    model: str | None = None
    system_prompt: str | None = None
    system_prompt_file: Path | None = None
    environment: Mapping[str, str] = field(default_factory=dict)
    allowed_tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LaunchPlan:
    adapter_id: str
    executable: str
    arguments: tuple[str, ...]
    working_directory: Path
    environment: Mapping[str, str]
    prompt_delivery: PromptDelivery
    prompt_after_start: str | None = None

    @property
    def argv(self) -> tuple[str, ...]:
        return (self.executable, *self.arguments)


@dataclass(frozen=True, slots=True)
class AgentSessionRef:
    native_session_id: str
    workspace_path: Path
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentSessionInfo:
    native_session_id: str
    title: str | None = None
    summary: str | None = None


@dataclass(frozen=True, slots=True)
class AgentFeedback:
    kind: FeedbackKind
    summary: str
    details: str
    source_url: str | None = None


@dataclass(frozen=True, slots=True)
class FeedbackDelivery:
    prompt: str
    delivery: PromptDelivery = PromptDelivery.AFTER_START


class AgentAdapter(Protocol):
    @property
    def manifest(self) -> AdapterManifest: ...

    async def probe(self) -> AdapterProbe: ...

    def build_launch(self, request: AgentLaunchRequest) -> LaunchPlan: ...

    def build_restore(
        self, request: AgentLaunchRequest, session: AgentSessionRef
    ) -> LaunchPlan | None: ...

    def session_info(self, metadata: Mapping[str, str]) -> AgentSessionInfo | None: ...

    def receive_feedback(self, feedback: AgentFeedback) -> FeedbackDelivery: ...


class AgentAdapterRegistry(Protocol):
    def list_manifests(self) -> tuple[AdapterManifest, ...]: ...

    def resolve(self, adapter_id: str) -> AgentAdapter: ...
