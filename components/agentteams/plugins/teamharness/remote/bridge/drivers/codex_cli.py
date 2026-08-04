#!/usr/bin/env python3
"""``RuntimeDriver`` over ``codex exec --json``.

The design note for this bridge predicted Codex would be driven through
``codex app-server`` JSON-RPC. It should not be: ``app-server`` is marked
``[experimental]`` in ``codex --help``, while ``codex exec`` is the supported
non-interactive entry point, emits JSONL with ``--json``, and has a first-class
``resume`` subcommand. The protocol below was captured from a real
``codex-cli 0.145.0`` run rather than assumed -- see the trap list, every entry
of which cost a probe to find.

Event shape (one JSON object per stdout line)::

    {"type": "thread.started", "thread_id": "<uuid>"}
    {"type": "turn.started"}
    {"type": "item.started",   "item": {"id": "...", "type": "...", ...}}
    {"type": "item.completed", "item": {"id": "...", "type": "...", ...}}
    {"type": "turn.completed", "usage": {...}}
    {"type": "turn.failed",    ...}

``item.type`` observed or present in the binary: ``agent_message``,
``reasoning``, ``command_execution``, ``file_change``, ``mcp_tool_call``,
``web_search``, ``todo_list``, ``error``.

Traps this driver exists to avoid, all found by probing:

1. **``codex exec`` reads stdin even when the prompt is an argument** and blocks
   until EOF. With an inherited pipe the turn hangs forever and emits nothing.
   ``_process.spawn`` pins ``stdin=DEVNULL``.
2. **An ``error`` item is not a failed turn.** A successful probe run emitted
   ``{"type":"error","message":"Skill descriptions were shortened..."}`` as a
   completed item and then finished normally. Mapping ``item.type == "error"``
   to ``failed`` reports a false failure on a turn that worked.
3. **A turn emits several ``agent_message`` items**, not one. The intermediate
   ones are narration ("I'll read sample.txt directly...", "The shell process
   hasn't returned yet..."). The final answer is the *last* one; forwarding all
   of them would post exactly the running commentary that
   ``prompts/agent/remote-member.md`` tells the agent never to post.
4. **``resume`` is a subcommand, not a flag**, and it accepts a narrower option
   set than ``codex exec``. Global flags must precede it:
   ``codex exec --json --sandbox X resume <id> <prompt>``. Putting
   ``--sandbox`` after ``resume`` fails with ``unexpected argument``.
5. **Codex writes UTF-8 regardless of the console code page.** Decoding with
   the platform locale corrupts every non-ASCII character; ``_process.spawn``
   pins ``encoding="utf-8"``.
6. **An MCP server's ``env`` table is its whole environment, not an overlay.**
   Codex passes nothing of its own to a stdio MCP child, so declaring three
   variables in ``env`` left the TeamHarness server with exactly those three
   and no Matrix or storage configuration. The symptom is not an error:
   ``filesync`` fell back to an unprefixed remote path, which ``mc`` read as a
   *local* directory, so a push reported success having never left the disk.
   ``env_vars`` is the companion field that inherits by name -- see
   ``mcp_config_args``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from typing import Any, Generator, Iterable

from . import _process
from ..protocol import AssetContext, DriverProbe, TurnEvent, TurnRequest, TurnResult

DEFAULT_BINARY = "codex"

MCP_SERVER_ID = "teamharness"
ROLE_ENV_VAR = "AGENTTEAMS_AGENT_ROLE"
SHARED_DIR_ENV_VAR = "TEAMHARNESS_SHARED_DIR"

# ``item.type`` values that carry prose the bridge may forward. Everything else
# is machinery the room should never see.
_TEXT_ITEM = "agent_message"
_REASONING_ITEM = "reasoning"
_ERROR_ITEM = "error"
# Items that represent the agent doing something rather than saying something.
_TOOL_ITEMS = frozenset({"command_execution", "file_change", "mcp_tool_call", "web_search"})


class CodexCliDriver:
    """Drive one bounded Codex turn over ``codex exec --json``.

    One instance owns at most one live turn; the supervisor creates one per
    concurrent task. ``binary`` and ``env`` are injectable so the tests can
    drive a scripted stand-in without patching anything.
    """

    name = "codex-cli"

    def __init__(
        self,
        binary: str | Iterable[str] = DEFAULT_BINARY,
        extra_args: Iterable[str] = (),
        env: dict[str, str] | None = None,
    ) -> None:
        self._command: tuple[str, ...] = (
            (binary,) if isinstance(binary, str) else tuple(str(part) for part in binary)
        )
        if not self._command:
            raise ValueError("binary must name at least one command element")
        # Resolve argv[0] once, at construction: an npm-installed CLI on
        # Windows is a .CMD shim that cannot be spawned by bare name.
        self._command = _process.resolve_command(self._command)
        self._extra_args: tuple[str, ...] = tuple(str(arg) for arg in extra_args)
        self._env: dict[str, str] = dict(env or {})
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None

    @property
    def binary(self) -> str:
        return self._command[0]

    # ---- probe -------------------------------------------------------

    def probe(self) -> DriverProbe:
        """Check binary presence, version, and local auth. Never raises."""
        resolved = shutil.which(self.binary)
        if not resolved:
            return DriverProbe(
                available=False,
                binary=self.binary,
                reason=f"executable not found on PATH: {self.binary}",
            )
        try:
            completed = subprocess.run(
                [*self._command, "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_process.PROBE_TIMEOUT_SECONDS,
                env=_process.child_env(self._env),
            )
        except subprocess.TimeoutExpired:
            return DriverProbe(
                available=False,
                binary=resolved,
                reason=f"`--version` timed out after {_process.PROBE_TIMEOUT_SECONDS}s",
            )
        except OSError as exc:
            return DriverProbe(available=False, binary=resolved, reason=f"spawn failed: {exc}")
        if completed.returncode != 0:
            detail = _process.tail(
                _process.redact(completed.stderr or completed.stdout or "", self._env)
            )
            return DriverProbe(
                available=False,
                binary=resolved,
                reason=f"`--version` exited {completed.returncode}: {detail}".strip(),
            )
        return DriverProbe(
            available=True,
            binary=resolved,
            version=_process.first_line(completed.stdout),
            authenticated=_has_local_credentials(),
        )

    # ---- turn --------------------------------------------------------

    def run_turn(self, request: TurnRequest) -> Generator[TurnEvent, None, TurnResult]:
        """Yield normalized events; return the ``TurnResult`` on completion."""
        with self._lock:
            live = self._process
            if live is not None and live.poll() is None:
                raise RuntimeError("a turn is already running on this driver instance")

        argv = self._build_argv(request)
        try:
            process = _process.spawn(
                argv,
                cwd=str(request.workspace),
                env=_process.child_env(self._env),
            )
        except OSError as exc:
            # Returning before the first yield is legal and is exactly why the
            # protocol forbids consuming this generator with a bare for loop.
            return TurnResult(status="failed", error=f"spawn failed: {exc}")

        with self._lock:
            self._process = process

        stderr_tail, pump = _process.start_stderr_pump(process)

        session_ref = ""
        # Every completed agent_message, in order. The last one is the answer;
        # the earlier ones are narration (trap 3).
        messages: list[str] = []
        stray_lines: list[str] = []
        outcome: str = ""  # "completed" | "failed", set by a turn.* frame
        failure_detail = ""

        try:
            try:
                for line in process.stdout or ():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        frame = json.loads(line)
                    except ValueError:
                        # npm shims and progress spinners leak into stdout. A
                        # dirty line is a diagnostic, never a turn failure.
                        if len(stray_lines) < _process.MAX_STRAY_LINES:
                            stray_lines.append(line)
                        continue
                    if not isinstance(frame, dict):
                        if len(stray_lines) < _process.MAX_STRAY_LINES:
                            stray_lines.append(line)
                        continue

                    kind = str(frame.get("type") or "")

                    if kind == "thread.started":
                        announced = str(frame.get("thread_id") or "")
                        # Emitted the moment the handle exists, well before the
                        # turn ends. Waiting for the end loses it on crash --
                        # the bug PR #828 shipped.
                        if announced and announced != session_ref:
                            session_ref = announced
                            yield TurnEvent(kind="session_ref", text=session_ref, raw=frame)
                        continue

                    if kind in ("item.started", "item.completed"):
                        item = frame.get("item")
                        if not isinstance(item, dict):
                            continue
                        completed = kind == "item.completed"
                        event = _item_event(item, completed=completed)
                        if event is None:
                            continue
                        if completed and event.kind == "assistant_text" and event.text:
                            messages.append(event.text)
                        yield event
                        continue

                    if kind == "turn.completed":
                        outcome = "completed"
                        break

                    if kind == "turn.failed":
                        outcome = "failed"
                        failure_detail = _failure_text(frame)
                        break
            except ValueError:
                # ``cancel()`` on another thread closed stdout under this read.
                pass

            exit_code = _process.wait(process, _process.PROCESS_WAIT_SECONDS)
            stderr_text = _process.redact("\n".join(stderr_tail), self._env)
            final_text = messages[-1] if messages else ""

            if outcome == "completed":
                return TurnResult(
                    status="completed",
                    session_ref=session_ref,
                    final_text=final_text,
                    exit_code=exit_code,
                )
            if outcome == "failed":
                detail = _process.redact(failure_detail, self._env) or stderr_text
                return TurnResult(
                    status="failed",
                    session_ref=session_ref,
                    final_text=final_text,
                    exit_code=exit_code,
                    error=detail or "runtime reported a failed turn",
                )

            # No terminal turn frame: the CLI died, or exited without ever
            # committing to an outcome. Both leave the turn unfinished, and
            # accumulated text must not be promoted into a success.
            return TurnResult(
                status="failed",
                session_ref=session_ref,
                final_text=final_text,
                exit_code=exit_code,
                error=_process.no_result_error(
                    exit_code, stderr_text, stray_lines, noun="turn.completed frame"
                ),
            )
        finally:
            # Reached on normal return, on an exception, and -- the case that
            # matters -- on the supervisor's ``close()`` at the deadline.
            self._reap(process)
            pump.join(timeout=_process.TERMINATE_GRACE_SECONDS)

    def cancel(self) -> None:
        """Best-effort abort of the live turn. Safe on an already-dead turn."""
        with self._lock:
            process = self._process
        if process is None:
            return
        self._reap(process)

    # ---- internals ---------------------------------------------------

    def _build_argv(self, request: TurnRequest) -> list[str]:
        """Assemble the argv, honouring resume's narrower option set (trap 4).

        Global flags -- including the operator's ``driverArgs`` -- go before the
        ``resume`` subcommand, because ``codex exec resume`` rejects options
        that ``codex exec`` accepts. Appending them after the subcommand, the
        way the Claude driver appends ``--resume``, fails with
        ``unexpected argument``.
        """
        argv = [
            *self._command,
            "exec",
            "--json",
            "-C",
            str(request.workspace),
        ]
        argv += list(self._extra_args)
        if request.session_ref:
            argv += ["resume", request.session_ref]
        argv.append(request.prompt)
        return argv

    def _reap(self, process: subprocess.Popen[str]) -> None:
        _process.reap(process)
        with self._lock:
            if self._process is process:
                self._process = None


# ---- MCP declaration --------------------------------------------------


def mcp_config_args(ctx: AssetContext) -> tuple[str, ...]:
    """Declare the TeamHarness MCP server as ``-c`` overrides, writing nothing.

    Claude Code takes its MCP config from a projected ``.mcp.json`` inside the
    workspace. Codex has no project-level equivalent: servers live globally in
    ``~/.codex/config.toml``, alongside whatever the operator configured for
    themselves. Projecting into that file would make installing a team member
    mutate the operator's machine-wide setup, and uninstalling it would mean
    editing their global config back. Passing the same declaration as
    per-invocation ``-c`` overrides gets the tools in front of the agent while
    leaving nothing behind -- there is no uninstall because there was no
    install.

    **Credential handling, and why no secrets appear here.** ``-c`` values land
    in the process argument list, which on a shared machine is readable by
    other processes -- strictly worse than a file with the operator's own
    permissions. So this writes no secret values and no ``${VAR}`` references
    either: only the literal, non-secret role and encoding pins. Everything
    sensitive (Matrix token, storage keys) reaches the MCP server the way it
    reaches Codex itself -- by ordinary environment inheritance from the bridge
    process, which never serialises it anywhere.

    That assumption was tested on a live run and **came back false**: Codex
    passes none of its own environment to a stdio MCP child, and an ``env``
    table is the child's entire environment rather than an overlay on it. The
    trade-off did not reopen, because Codex has a second field for exactly this
    case. ``env_vars`` is a list of variable *names* inherited from the parent
    process, so the passthrough set crosses as names only -- the same shape as
    ``AssetContext.mcp_env_passthrough``, and the same shape as the ``${VAR}``
    references the Claude Code projector writes. No value is serialised.

    The failure this prevents is silent, which is why it is worth the words:
    with no ``AGENTTEAMS_STORAGE_PREFIX`` the server built an unprefixed remote
    path, ``mc`` treated it as a local directory, and ``filesync push`` returned
    ``{"ok": true}`` having copied the file next to itself on disk.
    """
    server_path = Path(ctx.plugin_dir) / "mcp" / "server.py"
    prefix = f"mcp_servers.{MCP_SERVER_ID}"
    # Values are parsed as TOML, so strings need their quotes and the args list
    # needs TOML array syntax.
    shared_dir = str(Path(ctx.workspace) / "shared")
    env_pairs = ", ".join(
        (
            f'{ROLE_ENV_VAR} = "{ctx.role or "remote-member"}"',
            # Without this, taskflow/filesync cannot infer the workspace and
            # every call fails with "workspaceDir is required" -- a worker
            # container gets it from QWENPAW_WORKING_DIR, a remote member has
            # no equivalent. A path, not a credential.
            f"{SHARED_DIR_ENV_VAR} = {_toml_str(shared_dir)}",
            # MCP frames are UTF-8, but Python's standard streams follow the
            # platform locale: on a Chinese Windows install the server encodes
            # its own tool descriptions as GBK and the client silently sees no
            # tools at all.
            'PYTHONIOENCODING = "utf-8"',
        )
    )
    # Names only. Codex rejects a table here ("invalid type: map, expected a
    # sequence"), which is a happy accident: the list form has nowhere to put a
    # value even by mistake.
    inherited = ", ".join(_toml_str(name) for name in ctx.mcp_env_passthrough)
    return (
        "-c",
        # The interpreter running the bridge, not the bare name "python": the
        # CLI spawns this server from its own environment, where "python" may
        # be a different install or absent entirely.
        f'{prefix}.command={_toml_str(sys.executable or "python")}',
        "-c",
        f"{prefix}.args=[{_toml_str(str(server_path))}]",
        "-c",
        f"{prefix}.env={{{env_pairs}}}",
        "-c",
        f"{prefix}.env_vars=[{inherited}]",
    )


def _toml_str(value: str) -> str:
    """Quote a TOML basic string. Windows paths are full of backslashes."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


# ---- frame translation ----------------------------------------------


def _item_event(item: dict[str, Any], *, completed: bool) -> TurnEvent | None:
    """Map one Codex item to a normalized event, or ``None`` to drop it.

    ``item.started`` and ``item.completed`` both arrive for the same item.
    Prose is only taken from the completed form -- a started ``agent_message``
    has no final text -- while tool activity is reported from both so a
    supervisor can see work in flight.
    """
    item_type = str(item.get("type") or "")

    if item_type == _TEXT_ITEM:
        if not completed:
            return None
        text = str(item.get("text") or "")
        return TurnEvent(kind="assistant_text", text=text, raw=item) if text else None

    if item_type == _REASONING_ITEM:
        if not completed:
            return None
        return TurnEvent(kind="reasoning", text=_reasoning_text(item), raw=item)

    if item_type == _ERROR_ITEM:
        # An error *item* is a diagnostic, not a verdict on the turn (trap 2).
        # Only turn.failed decides the outcome.
        return TurnEvent(kind="error", text=str(item.get("message") or ""), raw=item)

    if item_type in _TOOL_ITEMS:
        kind = "tool_result" if completed else "tool_call"
        return TurnEvent(kind=kind, text=_tool_label(item), raw=item)

    # Unknown item types are surfaced as tool activity rather than dropped or
    # promoted to text: a future Codex version adding an item type must not be
    # able to inject unreviewed prose into a room.
    return TurnEvent(kind="tool_call", text=item_type, raw=item) if item_type else None


def _tool_label(item: dict[str, Any]) -> str:
    """A short human label, so policy and logs need not re-parse ``raw``."""
    for key in ("command", "name", "tool", "query", "path"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return str(item.get("type") or "")


def _reasoning_text(item: dict[str, Any]) -> str:
    for key in ("text", "summary", "content"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _failure_text(frame: dict[str, Any]) -> str:
    """Pull a message out of ``turn.failed``, whatever shape it carries."""
    error = frame.get("error")
    if isinstance(error, str) and error:
        return error
    if isinstance(error, dict):
        for key in ("message", "detail", "reason"):
            value = error.get(key)
            if isinstance(value, str) and value:
                return value
    for key in ("message", "detail", "reason"):
        value = frame.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _has_local_credentials() -> bool:
    """Existence check only -- these files are never opened. See README.

    Codex stores its token under ``$CODEX_HOME`` (default ``~/.codex``). The
    bridge does not authenticate the runtime and must never read, copy, or log
    the file; presence is the strongest signal it is allowed to take.
    """
    home = os.getenv("CODEX_HOME", "").strip()
    root = Path(home) if home else Path.home() / ".codex"
    for candidate in (root / "auth.json", root / "config.toml"):
        try:
            if candidate.exists():
                return True
        except OSError:
            continue
    return False
