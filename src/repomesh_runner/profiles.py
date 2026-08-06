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
    #: False marks a profile that is not a vendor CLI at all (the validation
    #: mock below). Such a profile has no entry in the discovery catalog and no
    #: vendor to keep in sync with, so the catalog cross-checks skip it.
    vendor_cli: bool = True
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
            RunnerPermissionMode.DEFAULT: ("--permission-mode", "manual"),
            RunnerPermissionMode.ACCEPT_EDITS: ("--permission-mode", "acceptEdits"),
            RunnerPermissionMode.AUTO: ("--permission-mode", "acceptEdits"),
            # Deliberately the DEFAULT (ask-everything) arguments, not
            # ``bypassPermissions``: platform bypass means auto-approval over
            # the protocol, and the CLI's own bypass flag stops it from emitting
            # the control_request channel that enforces the deny rules.
            RunnerPermissionMode.BYPASS_PERMISSIONS: ("--permission-mode", "manual"),
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
    CliProfile(
        # Validation-only profile: NOT a vendor CLI. It drives
        # ``components/repomesh-runner/mock/mock_coding_agent.py``, a stdlib
        # script that replays a scripted stream-json conversation, so the worker
        # image and the execution plane can be exercised end to end without any
        # vendor binary, network access, or credentials. Never use it to serve
        # real work: it does not read or write the workspace and never calls a
        # model.
        id="mock",
        family=DriverFamily.STREAM_JSON,
        # The launcher installed on PATH by components/repomesh-runner/Dockerfile.
        binaries=("repomesh-mock-agent",),
        launchable=True,
        # Verified in tests/runner/test_mock_agent_executable.py against the real
        # driver and a real subprocess: the mock emits session_started, text,
        # thinking, tool_use/tool_result and control_request events, and answers
        # the control_response frame the driver writes back.
        observable=True,
        # Resume is `--resume <session_id>`: the mock persists the turn under
        # REPOMESH_MOCK_STATE_DIR and repeats the recalled prompt back on the
        # next turn. An unknown id fails loudly with an error result.
        resumable=True,
        # No base arguments: the mock speaks stream-json natively and needs no
        # dialect flags.
        model_flag="--model",
        system_prompt_flag="--append-system-prompt",
        resume_flag="--resume",
        # No permission flags, deliberately: permissions travel over the
        # control_request channel, which is exactly what this profile exists to
        # exercise. The mock's boundary is the container around it.
        stream_json=StreamJsonConfig(prompt_via_stdin=True),
        vendor_cli=False,
    ),
)


class UnknownProfile(LookupError):
    pass


def get_profile(profile_id: str) -> CliProfile:
    for profile in PROFILES:
        if profile.id == profile_id:
            return profile
    raise UnknownProfile(f"unknown runner profile: {profile_id}")
