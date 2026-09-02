from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class BootstrapKind(StrEnum):
    CONFIGURE_EXECUTION_PLANE = "configure_execution_plane"


class BootstrapState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_FOR_USER = "waiting_for_user"
    RETRYABLE_FAILURE = "retryable_failure"
    TERMINAL_FAILURE = "terminal_failure"
    COMPLETED = "completed"


class BootstrapPhase(StrEnum):
    WAITING_FOR_MODEL = "waiting_for_model"
    INSTALLING_AGENTTEAMS = "installing_agentteams"
    VERIFYING_CONTROLLER = "verifying_controller"
    CONFIGURING_MATRIX = "configuring_matrix"
    CONFIGURING_STORAGE = "configuring_storage"
    WRITING_RUNTIME_CONFIG = "writing_runtime_config"
    RESTARTING_API = "restarting_api"
    VERIFYING_PLATFORM = "verifying_platform"
    COMPLETE = "complete"


class BootstrapErrorCode(StrEnum):
    DOCKER_UNAVAILABLE = "docker_unavailable"
    MODEL_CREDENTIAL_MISSING = "model_credential_missing"
    MODEL_CREDENTIAL_INVALID = "model_credential_invalid"
    IMAGE_PULL_FAILED = "image_pull_failed"
    AGENTTEAMS_INSTALL_FAILED = "agentteams_install_failed"
    CONTROLLER_UNHEALTHY = "controller_unhealthy"
    MATRIX_LOGIN_FAILED = "matrix_login_failed"
    STORAGE_CREDENTIALS_MISSING = "storage_credentials_missing"
    RUNTIME_CONFIG_WRITE_FAILED = "runtime_config_write_failed"
    API_RESTART_FAILED = "api_restart_failed"
    PLATFORM_VERIFICATION_FAILED = "platform_verification_failed"


ACTIVE_BOOTSTRAP_STATES = frozenset(
    {
        BootstrapState.PENDING,
        BootstrapState.RUNNING,
        BootstrapState.WAITING_FOR_USER,
        BootstrapState.RETRYABLE_FAILURE,
    }
)

_ALLOWED_TRANSITIONS = {
    BootstrapState.PENDING: frozenset(
        {BootstrapState.RUNNING, BootstrapState.WAITING_FOR_USER}
    ),
    BootstrapState.RUNNING: frozenset(
        {
            BootstrapState.RUNNING,
            BootstrapState.WAITING_FOR_USER,
            BootstrapState.RETRYABLE_FAILURE,
            BootstrapState.TERMINAL_FAILURE,
            BootstrapState.COMPLETED,
        }
    ),
    BootstrapState.WAITING_FOR_USER: frozenset({BootstrapState.PENDING}),
    BootstrapState.RETRYABLE_FAILURE: frozenset({BootstrapState.PENDING}),
    BootstrapState.TERMINAL_FAILURE: frozenset(),
    BootstrapState.COMPLETED: frozenset(),
}


class BootstrapTransitionError(RuntimeError):
    pass


class BootstrapExecutionError(RuntimeError):
    def __init__(
        self,
        code: BootstrapErrorCode,
        safe_detail: str,
        *,
        retryable: bool,
    ) -> None:
        if not safe_detail or len(safe_detail) > 2000:
            raise ValueError("bootstrap safe detail must contain 1-2000 characters")
        super().__init__(safe_detail)
        self.code = code
        self.safe_detail = safe_detail
        self.retryable = retryable


class BootstrapUserInputRequired(BootstrapExecutionError):
    def __init__(self, code: BootstrapErrorCode, safe_detail: str) -> None:
        super().__init__(code, safe_detail, retryable=False)


def assert_bootstrap_transition(
    current: BootstrapState,
    target: BootstrapState,
    phase: BootstrapPhase,
) -> None:
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise BootstrapTransitionError(f"cannot transition bootstrap from {current} to {target}")
    if target is BootstrapState.COMPLETED and phase is not BootstrapPhase.COMPLETE:
        raise BootstrapTransitionError("completed bootstrap must use the complete phase")
    if target is not BootstrapState.COMPLETED and phase is BootstrapPhase.COMPLETE:
        raise BootstrapTransitionError("complete phase requires completed bootstrap state")


@dataclass(frozen=True, slots=True)
class BootstrapOperation:
    id: UUID
    kind: BootstrapKind
    state: BootstrapState
    phase: BootstrapPhase
    attempt: int
    requested_by: UUID | None
    lease_owner: str | None
    lease_expires_at: datetime | None
    error_code: BootstrapErrorCode | None
    error_detail: str | None
    requested_at: datetime
    started_at: datetime | None
    updated_at: datetime
    finished_at: datetime | None


class BootstrapOperationStore(Protocol):
    async def ensure_requested(self, *, requested_by: UUID | None) -> BootstrapOperation: ...

    async def latest(self) -> BootstrapOperation | None: ...

    async def claim(
        self, lease_owner: str, *, lease_seconds: int = 300
    ) -> BootstrapOperation | None: ...

    async def renew(
        self, operation_id: UUID, lease_owner: str, *, lease_seconds: int = 300
    ) -> BootstrapOperation: ...

    async def transition(
        self,
        operation_id: UUID,
        *,
        target: BootstrapState,
        phase: BootstrapPhase,
        lease_owner: str | None = None,
        error_code: BootstrapErrorCode | None = None,
        error_detail: str | None = None,
    ) -> BootstrapOperation: ...

    async def retry(self, operation_id: UUID) -> BootstrapOperation: ...


class BootstrapExecutor(Protocol):
    async def execute(
        self,
        operation: BootstrapOperation,
        lease_owner: str,
    ) -> None: ...
