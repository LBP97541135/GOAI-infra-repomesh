#!/usr/bin/env python3
"""Subprocess plumbing shared by every headless-CLI driver.

Nothing here knows a protocol. It is the part of "drive a child process and
survive it" that is identical whether the child speaks Claude Code stream-json
or Codex JSONL: bounded waits, a stderr pump that cannot deadlock, terminate ->
grace -> kill, and secret redaction on anything user-visible.

This module exists because the second driver would otherwise have copied it.
Two copies of process reaping is how one of them quietly stops reaping: the
bug lands in the copy nobody is currently debugging. Keeping the runtime seam
at *protocol translation* -- and only there -- is the whole point of the
drivers/ layer.
"""

from __future__ import annotations

from collections import deque
import os
import shutil
import subprocess
import threading
from typing import Any

# A `--version` that has not answered in this long is a broken install, not a
# slow one.
PROBE_TIMEOUT_SECONDS = 10
# How long to wait for a child to exit once its stream has closed.
PROCESS_WAIT_SECONDS = 15
# Grace between terminate and kill.
TERMINATE_GRACE_SECONDS = 5
# Bounded stderr retention: enough to diagnose, never enough to exhaust memory
# on a child that loops printing.
STDERR_TAIL_LINES = 50
STDERR_TAIL_CHARS = 2000
# Non-JSON stdout lines kept for diagnostics before the rest are dropped.
MAX_STRAY_LINES = 20
# Shorter injected values are not worth redacting and would mangle ordinary
# text; a real token is far longer than this.
MIN_REDACTABLE_SECRET = 8


def resolve_command(command: tuple[str, ...]) -> tuple[str, ...]:
    """Replace argv[0] with its fully resolved path, when one can be found.

    Required on Windows, where npm installs a CLI as a ``.CMD`` shim: spawning
    the bare name ``codex`` raises ``FileNotFoundError [WinError 2]`` even
    though ``shutil.which`` finds ``codex.CMD`` and spawning *that* path works.
    Without this, a driver reports "executable not found" for a CLI that is
    installed, on PATH, and runnable -- and the OS error text arrives in the
    console locale, so on a Chinese Windows install the message is mojibake
    with no clue that the cause is the shim.

    ``claude`` is a native ``.exe`` under its own installer and is unaffected;
    the same install via npm is not. Resolving unconditionally costs nothing
    and removes the difference.

    Returns the command unchanged when nothing resolves, so the caller's own
    "not found" reporting stays in charge of the error message.
    """
    if not command:
        return command
    resolved = shutil.which(command[0])
    return (resolved, *command[1:]) if resolved else command


def wait(process: subprocess.Popen[str], timeout: float) -> int | None:
    """``wait`` that reports a hung child as ``None`` instead of raising."""
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return None


def drain(stream: Any, sink: deque[str]) -> None:
    """Consume stderr into a bounded tail so the pipe can never fill up.

    Run this on a background thread. A child that fills its stderr pipe while
    the driver blocks reading stdout deadlocks both sides, and the symptom is a
    turn that hangs until the supervisor's deadline with no output to explain
    why.
    """
    if stream is None:
        return
    try:
        for line in stream:
            sink.append(line.rstrip("\r\n"))
    except (OSError, ValueError):
        # The pipe was closed by reap() while this thread was blocked on it.
        pass


def reap(process: subprocess.Popen[str]) -> None:
    """Terminate, then kill, then release the pipes. Idempotent.

    Called from ``finally`` on every exit path, including the supervisor's
    ``close()`` at the deadline. An unreaped child keeps writing to the
    operator's workspace after the turn was abandoned.
    """
    if process.poll() is None:
        try:
            # On Windows this is TerminateProcess, i.e. already the hard stop;
            # on POSIX it is SIGTERM and the kill below is the backstop for a
            # child that ignores it.
            process.terminate()
        except OSError:
            pass
        if wait(process, TERMINATE_GRACE_SECONDS) is None:
            try:
                process.kill()
            except OSError:
                pass
            wait(process, TERMINATE_GRACE_SECONDS)
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass


def spawn(
    argv: list[str],
    *,
    cwd: str,
    env: dict[str, str] | None,
) -> subprocess.Popen[str]:
    """Start a headless CLI with the pipe and decoding settings drivers need.

    Two of these are load-bearing rather than stylistic:

    - ``stdin=DEVNULL``. Codex ``exec`` reads stdin even when the prompt is an
      argument, and blocks until EOF; inheriting a pipe hangs the turn forever
      with no output. Claude Code does not need it but is not harmed by it.
    - ``encoding="utf-8"``. These CLIs emit UTF-8 regardless of platform, but
      Python decodes a text-mode pipe with the *locale* encoding -- GBK on a
      Chinese Windows install. Without pinning, every non-ASCII character in an
      agent's answer is corrupted on the way into the room.
    """
    return subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )


def start_stderr_pump(process: subprocess.Popen[str]) -> tuple[deque[str], threading.Thread]:
    """Begin draining stderr into a bounded tail. Join the thread in ``finally``."""
    tail: deque[str] = deque(maxlen=STDERR_TAIL_LINES)
    pump = threading.Thread(target=drain, args=(process.stderr, tail), daemon=True)
    pump.start()
    return tail, pump


def child_env(injected: dict[str, str]) -> dict[str, str] | None:
    """Inherit the parent environment, overlaid with the injected values.

    ``None`` (plain inheritance) when nothing was injected, so the common case
    leaves ``Popen`` on its default path.
    """
    if not injected:
        return None
    merged = dict(os.environ)
    merged.update(injected)
    return merged


def redact(text: str, injected: dict[str, str]) -> str:
    """Strip injected secret values out of anything user-visible.

    The bridge passes Matrix and storage tokens through ``env``; a CLI that
    echoes its environment on crash would otherwise leak them into a room
    message via the error field.
    """
    if not text:
        return ""
    for value in injected.values():
        if value and len(value) >= MIN_REDACTABLE_SECRET:
            text = text.replace(value, "***")
    return text


def first_line(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def tail(text: str, limit: int = STDERR_TAIL_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return "..." + text[-limit:]


def no_result_error(
    exit_code: int | None,
    stderr_text: str,
    stray_lines: list[str],
    *,
    noun: str = "result frame",
) -> str:
    """Explain a turn that ended without the runtime committing to an outcome.

    A runtime that exits cleanly without committing is a FAILED turn, not a
    completed one (see protocol.RuntimeDriver) -- this builds the message that
    says so in terms an operator can act on.
    """
    if exit_code is None:
        head = "runtime did not exit after its stream closed"
    elif exit_code != 0:
        head = f"runtime exited {exit_code} without a {noun}"
    else:
        head = f"runtime exited cleanly without a {noun}"
    detail = stderr_text or ("\n".join(stray_lines) if stray_lines else "")
    return f"{head}: {tail(detail)}" if detail else head
