import asyncio
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from repomesh.modules.agent_runtime.ports.agent_adapter import (
    AdapterCapability,
    AdapterManifest,
    AdapterProbe,
    AgentFeedback,
    AgentLaunchRequest,
    AgentSessionInfo,
    AgentSessionRef,
    AuthStatus,
    FeedbackDelivery,
    LaunchPlan,
    PermissionMode,
    PromptDelivery,
)


class AdapterError(RuntimeError):
    pass


class AgentBinaryNotFound(AdapterError):
    pass


class UnsafePermissionMode(AdapterError):
    pass


@dataclass(frozen=True, slots=True)
class CommandResult:
    exit_code: int
    output: str


class CommandRunner(Protocol):
    async def run(
        self, executable: str, arguments: tuple[str, ...], timeout_seconds: float
    ) -> CommandResult: ...


class SubprocessCommandRunner:
    async def run(
        self, executable: str, arguments: tuple[str, ...], timeout_seconds: float
    ) -> CommandResult:
        process = await asyncio.create_subprocess_exec(
            executable,
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout_seconds)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        return CommandResult(process.returncode or 0, output.decode(errors="replace"))


class PromptStyle(StrEnum):
    NONE = "none"
    POSITIONAL = "positional"
    SEPARATOR = "separator"
    FLAG = "flag"


class ExecutionStatus(StrEnum):
    """How much trust a catalog entry's launch shape has earned.

    ``UNVERIFIED`` is the default and the honest description of most entries:
    they were transcribed from the upstream reference in ``source_revision``
    and have never been run against the real CLI.

    ``SUPERSEDED_BY_DRIVER`` marks the CLIs the RepoMesh Runner drives through
    a verified protocol profile in ``repomesh_runner.profiles``. For those, the
    launch shape here is known to be wrong for unattended execution — the
    interactive invocation either demands a TTY or silently completes without
    doing any work — and the profile is authoritative.
    """

    UNVERIFIED = "unverified"
    SUPERSEDED_BY_DRIVER = "superseded_by_driver"


@dataclass(frozen=True, slots=True)
class AuthProbeSpec:
    arguments: tuple[str, ...] | None = None
    environment_variables: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AdapterSpec:
    id: str
    display_name: str
    binaries: tuple[str, ...]
    prompt_delivery: PromptDelivery
    launch_prefix: tuple[str, ...] = ()
    prompt_style: PromptStyle = PromptStyle.SEPARATOR
    prompt_flag: str | None = None
    restore_prefix: tuple[str, ...] | None = ()
    restore_flag: str | None = None
    workspace_flag: str | None = None
    session_flag: str | None = None
    model_flag: str | None = None
    system_prompt_flag: str | None = None
    system_prompt_file_flag: str | None = None
    allowed_tools_flag: str | None = None
    disallowed_tools_flag: str | None = None
    permission_arguments: Mapping[PermissionMode, tuple[str, ...]] = field(default_factory=dict)
    environment: Mapping[str, str] = field(default_factory=dict)
    auth: AuthProbeSpec = field(default_factory=AuthProbeSpec)
    execution_status: ExecutionStatus = ExecutionStatus.UNVERIFIED
    source_revision: str = (
        "Untrivial-ai/agent-orchestrator@10025449557665e2474c67096dab47c32d10138f"
    )


class BinaryResolver:
    def resolve(self, names: tuple[str, ...]) -> str | None:
        for name in names:
            if path := shutil.which(name):
                return path

        home = Path.home()
        roots = [home / ".local" / "bin", home / ".cargo" / "bin"]
        if os.name == "nt":
            if appdata := os.environ.get("APPDATA"):
                roots.append(Path(appdata) / "npm")
        else:
            roots.extend((Path("/usr/local/bin"), Path("/opt/homebrew/bin")))

        suffixes = (".cmd", ".exe", "") if os.name == "nt" else ("",)
        for root in roots:
            for name in names:
                for suffix in suffixes:
                    candidate = root / f"{name}{suffix}"
                    if candidate.is_file():
                        return str(candidate)
        return None


class CliAgentAdapter:
    def __init__(
        self,
        spec: AdapterSpec,
        *,
        runner: CommandRunner | None = None,
        resolver: BinaryResolver | None = None,
        resolved_binary: str | None = None,
    ) -> None:
        self._spec = spec
        self._runner = runner or SubprocessCommandRunner()
        self._resolver = resolver or BinaryResolver()
        self._resolved_binary = resolved_binary

    @property
    def manifest(self) -> AdapterManifest:
        capabilities = {
            AdapterCapability.AUTH_PROBE,
            AdapterCapability.NATIVE_SESSION,
            AdapterCapability.FEEDBACK,
        }
        if self._spec.restore_prefix is not None and self._spec.restore_flag is not None:
            capabilities.add(AdapterCapability.RESTORE)
        return AdapterManifest(
            id=self._spec.id,
            display_name=self._spec.display_name,
            binary_names=self._spec.binaries,
            prompt_delivery=self._spec.prompt_delivery,
            capabilities=frozenset(capabilities),
            source_revision=self._spec.source_revision,
            execution_status=self._spec.execution_status.value,
        )

    async def probe(self) -> AdapterProbe:
        executable = self._find_binary()
        if executable is None:
            return AdapterProbe(
                adapter_id=self._spec.id,
                installed=False,
                executable=None,
                auth_status=AuthStatus.UNKNOWN,
                detail="binary_not_found",
            )

        if any(os.environ.get(name, "").strip() for name in self._spec.auth.environment_variables):
            return AdapterProbe(self._spec.id, True, executable, AuthStatus.AUTHORIZED)
        if self._spec.auth.arguments is None:
            return AdapterProbe(
                self._spec.id, True, executable, AuthStatus.UNKNOWN, "auth_probe_not_supported"
            )

        try:
            result = await self._runner.run(executable, self._spec.auth.arguments, 3.0)
        except (OSError, TimeoutError) as error:
            return AdapterProbe(
                self._spec.id, True, executable, AuthStatus.UNKNOWN, type(error).__name__
            )
        status = self._auth_status(result.output, result.exit_code)
        return AdapterProbe(self._spec.id, True, executable, status)

    def build_launch(self, request: AgentLaunchRequest) -> LaunchPlan:
        """Render the interactive launch shape for preview and discovery.

        This is not the execution path. Unattended runs go through
        ``repomesh_runner`` protocol drivers; see ``execution_status`` on the
        spec for whether a verified driver profile supersedes this shape.
        """

        self._validate_permissions(request)
        arguments = list(self._spec.launch_prefix)
        self._append_common_arguments(arguments, request)
        self._append_prompt(arguments, request.prompt)
        return self._plan(request, tuple(arguments))

    def build_restore(
        self, request: AgentLaunchRequest, session: AgentSessionRef
    ) -> LaunchPlan | None:
        if self._spec.restore_prefix is None or self._spec.restore_flag is None:
            return None
        native_id = session.native_session_id.strip()
        if not native_id:
            return None
        restore_request = replace(request, workspace_path=session.workspace_path)
        self._validate_permissions(restore_request)
        arguments = list(self._spec.restore_prefix)
        self._append_common_arguments(arguments, restore_request, include_session=False)
        if self._spec.restore_flag:
            arguments.extend((self._spec.restore_flag, native_id))
        else:
            arguments.append(native_id)
        return self._plan(restore_request, tuple(arguments), prompt_after_start=None)

    def session_info(self, metadata: Mapping[str, str]) -> AgentSessionInfo | None:
        native_id = next(
            (
                metadata[key].strip()
                for key in ("agentSessionId", "agent_session_id", "native_session_id")
                if metadata.get(key, "").strip()
            ),
            "",
        )
        if not native_id:
            return None
        return AgentSessionInfo(
            native_session_id=native_id,
            title=metadata.get("title"),
            summary=metadata.get("summary"),
        )

    def receive_feedback(self, feedback: AgentFeedback) -> FeedbackDelivery:
        source = f"\nSource: {feedback.source_url}" if feedback.source_url else ""
        prompt = (
            f"[{feedback.kind.value.upper()} FEEDBACK] {feedback.summary}\n\n"
            f"{feedback.details}{source}\n\n"
            "Inspect the workspace, apply the smallest correct fix, and rerun relevant checks."
        )
        return FeedbackDelivery(prompt=prompt)

    def _find_binary(self) -> str | None:
        return self._resolved_binary or self._resolver.resolve(self._spec.binaries)

    def _require_binary(self) -> str:
        if executable := self._find_binary():
            return executable
        raise AgentBinaryNotFound(f"{self._spec.id}: binary_not_found")

    def _validate_permissions(self, request: AgentLaunchRequest) -> None:
        """Permission modes are passed through, including bypass.

        Provider tool flags were measured to be advisory rather than
        enforceable, so gating here bought no containment. Isolation is owned
        by the workspace, container, and network scope around the process.
        ``UnsafePermissionMode`` is retained for adapters that reject a mode
        their CLI cannot express.
        """

    def _append_common_arguments(
        self,
        arguments: list[str],
        request: AgentLaunchRequest,
        *,
        include_session: bool = True,
    ) -> None:
        if self._spec.workspace_flag:
            arguments.extend((self._spec.workspace_flag, str(request.workspace_path)))
        if include_session and self._spec.session_flag and request.session_id:
            arguments.extend((self._spec.session_flag, request.session_id))
        arguments.extend(self._spec.permission_arguments.get(request.permission_mode, ()))
        if self._spec.allowed_tools_flag and request.allowed_tools:
            arguments.extend((self._spec.allowed_tools_flag, ",".join(request.allowed_tools)))
        if self._spec.disallowed_tools_flag and request.disallowed_tools:
            arguments.extend((self._spec.disallowed_tools_flag, ",".join(request.disallowed_tools)))
        if self._spec.model_flag and request.model:
            arguments.extend((self._spec.model_flag, request.model))
        if self._spec.system_prompt_flag and request.system_prompt:
            arguments.extend((self._spec.system_prompt_flag, request.system_prompt))
        if self._spec.system_prompt_file_flag and request.system_prompt_file:
            arguments.extend((self._spec.system_prompt_file_flag, str(request.system_prompt_file)))

    def _append_prompt(self, arguments: list[str], prompt: str) -> None:
        if not prompt or self._spec.prompt_delivery is PromptDelivery.AFTER_START:
            return
        if self._spec.prompt_style is PromptStyle.POSITIONAL:
            arguments.append(prompt)
        elif self._spec.prompt_style is PromptStyle.SEPARATOR:
            arguments.extend(("--", prompt))
        elif self._spec.prompt_style is PromptStyle.FLAG and self._spec.prompt_flag:
            arguments.extend((self._spec.prompt_flag, prompt))

    def _plan(
        self,
        request: AgentLaunchRequest,
        arguments: tuple[str, ...],
        *,
        prompt_after_start: str | None = None,
    ) -> LaunchPlan:
        if prompt_after_start is None and self._spec.prompt_delivery is PromptDelivery.AFTER_START:
            prompt_after_start = request.prompt
        environment = dict(self._spec.environment)
        environment.update(request.environment)
        return LaunchPlan(
            adapter_id=self._spec.id,
            executable=self._require_binary(),
            arguments=arguments,
            working_directory=request.workspace_path,
            environment=environment,
            prompt_delivery=self._spec.prompt_delivery,
            prompt_after_start=prompt_after_start,
        )

    @staticmethod
    def _auth_status(output: str, exit_code: int) -> AuthStatus:
        text = output.casefold()
        denied = ("not logged in", "logged out", "unauthorized", "not authenticated")
        allowed = ("logged in", "authenticated", "authorized", '"loggedin":true')
        if any(marker in text for marker in denied):
            return AuthStatus.UNAUTHORIZED
        if any(marker in text for marker in allowed):
            return AuthStatus.AUTHORIZED
        if exit_code != 0:
            return AuthStatus.UNAUTHORIZED
        return AuthStatus.UNKNOWN
