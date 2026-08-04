#!/usr/bin/env python3
"""Claude Code driver: one bounded turn over headless ``stream-json``.

The runtime contract is ``claude -p <prompt> --output-format stream-json
--verbose``: the CLI writes one JSON object per stdout line and exits when the
turn ends. This module translates those frames into ``protocol.TurnEvent``
values and nothing else -- no Matrix, no room routing, no retry policy.

Three properties are load-bearing and each one exists because of a past bug:

``session_ref`` is emitted the instant it appears
    The ``system``/``init`` frame carries the resume handle on the very first
    line. Emitting it there (instead of at turn end) is what lets the session
    store persist a handle that survives a mid-turn crash -- PR #828 emitted it
    at the end, so ``--resume`` never fired. A resumed turn may also be issued
    a *different* session id than the one passed to ``--resume``, so any later
    frame carrying a new id re-emits the event; the last one wins.

The child process is always reaped
    The supervisor owns the clock: at the deadline it stops consuming and calls
    ``close()`` on the generator, which raises ``GeneratorExit`` at the pending
    ``yield``. The ``finally`` path terminates (then kills) the child, so an
    interrupted turn cannot leave an orphaned agent writing into the workspace.

Credentials are never touched
    ``probe`` checks only for the *existence* of the local Claude Code
    credential files and never opens them. Values from the injected ``env``
    overlay are redacted out of any stderr text that reaches an error message.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import threading
from typing import Any, Generator, Sequence

from . import _process
from ..protocol import DriverProbe, TurnEvent, TurnRequest, TurnResult

# Process plumbing -- timeouts, stderr bounds, the redaction threshold, reaping
# -- is shared with every other driver in ``_process``. Only protocol
# translation lives below. Two copies of process reaping is how one of them
# quietly stops reaping.
PROBE_TIMEOUT_SECONDS = _process.PROBE_TIMEOUT_SECONDS
PROCESS_WAIT_SECONDS = _process.PROCESS_WAIT_SECONDS
TERMINATE_GRACE_SECONDS = _process.TERMINATE_GRACE_SECONDS
STDERR_TAIL_CHARS = _process.STDERR_TAIL_CHARS
MAX_STRAY_LINES = _process.MAX_STRAY_LINES


class ClaudeCodeDriver:
    """Drive one Claude Code turn per instance-at-a-time.

    ``binary`` accepts either a command name resolved through ``PATH`` or a
    full argv prefix (``("/usr/bin/python", "/path/to/wrapper.py")``). The
    sequence form exists so that a test double, a version pin, or a
    ``docker exec`` prefix needs no wrapper script on disk; the first element is
    what ``probe`` resolves and reports.

    ``extra_args`` is appended verbatim to every invocation. Permission policy
    (``--permission-mode``, ``--allowedTools``, settings paths) is deliberately
    the caller's decision: the driver has no opinion the supervisor cannot
    express, and baking one in here would mean two places to audit.
    """

    name = "claude-code"

    def __init__(
        self,
        binary: str | Sequence[str] = "claude",
        extra_args: tuple[str, ...] = (),
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
        # Guards the single live child. One driver instance owns at most one
        # turn (see protocol.RuntimeDriver), so a lock plus one slot is the
        # whole bookkeeping ``cancel()`` needs.
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None

    @property
    def binary(self) -> str:
        """The executable ``probe`` resolves; the argv prefix's first element."""
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
                timeout=PROBE_TIMEOUT_SECONDS,
                env=self._child_env(),
            )
        except subprocess.TimeoutExpired:
            return DriverProbe(
                available=False,
                binary=resolved,
                reason=f"`--version` timed out after {PROBE_TIMEOUT_SECONDS}s",
            )
        except OSError as exc:
            return DriverProbe(available=False, binary=resolved, reason=f"spawn failed: {exc}")
        if completed.returncode != 0:
            detail = _process.tail(self._redact(completed.stderr or completed.stdout or ""))
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
                env=self._child_env(),
            )
        except OSError as exc:
            return TurnResult(status="failed", error=f"spawn failed: {exc}")

        with self._lock:
            self._process = process

        # stderr is drained by a thread rather than read after the fact: a child
        # that fills the stderr pipe while we block on stdout deadlocks both.
        stderr_tail, pump = _process.start_stderr_pump(process)

        session_ref = ""
        final_text = ""
        assistant_chunks: list[str] = []
        stray_lines: list[str] = []
        result_frame: dict[str, Any] | None = None

        try:
            try:
                for line in process.stdout or ():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        frame = json.loads(line)
                    except ValueError:
                        # Wrappers, npm banners, and progress spinners all leak
                        # into stdout. A dirty line is a diagnostic, never a
                        # turn failure.
                        if len(stray_lines) < MAX_STRAY_LINES:
                            stray_lines.append(line)
                        continue
                    if not isinstance(frame, dict):
                        if len(stray_lines) < MAX_STRAY_LINES:
                            stray_lines.append(line)
                        continue

                    # Any frame may carry the handle, and a resumed turn can be
                    # given a new one mid-stream. Latest wins, and each change is
                    # announced immediately so the session store stays current.
                    announced = str(frame.get("session_id") or "")
                    if announced and announced != session_ref:
                        session_ref = announced
                        yield TurnEvent(kind="session_ref", text=session_ref, raw=frame)

                    kind = str(frame.get("type") or "")
                    if kind == "assistant":
                        for event in _assistant_events(frame):
                            if event.kind == "assistant_text":
                                assistant_chunks.append(event.text)
                            yield event
                    elif kind == "user":
                        for event in _tool_result_events(frame):
                            yield event
                    elif kind == "result":
                        result_frame = frame
                        break
                    elif kind == "error":
                        yield TurnEvent(
                            kind="error", text=str(frame.get("message") or ""), raw=frame
                        )
            except ValueError:
                # ``cancel()`` on another thread closes stdout under this read.
                # The usual path is terminate -> EOF -> clean loop exit, but a
                # close that lands mid-read raises here instead; treat it the
                # same as EOF and let the no-result path below report the abort.
                pass

            exit_code = _process.wait(process, PROCESS_WAIT_SECONDS)
            stderr_text = self._redact("\n".join(stderr_tail))

            if result_frame is not None:
                subtype = str(result_frame.get("subtype") or "")
                text = _result_text(result_frame)
                if subtype == "success" and not result_frame.get("is_error"):
                    final_text = text or "\n".join(c for c in assistant_chunks if c)
                    return TurnResult(
                        status="completed",
                        session_ref=session_ref,
                        final_text=final_text,
                        exit_code=exit_code,
                    )
                return TurnResult(
                    status="failed",
                    session_ref=session_ref,
                    final_text=text,
                    exit_code=exit_code,
                    error=self._redact(text or subtype or "runtime reported an error"),
                )

            # No result frame. Either the CLI died or it exited without ever
            # committing to an outcome; both leave the turn unfinished.
            return TurnResult(
                status="failed",
                session_ref=session_ref,
                final_text="\n".join(c for c in assistant_chunks if c),
                exit_code=exit_code,
                error=_no_result_error(exit_code, stderr_text, stray_lines),
            )
        finally:
            # Reached on normal return, on an exception, and -- the case that
            # matters -- on the supervisor's ``close()`` at the deadline.
            self._reap(process)
            pump.join(timeout=TERMINATE_GRACE_SECONDS)

    def cancel(self) -> None:
        """Best-effort abort of the live turn. Safe on an already-dead turn."""
        with self._lock:
            process = self._process
        if process is None:
            return
        self._reap(process)

    # ---- internals ---------------------------------------------------

    def _build_argv(self, request: TurnRequest) -> list[str]:
        argv = [
            *self._command,
            "-p",
            request.prompt,
            "--output-format",
            "stream-json",
            "--verbose",  # stream-json emits only the result frame without it
        ]
        if request.session_ref:
            argv += ["--resume", request.session_ref]
        argv += list(self._extra_args)
        return argv

    def _child_env(self) -> dict[str, str] | None:
        return _process.child_env(self._env)

    def _redact(self, text: str) -> str:
        return _process.redact(text, self._env)

    def _reap(self, process: subprocess.Popen[str]) -> None:
        """Terminate, then kill, then release the pipes. Idempotent."""
        _process.reap(process)
        with self._lock:
            if self._process is process:
                self._process = None


# ---- frame translation ----------------------------------------------


def _assistant_events(frame: dict[str, Any]) -> list[TurnEvent]:
    """Split one ``assistant`` frame's content blocks into events."""
    events: list[TurnEvent] = []
    for block in _content_blocks(frame):
        block_type = str(block.get("type") or "")
        if block_type == "text":
            text = str(block.get("text") or "")
            if text:
                events.append(TurnEvent(kind="assistant_text", text=text, raw=block))
        elif block_type == "tool_use":
            # text is the tool name so a supervisor can apply policy without
            # re-parsing raw; the full block stays available for audit.
            events.append(
                TurnEvent(kind="tool_call", text=str(block.get("name") or ""), raw=block)
            )
        elif block_type in ("thinking", "redacted_thinking"):
            events.append(
                TurnEvent(kind="reasoning", text=str(block.get("thinking") or ""), raw=block)
            )
    return events


def _tool_result_events(frame: dict[str, Any]) -> list[TurnEvent]:
    """Claude Code echoes tool output back as a ``user`` frame."""
    events: list[TurnEvent] = []
    for block in _content_blocks(frame):
        if str(block.get("type") or "") != "tool_result":
            continue
        events.append(
            TurnEvent(kind="tool_result", text=_flatten_content(block.get("content")), raw=block)
        )
    return events


def _content_blocks(frame: dict[str, Any]) -> list[dict[str, Any]]:
    message = frame.get("message")
    content = message.get("content") if isinstance(message, dict) else frame.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]


def _flatten_content(content: Any) -> str:
    """Tool results arrive as a string or as a list of content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return "\n".join(part for part in parts if part)
    return ""


def _result_text(frame: dict[str, Any]) -> str:
    for key in ("result", "error", "message"):
        value = frame.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _no_result_error(exit_code: int | None, stderr_text: str, stray_lines: list[str]) -> str:
    if exit_code is None:
        head = "runtime did not exit after its stream closed"
    elif exit_code != 0:
        head = f"runtime exited {exit_code} without a result frame"
    else:
        head = "runtime exited cleanly without a result frame"
    detail = stderr_text or ("\n".join(stray_lines) if stray_lines else "")
    return f"{head}: {_process.tail(detail)}" if detail else head


# ---- process helpers -------------------------------------------------


def _has_local_credentials() -> bool:
    """Existence check only -- these files are never opened. See README.

    Claude Code stores its token in ``~/.claude/.credentials.json`` (or the OS
    keychain, with ``~/.claude.json`` present once the CLI has been set up).
    The bridge does not authenticate the runtime and must never read, copy, or
    log either file; presence is the strongest signal it is allowed to take.
    """
    home = Path.home()
    for candidate in (home / ".claude" / ".credentials.json", home / ".claude.json"):
        try:
            if candidate.exists():
                return True
        except OSError:
            continue
    return False
