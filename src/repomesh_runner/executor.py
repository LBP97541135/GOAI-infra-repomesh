"""DriverExecutor: the first real implementation of the RunnerExecutor port.

Resolves a RunnerTask to a CLI profile, answers the worker permission callbacks,
delegates to the protocol driver for the profile's family, and maps the driver
result back onto the Runtime v1 result contract together with the execution
evidence (changed files, test command outcomes) the run produced.

The permission callbacks are a cooperative boundary, not the boundary — see
:class:`AllowlistPermissionPolicy`.
"""

from __future__ import annotations

import asyncio
import fnmatch
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path

from repomesh_runner.contracts import (
    RunnerExecutionResult,
    RunnerPermissionMode,
    RunnerPermissions,
    RunnerResultStatus,
    RunnerTask,
    TestCommandResult,
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

# Tool inputs are agent-authored JSON; a bounded walk keeps a pathological
# nesting from turning one permission callback into an unbounded scan.
_MAX_INPUT_DEPTH = 8

# A test command the shell could not even start. 127 is the conventional
# "command not found" code and reads as a failure everywhere.
_SPAWN_FAILURE_EXIT_CODE = 127


def _string_leaves(value: object, depth: int = 0) -> Iterator[str]:
    """Yield every string leaf reachable from ``value``."""

    if depth > _MAX_INPUT_DEPTH:
        return
    if isinstance(value, str):
        if value.strip():
            yield value
        return
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _string_leaves(item, depth + 1)
        return
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        for item in value:
            yield from _string_leaves(item, depth + 1)


def _normalize(value: str) -> str:
    return value.replace("\\", "/")


class AllowlistPermissionPolicy:
    """Answers protocol permission callbacks from RunnerTask permissions.

    Precedence (decision D2/D3):
    ``denied_paths > disallowed_tools > allowed_paths > allowed_tools > mode``.
    Deny rules bind in *every* mode. ``BYPASS_PERMISSIONS`` only removes the
    interactive confirmation step — it never removes filtering — so it is
    consulted after the two deny rules have already had their say. ESCALATE is
    the conservative answer under DEFAULT mode: the run stops as input_required
    instead of guessing on the worker's behalf.

    Honesty about the boundary: this callback is *cooperative*. It works only
    because the CLI asks before each tool and honours the answer; the hard
    boundary is the container, filesystem and network scope around the process.
    Path extraction reads string leaves of the tool input, so a path embedded
    inside a shell command string ("rm ../../secrets") is not seen here.
    """

    def __init__(
        self,
        permissions: RunnerPermissions,
        workspace: Path | None = None,
    ) -> None:
        self._mode = permissions.mode
        self._allowed = frozenset(permissions.allowed_tools)
        self._disallowed = frozenset(permissions.disallowed_tools)
        self._denied_paths = tuple(_normalize(pattern) for pattern in permissions.denied_paths)
        self._allowed_paths = tuple(_normalize(pattern) for pattern in permissions.allowed_paths)
        self._workspace = workspace

    def decide(self, tool_name: str, tool_input: Mapping[str, object]) -> PermissionDecision:
        candidates = [_normalize(leaf) for leaf in _string_leaves(tool_input)]

        if any(self._matches(candidate, self._denied_paths) for candidate in candidates):
            return PermissionDecision.DENY
        if tool_name in self._disallowed:
            return PermissionDecision.DENY
        if self._mode is RunnerPermissionMode.BYPASS_PERMISSIONS:
            return PermissionDecision.ALLOW

        autonomous = self._mode in (
            RunnerPermissionMode.ACCEPT_EDITS,
            RunnerPermissionMode.AUTO,
        )
        if self._allowed_paths and self._touches_unlisted_path(candidates):
            return PermissionDecision.DENY if autonomous else PermissionDecision.ESCALATE
        if self._allowed and tool_name not in self._allowed:
            return PermissionDecision.DENY if autonomous else PermissionDecision.ESCALATE
        return PermissionDecision.ALLOW if autonomous else PermissionDecision.ESCALATE

    def _touches_unlisted_path(self, candidates: list[str]) -> bool:
        """True when a candidate lands inside the workspace off the allowlist.

        Only candidates that resolve under the workspace are judged: an
        arbitrary string leaf ("fix the flaky test") is not a path, and denying
        on it would make ``allowed_paths`` unusable.
        """

        for candidate in candidates:
            if self._relative_form(candidate) is None:
                continue
            if not self._matches(candidate, self._allowed_paths):
                return True
        return False

    def _matches(self, candidate: str, patterns: tuple[str, ...]) -> bool:
        forms = [candidate]
        relative = self._relative_form(candidate)
        if relative is not None:
            forms.append(relative)
        return any(fnmatch.fnmatch(form, pattern) for form in forms for pattern in patterns)

    def _relative_form(self, candidate: str) -> str | None:
        """The workspace-relative form of an absolute in-workspace path."""

        if self._workspace is None:
            return None
        try:
            path = Path(candidate)
            if not path.is_absolute():
                return None
            return path.relative_to(self._workspace).as_posix()
        except (ValueError, OSError):
            return None


class DriverExecutor:
    def __init__(
        self,
        drivers: Mapping[DriverFamily, ProtocolDriver],
        workspace_root: Path,
        *,
        profile_resolver: Callable[[str], CliProfile] = get_profile,
        binary_resolver: Callable[[tuple[str, ...]], str | None] = resolve_binary,
        observer: Callable[[DriverEvent], None] | None = None,
        # Default None is permissive on purpose: the platform has not decided
        # how the context/coding package is materialized into the workspace, so
        # there is nothing to hash against yet. This is the refusal seam that
        # decision lands on, not a verification implementation.
        context_verifier: Callable[[RunnerTask], str | None] | None = None,
    ) -> None:
        self._drivers = dict(drivers)
        self._workspace_root = workspace_root
        self._profile_resolver = profile_resolver
        self._binary_resolver = binary_resolver
        self._observer = observer or (lambda event: None)
        self._context_verifier = context_verifier

    async def execute(self, task: RunnerTask) -> RunnerExecutionResult:
        refusal = self._verify_context(task)
        if refusal is not None:
            return refusal

        profile = self._profile_resolver(task.adapter_id)
        if not profile.launchable:
            raise DriverError(f"{profile.id}: profile is not launchable")
        driver = self._drivers.get(profile.family)
        if driver is None:
            raise DriverError(f"{profile.id}: no driver registered for {profile.family.value}")
        executable = self._binary_resolver(profile.binaries)
        if executable is None:
            raise DriverError(f"{profile.id}: binary_not_found")

        workspace = self._resolve_workspace(task)
        request = DriverRequest(
            executable=executable,
            workspace=workspace,
            prompt=task.instruction,
            permission_policy=AllowlistPermissionPolicy(task.permissions, workspace),
            resume_session_id=task.resume_session_id if profile.resumable else None,
            extra_arguments=profile.permission_arguments.get(task.permissions.mode, ()),
        )
        result = await driver.execute(request, profile, self._observer)
        return await self._collect_evidence(task, workspace, result)

    def _verify_context(self, task: RunnerTask) -> RunnerExecutionResult | None:
        if self._context_verifier is None:
            return None
        reason = self._context_verifier(task)
        if reason is None:
            return None
        return RunnerExecutionResult(
            status=RunnerResultStatus.FAILED,
            summary=f"context_verification_failed: {reason}",
        )

    def _resolve_workspace(self, task: RunnerTask) -> Path:
        """The directory the driver runs in.

        A task carrying a ``WorkspaceAssignment`` names a directory the platform
        already prepared: the Runner only validates containment and existence,
        never creates it. Without one, the transitional fallback still applies.
        """

        if task.workspace is None:
            workspace = self._workspace_root / str(task.run_id)
            workspace.mkdir(parents=True, exist_ok=True)
            return workspace

        root = self._workspace_root.resolve()
        # resolve() collapses ".." and follows symlinks, so the containment test
        # below sees the real target rather than the path as written.
        candidate = Path(task.workspace.path).resolve()
        if candidate == root or not candidate.is_relative_to(root):
            raise DriverError(
                f"workspace_escape: {task.workspace.workspace_id} resolves to {candidate}, "
                f"which is not a subdirectory of {root}"
            )
        if not candidate.is_dir():
            raise DriverError(
                f"workspace_not_found: {task.workspace.workspace_id} at {candidate} "
                "was not prepared by the platform"
            )
        return candidate

    async def _collect_evidence(
        self,
        task: RunnerTask,
        workspace: Path,
        result: DriverResult,
    ) -> RunnerExecutionResult:
        mapped = self._to_runner_result(result)
        changed_files = await _changed_files(workspace)
        test_results: tuple[TestCommandResult, ...] = ()
        status = mapped.status
        summary = mapped.summary

        if result.status is DriverResultStatus.SUCCEEDED and task.test_commands:
            test_results = await _run_test_commands(workspace, task.test_commands)
            # A task whose own verification fails is not a success, whatever the
            # agent claimed in its final message.
            failure = next((entry for entry in test_results if entry.exit_code != 0), None)
            if failure is not None:
                status = RunnerResultStatus.FAILED
                summary = (
                    f"test_command_failed: {failure.command} "
                    f"(exit code {failure.exit_code})"
                )

        return RunnerExecutionResult(
            status=status,
            summary=summary,
            native_session_id=mapped.native_session_id,
            changed_files=changed_files,
            test_results=test_results,
        )

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


async def _changed_files(workspace: Path) -> tuple[str, ...]:
    """Repo-relative paths git reports as dirty, or nothing.

    Guarded by a toplevel check first: git discovers a repository by walking
    *upward*, so a workspace that merely sits inside some enclosing checkout
    would otherwise have that parent's dirt attributed to this run. Only a
    workspace that is itself the repository root reports evidence.

    Evidence collection must never take a run down: a workspace that is not a
    git repository, or a host without git, yields an empty tuple.
    """

    toplevel = await _git_output(workspace, "rev-parse", "--show-toplevel")
    if toplevel is None:
        return ()
    try:
        if Path(toplevel.strip()).resolve() != workspace.resolve():
            return ()
    except (OSError, ValueError):
        return ()

    # quotepath=false keeps non-ASCII names verbatim; -uall expands an
    # untracked directory into its files instead of reporting "dir/".
    output = await _git_output(
        workspace,
        "-c",
        "core.quotepath=false",
        "status",
        "--porcelain",
        "-uall",
    )
    if output is None:
        return ()
    return _parse_porcelain(output)


async def _git_output(workspace: Path, *arguments: str) -> str | None:
    """Stdout of a git invocation in ``workspace``, or None if it did not succeed."""

    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            *arguments,
            cwd=str(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await process.communicate()
    except (OSError, ValueError, NotImplementedError):
        return None
    if process.returncode != 0:
        return None
    return stdout.decode(errors="replace")


def _parse_porcelain(output: str) -> tuple[str, ...]:
    paths: list[str] = []
    for line in output.splitlines():
        if len(line) < 4:
            continue
        entry = line[3:].strip()
        if " -> " in entry:
            # Rename/copy: the new path is what the run produced.
            entry = entry.rsplit(" -> ", 1)[1]
        entry = entry.strip().strip('"')
        if entry and entry not in paths:
            paths.append(entry)
    return tuple(paths)


async def _run_test_commands(
    workspace: Path,
    commands: tuple[str, ...],
) -> tuple[TestCommandResult, ...]:
    """Run every command, in order, even after one fails (full evidence)."""

    results: list[TestCommandResult] = []
    for command in commands:
        results.append(
            TestCommandResult(command=command, exit_code=await _shell_exit_code(command, workspace))
        )
    return tuple(results)


async def _shell_exit_code(command: str, workspace: Path) -> int:
    try:
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(workspace),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except (OSError, ValueError, NotImplementedError):
        return _SPAWN_FAILURE_EXIT_CODE
    return await process.wait()


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
