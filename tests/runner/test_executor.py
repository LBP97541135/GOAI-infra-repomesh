import shutil
import subprocess
import sys
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
    WorkspaceAssignment,
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


def make_executor(driver: RecordingDriver, tmp_path: Path, **overrides: object) -> DriverExecutor:
    return DriverExecutor(
        drivers={DriverFamily.STREAM_JSON: driver},
        workspace_root=tmp_path,
        binary_resolver=lambda names: f"C:/fake/{names[0]}.exe",
        **overrides,  # type: ignore[arg-type]
    )


def python_command(body: str) -> str:
    """A shell-runnable test command that behaves the same on Windows and POSIX."""

    return f'"{sys.executable}" -c "{body}"'


def make_git_workspace(tmp_path: Path, name: str = "ws") -> Path:
    workspace = tmp_path / name
    workspace.mkdir()
    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True)
    return workspace


needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


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

    def test_bypass_skips_confirmation_but_not_the_deny_rules(self, tmp_path: Path) -> None:
        # Inverted by decision D2: bypass means "do not ask", never "do not
        # filter". Deny rules bind in every mode.
        policy = AllowlistPermissionPolicy(
            RunnerPermissions(
                mode=RunnerPermissionMode.BYPASS_PERMISSIONS,
                allowed_tools=("Read",),
                disallowed_tools=("Bash",),
                denied_paths=("**/.env",),
            ),
            tmp_path,
        )
        assert policy.decide("Bash", {}) is PermissionDecision.DENY
        assert policy.decide("Edit", {"file_path": str(tmp_path / "service/.env")}) is (
            PermissionDecision.DENY
        )
        # Everything the deny rules do not name is auto-approved.
        assert policy.decide("Anything", {}) is PermissionDecision.ALLOW

    def test_denied_paths_outrank_disallowed_tools_and_allowlists(self, tmp_path: Path) -> None:
        policy = AllowlistPermissionPolicy(
            RunnerPermissions(
                mode=RunnerPermissionMode.ACCEPT_EDITS,
                allowed_tools=("Edit",),
                allowed_paths=("**",),
                denied_paths=("secrets/*.key",),
            ),
            tmp_path,
        )
        assert policy.decide("Edit", {"file_path": str(tmp_path / "secrets/prod.key")}) is (
            PermissionDecision.DENY
        )
        assert policy.decide("Edit", {"file_path": "secrets/prod.key"}) is PermissionDecision.DENY
        assert policy.decide("Edit", {"file_path": str(tmp_path / "src/app.py")}) is (
            PermissionDecision.ALLOW
        )

    def test_denied_paths_match_nested_string_leaves(self) -> None:
        policy = AllowlistPermissionPolicy(
            RunnerPermissions(
                mode=RunnerPermissionMode.AUTO,
                denied_paths=("*.pem",),
            )
        )
        nested = {"edits": [{"target": {"path": "certs/server.pem"}}]}
        assert policy.decide("MultiEdit", nested) is PermissionDecision.DENY

    def test_allowed_paths_bind_only_inside_the_workspace(self, tmp_path: Path) -> None:
        policy = AllowlistPermissionPolicy(
            RunnerPermissions(
                mode=RunnerPermissionMode.ACCEPT_EDITS,
                allowed_paths=("src/*",),
            ),
            tmp_path,
        )
        assert policy.decide("Edit", {"file_path": str(tmp_path / "src/app.py")}) is (
            PermissionDecision.ALLOW
        )
        assert policy.decide("Edit", {"file_path": str(tmp_path / "docs/readme.md")}) is (
            PermissionDecision.DENY
        )
        # Prose is not a path; an unlisted-path rule must not swallow it.
        assert policy.decide("Edit", {"note": "fix the flaky test"}) is PermissionDecision.ALLOW

    def test_allowed_paths_escalate_under_default_mode(self, tmp_path: Path) -> None:
        policy = AllowlistPermissionPolicy(
            RunnerPermissions(
                mode=RunnerPermissionMode.DEFAULT,
                allowed_paths=("src/*",),
            ),
            tmp_path,
        )
        assert policy.decide("Edit", {"file_path": str(tmp_path / "docs/readme.md")}) is (
            PermissionDecision.ESCALATE
        )


class TestDriverExecutor:
    async def test_bypass_never_maps_the_cli_bypass_flag(self, tmp_path: Path) -> None:
        # Inverted by decision D2: the CLI's own bypass flag silences the
        # control_request channel the deny rules are enforced on, so platform
        # bypass runs the CLI in its ask-everything mode and auto-approves.
        driver = RecordingDriver(DriverResult(status=DriverResultStatus.SUCCEEDED, summary="ok"))
        task = make_task(
            permissions=RunnerPermissions(mode=RunnerPermissionMode.BYPASS_PERMISSIONS)
        )
        result = await make_executor(driver, tmp_path).execute(task)
        assert result.status is RunnerResultStatus.SUCCEEDED
        assert driver.requests[0].extra_arguments == ("--permission-mode", "default")
        assert "bypassPermissions" not in build_arguments(
            driver.requests[0], get_profile("claude-code")
        )

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

    async def test_workspace_is_run_scoped_without_an_assignment(self, tmp_path: Path) -> None:
        driver = RecordingDriver(DriverResult(status=DriverResultStatus.SUCCEEDED, summary="ok"))
        task = make_task()
        await make_executor(driver, tmp_path).execute(task)
        assert driver.requests[0].workspace == tmp_path / str(task.run_id)
        assert driver.requests[0].workspace.is_dir()

    async def test_prepared_workspace_is_used_as_is(self, tmp_path: Path) -> None:
        driver = RecordingDriver(DriverResult(status=DriverResultStatus.SUCCEEDED, summary="ok"))
        prepared = tmp_path / "worktrees" / "ws-1"
        prepared.mkdir(parents=True)
        task = make_task(
            workspace=WorkspaceAssignment(
                workspace_id="ws-1", path=str(prepared), base_sha="abc123"
            )
        )
        await make_executor(driver, tmp_path).execute(task)
        assert driver.requests[0].workspace == prepared.resolve()
        assert not (tmp_path / str(task.run_id)).exists()

    async def test_workspace_outside_the_root_is_rejected(self, tmp_path: Path) -> None:
        driver = RecordingDriver(DriverResult(status=DriverResultStatus.SUCCEEDED, summary="ok"))
        root = tmp_path / "root"
        root.mkdir()
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        task = make_task(
            workspace=WorkspaceAssignment(
                workspace_id="ws-2", path=str(root / ".." / "elsewhere"), base_sha="abc123"
            )
        )
        executor = DriverExecutor(
            drivers={DriverFamily.STREAM_JSON: driver},
            workspace_root=root,
            binary_resolver=lambda names: f"C:/fake/{names[0]}.exe",
        )
        with pytest.raises(DriverError, match="workspace_escape"):
            await executor.execute(task)
        assert driver.requests == []

    async def test_workspace_root_itself_is_rejected(self, tmp_path: Path) -> None:
        driver = RecordingDriver(DriverResult(status=DriverResultStatus.SUCCEEDED, summary="ok"))
        task = make_task(
            workspace=WorkspaceAssignment(
                workspace_id="ws-3", path=str(tmp_path), base_sha="abc123"
            )
        )
        with pytest.raises(DriverError, match="workspace_escape"):
            await make_executor(driver, tmp_path).execute(task)

    async def test_missing_prepared_workspace_is_rejected(self, tmp_path: Path) -> None:
        driver = RecordingDriver(DriverResult(status=DriverResultStatus.SUCCEEDED, summary="ok"))
        task = make_task(
            workspace=WorkspaceAssignment(
                workspace_id="ws-4", path=str(tmp_path / "never-prepared"), base_sha="abc123"
            )
        )
        with pytest.raises(DriverError, match="workspace_not_found"):
            await make_executor(driver, tmp_path).execute(task)
        assert not (tmp_path / "never-prepared").exists()

    async def test_permission_policy_carries_the_workspace(self, tmp_path: Path) -> None:
        driver = RecordingDriver(DriverResult(status=DriverResultStatus.SUCCEEDED, summary="ok"))
        task = make_task(
            permissions=RunnerPermissions(
                mode=RunnerPermissionMode.ACCEPT_EDITS,
                denied_paths=("secrets/*",),
            )
        )
        await make_executor(driver, tmp_path).execute(task)
        workspace = driver.requests[0].workspace
        policy = driver.requests[0].permission_policy
        assert policy.decide("Edit", {"file_path": str(workspace / "secrets/token")}) is (
            PermissionDecision.DENY
        )


class TestContextVerification:
    async def test_refusal_short_circuits_before_any_driver_work(self, tmp_path: Path) -> None:
        driver = RecordingDriver(DriverResult(status=DriverResultStatus.SUCCEEDED, summary="ok"))
        executor = make_executor(
            driver,
            tmp_path,
            context_verifier=lambda task: "coding_package_hash mismatch",
        )
        result = await executor.execute(make_task())
        assert result.status is RunnerResultStatus.FAILED
        assert result.summary == "context_verification_failed: coding_package_hash mismatch"
        assert driver.requests == []

    async def test_refusal_precedes_profile_resolution(self, tmp_path: Path) -> None:
        driver = RecordingDriver(DriverResult(status=DriverResultStatus.SUCCEEDED, summary="ok"))
        executor = make_executor(driver, tmp_path, context_verifier=lambda task: "no bundle")
        result = await executor.execute(make_task(adapter_id="nope"))
        assert result.status is RunnerResultStatus.FAILED
        assert result.summary == "context_verification_failed: no bundle"

    async def test_passing_verifier_leaves_behaviour_unchanged(self, tmp_path: Path) -> None:
        driver = RecordingDriver(DriverResult(status=DriverResultStatus.SUCCEEDED, summary="ok"))
        executor = make_executor(driver, tmp_path, context_verifier=lambda task: None)
        result = await executor.execute(make_task())
        assert result.status is RunnerResultStatus.SUCCEEDED
        assert len(driver.requests) == 1


class TestExecutionEvidence:
    async def test_no_git_repository_yields_no_changed_files(self, tmp_path: Path) -> None:
        driver = RecordingDriver(DriverResult(status=DriverResultStatus.SUCCEEDED, summary="ok"))
        result = await make_executor(driver, tmp_path).execute(make_task())
        assert result.changed_files == ()
        assert result.test_results == ()

    @needs_git
    async def test_changed_files_are_collected_from_git_status(self, tmp_path: Path) -> None:
        workspace = make_git_workspace(tmp_path)
        (workspace / "added.py").write_text("x = 1\n", encoding="utf-8")
        (workspace / "nested").mkdir()
        (workspace / "nested" / "other.py").write_text("y = 2\n", encoding="utf-8")
        driver = RecordingDriver(DriverResult(status=DriverResultStatus.SUCCEEDED, summary="ok"))
        task = make_task(
            workspace=WorkspaceAssignment(
                workspace_id="ws-git", path=str(workspace), base_sha="abc123"
            )
        )

        result = await make_executor(driver, tmp_path).execute(task)

        assert set(result.changed_files) == {"added.py", "nested/other.py"}

    @needs_git
    async def test_workspace_below_the_repository_toplevel_reports_nothing(
        self, tmp_path: Path
    ) -> None:
        """An enclosing checkout's dirt is never this run's evidence."""

        repository = make_git_workspace(tmp_path, "repo")
        (repository / "parent_change.py").write_text("x = 1\n", encoding="utf-8")
        workspace = repository / "sub"
        workspace.mkdir()
        driver = RecordingDriver(DriverResult(status=DriverResultStatus.SUCCEEDED, summary="ok"))
        task = make_task(
            workspace=WorkspaceAssignment(
                workspace_id="ws-nested", path=str(workspace), base_sha="abc123"
            )
        )

        result = await make_executor(driver, tmp_path).execute(task)

        assert result.status is RunnerResultStatus.SUCCEEDED
        assert result.changed_files == ()

    @needs_git
    async def test_renamed_path_is_reported_by_its_new_name(self, tmp_path: Path) -> None:
        workspace = make_git_workspace(tmp_path)
        (workspace / "old.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=workspace, check=True, capture_output=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=runner",
                "-c",
                "user.email=runner@example.test",
                "commit",
                "-m",
                "base",
            ],
            cwd=workspace,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "mv", "old.py", "new.py"], cwd=workspace, check=True, capture_output=True
        )
        driver = RecordingDriver(DriverResult(status=DriverResultStatus.SUCCEEDED, summary="ok"))
        task = make_task(
            workspace=WorkspaceAssignment(
                workspace_id="ws-rename", path=str(workspace), base_sha="abc123"
            )
        )

        result = await make_executor(driver, tmp_path).execute(task)

        assert result.changed_files == ("new.py",)

    async def test_passing_test_commands_keep_the_run_successful(self, tmp_path: Path) -> None:
        driver = RecordingDriver(DriverResult(status=DriverResultStatus.SUCCEEDED, summary="ok"))
        commands = (python_command("import sys; sys.exit(0)"),)
        result = await make_executor(driver, tmp_path).execute(make_task(test_commands=commands))

        assert result.status is RunnerResultStatus.SUCCEEDED
        assert result.summary == "ok"
        assert [entry.exit_code for entry in result.test_results] == [0]
        assert result.test_results[0].command == commands[0]

    async def test_failing_test_command_fails_the_run_and_names_it(self, tmp_path: Path) -> None:
        driver = RecordingDriver(DriverResult(status=DriverResultStatus.SUCCEEDED, summary="ok"))
        failing = python_command("import sys; sys.exit(3)")
        trailing = python_command("import sys; sys.exit(0)")
        result = await make_executor(driver, tmp_path).execute(
            make_task(test_commands=(failing, trailing))
        )

        assert result.status is RunnerResultStatus.FAILED
        assert failing in result.summary
        assert "exit code 3" in result.summary
        # every command still ran: the report is full evidence, not first-failure
        assert [entry.exit_code for entry in result.test_results] == [3, 0]

    async def test_test_commands_are_skipped_when_the_driver_failed(self, tmp_path: Path) -> None:
        driver = RecordingDriver(
            DriverResult(status=DriverResultStatus.TIMEOUT, diagnostics="idle watchdog fired")
        )
        commands = (python_command("import sys; sys.exit(0)"),)
        result = await make_executor(driver, tmp_path).execute(make_task(test_commands=commands))

        assert result.status is RunnerResultStatus.FAILED
        assert result.summary == "idle watchdog fired"
        assert result.test_results == ()

    async def test_test_commands_run_inside_the_workspace(self, tmp_path: Path) -> None:
        driver = RecordingDriver(DriverResult(status=DriverResultStatus.SUCCEEDED, summary="ok"))
        prepared = tmp_path / "ws-cwd"
        prepared.mkdir()
        (prepared / "marker.txt").write_text("here", encoding="utf-8")
        task = make_task(
            workspace=WorkspaceAssignment(
                workspace_id="ws-cwd", path=str(prepared), base_sha="abc123"
            ),
            test_commands=(
                python_command("import pathlib,sys; sys.exit(0 if pathlib.Path('marker.txt')"
                               ".exists() else 9)"),
            ),
        )

        result = await make_executor(driver, tmp_path).execute(task)

        assert result.status is RunnerResultStatus.SUCCEEDED
        assert [entry.exit_code for entry in result.test_results] == [0]
