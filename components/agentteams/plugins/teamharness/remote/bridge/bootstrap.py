#!/usr/bin/env python3
"""Operator-written local bootstrap config for a remote member bridge.

``docs/member-runtime-config-contract.md`` makes the controller the source of
team and member facts through ``shared/runtime/members/{name}/runtime.yaml``,
and ``docs/teamharness-boundary-and-contracts.md`` forbids the bridge from
asking the ``agt`` CLI instead. Today the controller writes neither, and a
remote member has no in-cluster process to fall back on -- so the operator
hand-writes the same facts into a local file (see README open question 1).

The schema here is a deliberate *subset* of the contract, field names and all
(camelCase in YAML). When the controller starts writing ``runtime.yaml``, the
same parser reads it and only the path resolution changes. The one addition is
the ``local:`` section, which the contract explicitly excludes because it
carries machine-local paths that no controller can know.

Credential red line
-------------------
This file is hand-edited on a laptop, which is exactly where a real Matrix
token gets pasted "just to try it". :func:`load_bootstrap` refuses to load a
file that holds anything that looks like a secret *value*: the bridge fails to
start rather than running with a token committed to disk. The contract's
pointer fields (``credentials.matrixTokenEnv``, ``serviceAccountTokenPath``)
hold variable names and paths, not values, and stay allowed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from typing import Any, Iterator

BOOTSTRAP_ENV_VAR = "AGENTTEAMS_REMOTE_BOOTSTRAP"
DEFAULT_BOOTSTRAP_PATH = "~/.agentteams/remote/bootstrap.yaml"

DEFAULT_ROLE = "remote-member"
DEFAULT_KIND = "MemberRuntimeConfig"
DEFAULT_STATE_DIR_TEMPLATE = "~/.agentteams/remote/{member_name}"
DEFAULT_MCP_ENV_PASSTHROUGH: tuple[str, ...] = (
    "AGENTTEAMS_MATRIX_URL",
    "AGENTTEAMS_WORKER_MATRIX_TOKEN",
    # Shared object storage. The controller already provisions a scoped MinIO
    # user for every member -- including a containerManaged: false one, because
    # that happens during identity provisioning, before the container skip --
    # so these name credentials the member *has*, not ones it must be granted.
    # Listing them by default costs nothing when unset: the projected
    # ``${VAR}`` expands empty and ``filesync`` reports its normal
    # "alias is not configured" error, exactly as it does today.
    "AGENTTEAMS_FS_ENDPOINT",
    "AGENTTEAMS_FS_ACCESS_KEY",
    "AGENTTEAMS_FS_SECRET_KEY",
    "AGENTTEAMS_STORAGE_PREFIX",
)
# Accounts the controller and Manager use to invite members to team rooms.
DEFAULT_INVITER_LOCALPARTS: tuple[str, ...] = ("admin", "manager")

# A key whose *value* would be a secret if it were populated.
_SECRET_KEY_RE = re.compile(
    r"(?i)(token|secret|password|credential|apikey|api_key|accesskey|access_key)"
)
# ...unless the key names a *location* of the secret instead. The contract's
# credentials section is entirely of this shape: ``*Env`` holds an environment
# variable name, ``*Path`` a mount point. Both are safe to write down.
_SECRET_POINTER_RE = re.compile(r"(?i)(env|path)$")


@dataclass(frozen=True)
class StorageFacts:
    """Remote object storage coordinates. Never access keys, never local paths.

    Mirrors the contract's ``storage:`` section; every field is optional
    because a bridge that only relays room traffic needs none of them.
    """

    provider: str = ""
    bucket: str = ""
    endpoint: str = ""
    team_prefix: str = ""
    shared_prefix: str = ""
    global_shared_prefix: str = ""
    member_prefix: str = ""


@dataclass(frozen=True)
class BootstrapConfig:
    """Flattened, validated view of the bootstrap file.

    Flat by intent: callers want ``config.team_room_id``, not a walk through
    three optional mappings, and flattening is where the defaults get applied
    once instead of at every use site.
    """

    team_name: str
    team_room_id: str
    member_name: str
    matrix_user_id: str
    workspace: Path
    state_dir: Path
    leader_name: str = ""
    runtime_name: str = ""
    # Which local CLI drives this member, from ``local.runtime``.
    #
    # **Not** ``runtime_name`` above, despite how that reads. ``runtime_name``
    # is the AgentTeams agent name behind the ``agents/{runtime_name}/`` storage
    # prefix; this one is a key of the driver registry ("claude-code",
    # "codex-cli"). The two words collide because both names are inherited, and
    # the collision is worth a comment rather than a silent trap: writing
    # ``member.runtimeName: codex-cli`` moves the *storage prefix* and leaves
    # the CLI on its default, with neither side complaining.
    #
    # Empty means "not stated here"; ``--runtime`` and then the registry default
    # decide. Before this existed the choice lived only on the command line, so
    # two members on one laptop were distinguishable only by how they had been
    # launched -- nothing in either bootstrap file said which CLI it meant.
    runtime: str = ""
    role: str = DEFAULT_ROLE
    personal_room_id: str = ""
    storage: StorageFacts = field(default_factory=StorageFacts)
    mcp_env_passthrough: tuple[str, ...] = DEFAULT_MCP_ENV_PASSTHROUGH
    # Extra argv appended to every runtime invocation, verbatim. This is where
    # the operator grants the agent permission to act -- headless Claude Code
    # declines edits by default, so a bridge started without this answers every
    # coding task with "I need permission to write". Deliberately operator-set
    # and not defaulted: how much authority a remote agent gets on someone's
    # own laptop is not a decision this code may make for them.
    driver_args: tuple[str, ...] = ()
    # Matrix user ids whose room invites are accepted automatically. The
    # controller invites a member to its team room and expects the runtime to
    # accept; a containerised worker does that in its entrypoint, so a remote
    # member's bridge must. Auto-joining *anything* would let any homeserver
    # user pull the agent into a room and command it, hence an allowlist --
    # defaulted to the admin and manager accounts on the member's own server.
    auto_join_inviters: tuple[str, ...] = ()
    source_path: Path = Path()

    def accepts_invite_from(self, inviter: str) -> bool:
        """Trust rule for a pending invite. Empty inviter is never trusted."""
        candidate = str(inviter or "").strip()
        return bool(candidate) and candidate in self.auto_join_inviters


def resolve_bootstrap_path(
    path: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> Path:
    """Explicit argument, then ``AGENTTEAMS_REMOTE_BOOTSTRAP``, then the default.

    ``env`` is injected rather than read from ``os.environ`` at call time so
    tests can exercise the precedence without mutating process state, matching
    the ``now``-injection style in ``session_store``.
    """
    environ = os.environ if env is None else env
    if path is not None and str(path).strip():
        raw = str(path)
    else:
        raw = (environ.get(BOOTSTRAP_ENV_VAR) or "").strip() or DEFAULT_BOOTSTRAP_PATH
    return Path(raw).expanduser()


def load_bootstrap(
    path: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> BootstrapConfig:
    """Read, scan, validate, and flatten the bootstrap file.

    Raises ``FileNotFoundError`` when the resolved path does not exist, and
    ``ValueError`` for malformed YAML, a missing required field (the message
    carries the dotted field path), or a suspected secret value.
    """
    resolved = resolve_bootstrap_path(path, env)
    data = _read_yaml(resolved)

    kind = _text(data.get("kind"))
    if kind and kind != DEFAULT_KIND:
        raise ValueError(
            f"{resolved}: kind must be {DEFAULT_KIND!r}, got {kind!r}"
        )

    # Before anything else: a file holding a live token must not be used at
    # all, not even for the fields that look harmless.
    _reject_secret_values(data, resolved)

    team = _mapping(data, "team")
    member = _mapping(data, "member")
    local = _mapping(data, "local")

    team_name = _required(team, "name", "team.name", resolved)
    team_room_id = _required(team, "teamRoomId", "team.teamRoomId", resolved)
    member_name = _required(member, "name", "member.name", resolved)
    matrix_user_id = _required(member, "matrixUserId", "member.matrixUserId", resolved)
    workspace_raw = _required(local, "workspace", "local.workspace", resolved)

    base_dir = resolved.parent
    state_dir_raw = _text(local.get("stateDir")) or DEFAULT_STATE_DIR_TEMPLATE.format(
        member_name=member_name
    )

    passthrough = _text_tuple(local.get("mcpEnvPassthrough")) or DEFAULT_MCP_ENV_PASSTHROUGH

    return BootstrapConfig(
        team_name=team_name,
        team_room_id=team_room_id,
        member_name=member_name,
        matrix_user_id=matrix_user_id,
        workspace=_resolve_path(workspace_raw, base_dir),
        state_dir=_resolve_path(state_dir_raw, base_dir),
        leader_name=_text(team.get("leaderName")),
        runtime_name=_text(member.get("runtimeName")) or member_name,
        runtime=_text(local.get("runtime")),
        role=_text(member.get("role")) or DEFAULT_ROLE,
        personal_room_id=_text(member.get("personalRoomId")),
        storage=_storage_facts(_mapping(data, "storage")),
        mcp_env_passthrough=passthrough,
        driver_args=_text_tuple(local.get("driverArgs")),
        auto_join_inviters=_text_tuple(local.get("autoJoinInviters"))
        or _default_inviters(matrix_user_id),
        source_path=resolved,
    )


def _default_inviters(matrix_user_id: str) -> tuple[str, ...]:
    """``@admin`` and ``@manager`` on the member's own homeserver.

    Same-server only, and only those two localparts: the controller creates
    team rooms as ``@admin`` and the Manager runs coordination. Anything else
    -- another worker, a federated user -- has to be listed explicitly.
    """
    _, _, domain = str(matrix_user_id or "").partition(":")
    if not domain:
        return ()
    return tuple(f"@{localpart}:{domain}" for localpart in DEFAULT_INVITER_LOCALPARTS)


# ---- parsing -------------------------------------------------------------


def _read_yaml(path: Path) -> dict[str, Any]:
    """Parse with PyYAML, like the qwenpaw adapter's ``_read_yaml``.

    The adapter downgrades a missing PyYAML to a warning and an empty config;
    the bridge cannot, because an empty config here means "start with no team
    identity". Hand-rolling a YAML subset was the alternative and was rejected:
    this file shares a schema with a controller-written one, so the two must
    not disagree on quoting, anchors, or multi-line scalars.
    """
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ValueError(
            "PyYAML is required to read the remote bootstrap file "
            f"({path}); install it with 'pip install pyyaml'"
        ) from exc

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise FileNotFoundError(
            f"remote bootstrap file not found: {path} "
            f"(set {BOOTSTRAP_ENV_VAR} or create {DEFAULT_BOOTSTRAP_PATH})"
        ) from None
    except OSError as exc:
        raise ValueError(f"cannot read remote bootstrap file {path}: {exc}") from exc

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: invalid YAML: {exc}") from exc

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a mapping, got {type(data).__name__}")
    return data


def _mapping(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name) or {}
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    if value is None or isinstance(value, (dict, list)):
        return ""
    return str(value).strip()


def _text_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in (_text(v) for v in value) if item)


def _required(section: dict[str, Any], key: str, dotted: str, path: Path) -> str:
    value = _text(section.get(key))
    if not value:
        raise ValueError(f"{path}: missing required field {dotted}")
    return value


def _storage_facts(section: dict[str, Any]) -> StorageFacts:
    return StorageFacts(
        provider=_text(section.get("provider")),
        bucket=_text(section.get("bucket")),
        endpoint=_text(section.get("endpoint")),
        team_prefix=_text(section.get("teamPrefix")),
        shared_prefix=_text(section.get("sharedPrefix")),
        global_shared_prefix=_text(section.get("globalSharedPrefix")),
        member_prefix=_text(section.get("memberPrefix")),
    )


def _resolve_path(raw: str, base_dir: Path) -> Path:
    """``~`` first, then relative-to-the-bootstrap-file.

    Relative to the *file*, not to the process CWD: the bridge is started from
    wherever the operator happens to be standing, and a workspace that moves
    with the shell is a workspace that silently runs the agent in the wrong
    directory.
    """
    candidate = Path(raw).expanduser()
    # ``root`` and not just ``is_absolute``: on Windows a POSIX-style "/srv/ws"
    # is rooted but driveless, and joining it onto the bootstrap file's drive
    # would silently graft an unrelated volume onto the operator's path.
    if not candidate.is_absolute() and not candidate.root:
        candidate = base_dir / candidate
    return Path(os.path.normpath(str(candidate)))


# ---- credential red line -------------------------------------------------


def _reject_secret_values(data: Any, path: Path) -> None:
    for dotted, key in _walk_leaves(data):
        if not _SECRET_KEY_RE.search(key) or _SECRET_POINTER_RE.search(key):
            continue
        raise ValueError(
            f"{path}: field {dotted} looks like a secret value and must not be "
            "written to the bootstrap file; keep the value in an environment "
            f"variable and reference its name (e.g. {dotted}Env) instead"
        )


def _walk_leaves(node: Any, prefix: str = "", key: str = "") -> Iterator[tuple[str, str]]:
    """Yield ``(dotted_path, key)`` for every populated scalar leaf.

    Containers are recursed into rather than reported, so ``credentials:`` as a
    section name is fine while ``credentials: syt_live`` is not. List elements
    inherit the enclosing mapping key, so ``tokens: [syt_live]`` trips the check
    just like a scalar would.
    """
    if isinstance(node, dict):
        for raw_key, value in node.items():
            child_key = str(raw_key)
            dotted = f"{prefix}.{child_key}" if prefix else child_key
            yield from _walk_leaves(value, dotted, child_key)
    elif isinstance(node, (list, tuple)):
        for index, item in enumerate(node):
            yield from _walk_leaves(item, f"{prefix}[{index}]", key)
    elif _is_populated(node):
        yield prefix, key


def _is_populated(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True
