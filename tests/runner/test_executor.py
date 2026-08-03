from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from repomesh_runner.contracts import (
    ContextBundleRef,
    RepositoryCheckout,
    RunnerPermissionMode,
    RunnerPermissions,
    RunnerResultStatus,
    RunnerTask,
)
from repomesh_runner.drivers.base import (
    DriverError,
    DriverFamily,
    DriverRequest,
    DriverResult,
    DriverResultStatus,
    PermissionDecision,
)
from repomesh_runner.drivers.stream_json import build_arguments
from repomesh_runner.executor import AllowlistPermissionPolicy, DriverExecutor
from repomesh_runner.profiles import get_profile


def make_task(**overrides: object) -> RunnerTask:
    values: dict[str, object] = {
        "organization_id": uuid4(),
        "project_id": uuid4(),
        "task_id": uuid4(),
        "run_id": uuid4(),
        "correlation_id": uuid4(),
        "attempt": 1,
        "adapter_id": "claude-code",
        "instruction": "fix the failing test",
        "repository": RepositoryCheckout(uuid4(), "https://example.com/repo.git", "main"),
        "context_bundle": ContextBundleRef(
            uuid4(), 1, "s3://bundle/manifest", "sha256:" + "0" * 64
        ),
        "permissions": RunnerPermissions(mode=RunnerPermissionMode.ACCEPT_EDITS),
        "idempotency_key": "task-key",
        "issued_at": datetime.now(UTC),
    }
    values.update(overrides)
    return RunnerTask(**values)  # type: ignore[arg-type]


class RecordingDriver:
    def __init__(self, result: DriverResult) -> None:
        self.result = result
        self.requests: list[DriverRequest] = []

    @property
    def family(self) -> DriverFamily:
        return DriverFamily.STREAM_JSON

    async def execute(self, request, profile, observer) -> DriverResult:  # type: ignore[no-untyped-def]
        self.requests.append(request)
        return self.result


def make_executor(driver: RecordingDriver, tmp_path: Path) -> DriverExecutor:
    return DriverExecutor(
        drivers={DriverFamily.STREAM_JSON: driver},
        workspace_root=tmp_path,
        binary_resolver=lambda names: f"C:/fake/{names[0]}.exe",
    )


class TestAllowlistPermissionPolicy:
    def test_disallowed_wins_over_everything(self) -> None:
        policy = AllowlistPermissionPolicy(
            RunnerPermissions(
                mode=RunnerPermissionMode.ACCEPT_EDITS,
                allowed_tools=("Edit",),
                disallowed_tools=("Bash",),
            )
        )
        assert policy.decide("Bash", {}) is PermissionDecision.DENY

    def test_autonomous_modes_allow_and_deny(self) -> None:
        policy = AllowlistPermissionPolicy(
            RunnerPermissions(mode=RunnerPermissionMode.ACCEPT_EDITS, allowed_tools=("Edit",))
        )
        assert policy.decide("Edit", {}) is PermissionDecision.ALLOW
        assert policy.decide("WebFetch", {}) is PermissionDecision.DENY

    def test_default_mode_escalates(self) -> None:
        policy = AllowlistPermissionPolicy(RunnerPermissions(mode=RunnerPermissionMode.DEFAULT))
        assert policy.decide("Edit", {}) is PermissionDecision.ESCALATE

    def test_bypass_allows_everything_including_disallowed(self) -> None:
        policy = AllowlistPermissionPolicy(
            RunnerPermissions(
                mode=RunnerPermissionMode.BYPASS_PERMISSIONS,
                allowed_tools=("Read",),
                disallowed_tools=("Bash",),
            )
        )
        assert policy.decide("Bash", {}) is PermissionDecision.ALLOW
        assert policy.decide("Anything", {}) is PermissionDecision.ALLOW


class TestDriverExecutor:
    async def test_bypass_permissions_runs_and_maps_native_flags(self, tmp_path: Path) -> None:
        driver = RecordingDriver(DriverResult(status=DriverResultStatus.SUCCEEDED, summary="ok"))
        task = make_task(
            permissions=RunnerPermissions(mode=RunnerPermissionMode.BYPASS_PERMISSIONS)
        )
        result = await make_executor(driver, tmp_path).execute(task)
        assert result.status is RunnerResultStatus.SUCCEEDED
        assert driver.requests[0].extra_arguments == ("--permission-mode", "bypassPermissions")

    async def test_unknown_adapter_raises(self, tmp_path: Path) -> None:
        driver = RecordingDriver(DriverResult(status=DriverResultStatus.SUCCEEDED, summary="ok"))
        with pytest.raises(LookupError):
            await make_executor(driver, tmp_path).execute(make_task(adapter_id="nope"))

    async def test_missing_binary_raises_driver_error(self, tmp_path: Path) -> None:
        driver = RecordingDriver(DriverResult(status=DriverResultStatus.SUCCEEDED, summary="ok"))
        executor = DriverExecutor(
            drivers={DriverFamily.STREAM_JSON: driver},
            workspace_root=tmp_path,
            binary_resolver=lambda names: None,
        )
        with pytest.raises(DriverError, match="binary_not_found"):
            await executor.execute(make_task())

    async def test_success_maps_summary_and_session(self, tmp_path: Path) -> None:
        driver = RecordingDriver(
            DriverResult(
                status=DriverResultStatus.SUCCEEDED,
                summary="done",
                native_session_id="native-1",
            )
        )
        result = await make_executor(driver, tmp_path).execute(make_task())
        assert result.status is RunnerResultStatus.SUCCEEDED
        assert result.summary == "done"
        assert result.native_session_id == "native-1"

    async def test_failure_maps_diagnostics_not_summary(self, tmp_path: Path) -> None:
        driver = RecordingDriver(
            DriverResult(status=DriverResultStatus.TIMEOUT, diagnostics="idle watchdog fired")
        )
        result = await make_executor(driver, tmp_path).execute(make_task())
        assert result.status is RunnerResultStatus.FAILED
        assert result.summary == "idle watchdog fired"

    async def test_permission_mode_flows_into_extra_arguments(self, tmp_path: Path) -> None:
        driver = RecordingDriver(DriverResult(status=DriverResultStatus.SUCCEEDED, summary="ok"))
        await make_executor(driver, tmp_path).execute(make_task())
        request = driver.requests[0]
        assert request.extra_arguments == ("--permission-mode", "acceptEdits")
        argv = build_arguments(request, get_profile("claude-code"))
        assert "--permission-mode" in argv
        assert argv.index("--permission-mode") >= len(get_profile("claude-code").base_arguments)

    async def test_workspace_is_run_scoped(self, tmp_path: Path) -> None:
        driver = RecordingDriver(DriverResult(status=DriverResultStatus.SUCCEEDED, summary="ok"))
        task = make_task()
        await make_executor(driver, tmp_path).execute(task)
        assert driver.requests[0].workspace == tmp_path / str(task.run_id)
        assert driver.requests[0].workspace.is_dir()
