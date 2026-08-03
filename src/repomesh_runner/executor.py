"""DriverExecutor: the first real implementation of the RunnerExecutor port.

Resolves a RunnerTask to a CLI profile, enforces the worker permission
boundary, delegates to the protocol driver for the profile's family, and maps
the driver result back onto the Runtime v1 result contract.
"""

from collections.abc import Callable, Mapping
from pathlib import Path

from repomesh_runner.contracts import (
    RunnerExecutionResult,
    RunnerPermissionMode,
    RunnerPermissions,
    RunnerResultStatus,
    RunnerTask,
)
from repomesh_runner.drivers.base import (
    DriverError,
    DriverEvent,
    DriverFamily,
    DriverRequest,
    DriverResult,
    DriverResultStatus,
    PermissionDecision,
    ProtocolDriver,
)
from repomesh_runner.drivers.supervision import resolve_binary
from repomesh_runner.profiles import CliProfile, get_profile

_RESULT_STATUS = {
    DriverResultStatus.SUCCEEDED: RunnerResultStatus.SUCCEEDED,
    DriverResultStatus.FAILED: RunnerResultStatus.FAILED,
    DriverResultStatus.TIMEOUT: RunnerResultStatus.FAILED,
    DriverResultStatus.INTERRUPTED: RunnerResultStatus.INTERRUPTED,
    DriverResultStatus.INPUT_REQUIRED: RunnerResultStatus.INPUT_REQUIRED,
}


class AllowlistPermissionPolicy:
    """Answers protocol permission callbacks from RunnerTask permissions.

    ESCALATE is the conservative answer under DEFAULT mode: the run stops as
    input_required instead of guessing on the worker's behalf.
    BYPASS_PERMISSIONS answers ALLOW unconditionally — including tools named in
    ``disallowed_tools``, because the mode means "do not ask, do not filter".
    Containment for such runs comes from the workspace and container scope.
    """

    def __init__(self, permissions: RunnerPermissions) -> None:
        self._mode = permissions.mode
        self._allowed = frozenset(permissions.allowed_tools)
        self._disallowed = frozenset(permissions.disallowed_tools)

    def decide(self, tool_name: str, tool_input: Mapping[str, object]) -> PermissionDecision:
        if self._mode is RunnerPermissionMode.BYPASS_PERMISSIONS:
            return PermissionDecision.ALLOW
        if tool_name in self._disallowed:
            return PermissionDecision.DENY
        autonomous = self._mode in (
            RunnerPermissionMode.ACCEPT_EDITS,
            RunnerPermissionMode.AUTO,
        )
        if self._allowed and tool_name not in self._allowed:
            return PermissionDecision.DENY if autonomous else PermissionDecision.ESCALATE
        return PermissionDecision.ALLOW if autonomous else PermissionDecision.ESCALATE


class DriverExecutor:
    def __init__(
        self,
        drivers: Mapping[DriverFamily, ProtocolDriver],
        workspace_root: Path,
        *,
        profile_resolver: Callable[[str], CliProfile] = get_profile,
        binary_resolver: Callable[[tuple[str, ...]], str | None] = resolve_binary,
        observer: Callable[[DriverEvent], None] | None = None,
    ) -> None:
        self._drivers = dict(drivers)
        self._workspace_root = workspace_root
        self._profile_resolver = profile_resolver
        self._binary_resolver = binary_resolver
        self._observer = observer or (lambda event: None)

    async def execute(self, task: RunnerTask) -> RunnerExecutionResult:
        profile = self._profile_resolver(task.adapter_id)
        if not profile.launchable:
            raise DriverError(f"{profile.id}: profile is not launchable")
        driver = self._drivers.get(profile.family)
        if driver is None:
            raise DriverError(f"{profile.id}: no driver registered for {profile.family.value}")
        executable = self._binary_resolver(profile.binaries)
        if executable is None:
            raise DriverError(f"{profile.id}: binary_not_found")

        workspace = self._workspace_root / str(task.run_id)
        workspace.mkdir(parents=True, exist_ok=True)
        request = DriverRequest(
            executable=executable,
            workspace=workspace,
            prompt=task.instruction,
            permission_policy=AllowlistPermissionPolicy(task.permissions),
            resume_session_id=task.resume_session_id if profile.resumable else None,
            extra_arguments=profile.permission_arguments.get(task.permissions.mode, ()),
        )
        result = await driver.execute(request, profile, self._observer)
        return self._to_runner_result(result)

    @staticmethod
    def _to_runner_result(result: DriverResult) -> RunnerExecutionResult:
        status = _RESULT_STATUS[result.status]
        summary = (
            result.summary
            if result.status is DriverResultStatus.SUCCEEDED
            else result.diagnostics or result.status.value
        )
        return RunnerExecutionResult(
            status=status,
            summary=summary,
            native_session_id=result.native_session_id,
        )


def build_default_executor(workspace_root: Path) -> DriverExecutor:
    """Assemble the executor with real drivers and the real process factory."""

    from repomesh_runner.drivers.acp import AcpDriver
    from repomesh_runner.drivers.app_server import AppServerDriver
    from repomesh_runner.drivers.stream_json import StreamJsonDriver
    from repomesh_runner.drivers.supervision import SubprocessFactory

    factory = SubprocessFactory()
    return DriverExecutor(
        drivers={
            DriverFamily.STREAM_JSON: StreamJsonDriver(factory),
            DriverFamily.ACP: AcpDriver(factory),
            DriverFamily.APP_SERVER: AppServerDriver(factory),
        },
        workspace_root=workspace_root,
    )
