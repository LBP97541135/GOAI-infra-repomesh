"""Declarative CLI profiles consumed by protocol drivers.

A profile carries only per-CLI differences; protocol behavior lives in the
family driver. Capabilities are three independent claims — launchable,
observable, resumable — and default to the least capable truthful value.

Permission note: ``permission_arguments`` selects the CLI mode only, and no
profile may map onto a CLI's own bypass flag. Platform ``bypass_permissions``
means "auto-approve over the protocol", not "stop asking": a CLI left in bypass
stops emitting permission callbacks, and those callbacks are where
``denied_paths`` / ``disallowed_tools`` are enforced. CLI tool flags were
measured to be advisory rather than enforceable (spec section 6c) in any case,
so the hard boundary remains the workspace, container, and network scope around
the process — never the flags handed to the agent.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from repomesh_runner.contracts import RunnerPermissionMode
from repomesh_runner.drivers.base import DriverFamily


@dataclass(frozen=True, slots=True)
class StreamJsonConfig:
    prompt_via_stdin: bool = True


@dataclass(frozen=True, slots=True)
class AcpConfig:
    protocol_version: int = 1
    quiescence_seconds: float = 2.0


@dataclass(frozen=True, slots=True)
class AppServerConfig:
    """codex app-server tuning.

    ``quiescence_seconds`` bounds the wait for trailing notifications (final
    diff, token usage) that arrive just after ``turn/completed``.
    """

    quiescence_seconds: float = 1.0


@dataclass(frozen=True, slots=True)
class CliProfile:
    id: str
    family: DriverFamily
    binaries: tuple[str, ...]
    launchable: bool = True
    observable: bool = False
    resumable: bool = False
    base_arguments: tuple[str, ...] = ()
    model_flag: str | None = None
    system_prompt_flag: str | None = None
    resume_flag: str | None = None
    permission_arguments: Mapping[RunnerPermissionMode, tuple[str, ...]] = field(
        default_factory=dict
    )
    stream_json: StreamJsonConfig | None = None
    acp: AcpConfig | None = None
    app_server: AppServerConfig | None = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("profile id is required")
        if not self.binaries:
            raise ValueError("profile requires at least one binary name")
        if self.family is DriverFamily.STREAM_JSON and self.stream_json is None:
            raise ValueError(f"{self.id}: stream_json config is required for this family")
        if self.family is DriverFamily.ACP and self.acp is None:
            raise ValueError(f"{self.id}: acp config is required for this family")
        if self.family is DriverFamily.APP_SERVER and self.app_server is None:
            raise ValueError(f"{self.id}: app_server config is required for this family")


PROFILES: tuple[CliProfile, ...] = (
    CliProfile(
        id="claude-code",
        family=DriverFamily.STREAM_JSON,
        binaries=("claude",),
        # observable stays False until control_request handling is verified live.
        observable=False,
        # Resume is `--resume <session_id>`; verified end to end against
        # Claude Code 2.1.222 on 2026-08-05, including the crash case (id taken
        # from the event stream mid-turn, process killed, id resumed in a fresh
        # process, prior context recalled). An unknown id fails loudly.
        resumable=True,
        base_arguments=(
            "-p",
            "--output-format",
            "stream-json",
            "--input-format",
            "stream-json",
            "--verbose",
        ),
        model_flag="--model",
        system_prompt_flag="--append-system-prompt",
        resume_flag="--resume",
        permission_arguments={
            RunnerPermissionMode.DEFAULT: ("--permission-mode", "default"),
            RunnerPermissionMode.ACCEPT_EDITS: ("--permission-mode", "acceptEdits"),
            RunnerPermissionMode.AUTO: ("--permission-mode", "acceptEdits"),
            # Deliberately the DEFAULT (ask-everything) arguments, not
            # ``bypassPermissions``: platform bypass means auto-approval over
            # the protocol, and the CLI's own bypass flag stops it from emitting
            # the control_request channel that enforces the deny rules.
            RunnerPermissionMode.BYPASS_PERMISSIONS: ("--permission-mode", "default"),
        },
        stream_json=StreamJsonConfig(prompt_via_stdin=True),
    ),
    CliProfile(
        id="kimi",
        family=DriverFamily.ACP,
        binaries=("kimi",),
        observable=True,
        # Resume is the ACP `session/resume` RPC; verified end to end against
        # the installed kimi CLI on 2026-08-05, including the crash case (id
        # taken from the event stream mid-turn, process killed, id resumed in a
        # fresh process, prior context recalled). An unknown id fails loudly
        # with `-32602 Invalid params: Unknown sessionId`.
        resumable=True,
        base_arguments=("acp",),
        # Permissions travel over ACP as ``session/request_permission``, so no
        # flags are mapped here; ``--yolo`` in particular would suppress them.
        acp=AcpConfig(protocol_version=1, quiescence_seconds=2.0),
    ),
    CliProfile(
        id="codex",
        family=DriverFamily.APP_SERVER,
        binaries=("codex",),
        observable=True,
        # Resume is the app-server `thread/resume` RPC; verified end to end
        # against codex-cli 0.145.0 on 2026-08-05, including the crash case
        # (thread id taken from the event stream mid-turn, process killed, id
        # resumed in a fresh process, prior context recalled). An unknown id
        # fails loudly with `-32600 no rollout found for thread id`.
        resumable=True,
        base_arguments=("app-server",),
        # Approvals travel over the protocol as server-initiated requests, so
        # there are no permission flags to map onto this surface —
        # ``--dangerously-bypass-approvals-and-sandbox`` would remove them.
        app_server=AppServerConfig(quiescence_seconds=1.0),
    ),
)


class UnknownProfile(LookupError):
    pass


def get_profile(profile_id: str) -> CliProfile:
    for profile in PROFILES:
        if profile.id == profile_id:
            return profile
    raise UnknownProfile(f"unknown runner profile: {profile_id}")
