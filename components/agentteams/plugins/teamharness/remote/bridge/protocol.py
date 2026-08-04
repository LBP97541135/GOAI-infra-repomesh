#!/usr/bin/env python3
"""Runtime-neutral execution contracts for TeamHarness remote members.

Two protocols live here, deliberately kept apart:

``AssetProjector``
    Maps TeamHarness prompts, skills, and MCP definitions into a concrete
    local coding agent (``CLAUDE.md`` + ``.claude/skills`` + ``.mcp.json`` for
    Claude Code, ``AGENTS.md`` + ``config.toml`` for Codex). Implementations
    live in ``bridge/projectors/{runtime}.py``.

``RuntimeDriver``
    Drives one bounded turn of the agent through its headless protocol and
    reports normalized events. Implementations live in
    ``bridge/drivers/{runtime}.py``.

Keeping them apart is what makes the Codex swap cheap: Codex reuses the whole
bridge (supervision, dedup, session store) and replaces only these two leaves.
A single fused interface would force the supervision logic to be rewritten per
runtime, which is the mistake that sank PR #828.

Both are *runtime* seams, not directory seams. An earlier draft put projection
under ``adapters/{runtime}/`` to mirror the qwenpaw adapter, but that adapter
is an in-process plugin of a Python runtime and can be imported by it; a
projector is imported by the supervisor and must reach ``..protocol``, so it
belongs inside the bridge package. The separation that matters -- one type per
concern, swappable per runtime -- is unaffected.

The bridge never speaks Matrix. Outbound room traffic is either the driver's
own reply stream (current-room answers) or the TeamHarness MCP tools that the
agent calls itself (``taskflow`` / ``artifact`` / ``filesync``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generator, Literal, Protocol, runtime_checkable

TurnStatus = Literal[
    "completed",  # the agent finished the turn on its own
    "failed",  # the agent errored or exited non-zero
    "timeout",  # the supervisor cut the turn at the deadline
    "cancelled",  # an explicit cancel arrived (leader cancel_task, shutdown)
    "blocked",  # the agent asked for a permission the policy denies
]

EventKind = Literal[
    "assistant_text",  # user-visible prose the bridge may forward to the room
    "reasoning",  # thinking / internal narration; never forwarded
    "tool_call",
    "tool_result",
    "permission_request",
    "session_ref",  # the driver learned its resume handle; store it immediately
    "error",
]


@dataclass(frozen=True)
class DriverProbe:
    """Result of checking whether a local runtime is usable at all."""

    available: bool
    binary: str = ""
    version: str = ""
    authenticated: bool = False
    reason: str = ""


@dataclass(frozen=True)
class AssetContext:
    """Non-secret facts an adapter needs to project TeamHarness assets.

    Sourced from the controller-written member runtime config
    (``shared/runtime/members/{memberName}/runtime.yaml``), never from the
    ``agt`` CLI -- see docs/teamharness-boundary-and-contracts.md.
    """

    workspace: Path
    role: str  # "remote-member" for this path
    member_name: str
    team_name: str
    plugin_dir: Path  # unpacked TeamHarness package root
    # Names of environment variables the projected MCP config must pass through
    # to the TeamHarness server (e.g. AGENTTEAMS_MATRIX_URL,
    # AGENTTEAMS_WORKER_MATRIX_TOKEN). NAMES ONLY, by construction: an adapter
    # writes ``"env": {"VAR": "${VAR}"}`` references into ``.mcp.json`` and the
    # runtime expands them at spawn time. Secret *values* must never appear in
    # any file an adapter projects -- that is the credential red line, and a
    # ``dict[str, str]`` here would make violating it a one-line mistake.
    mcp_env_passthrough: tuple[str, ...] = ()
    # Room and identity coordinates the agent needs in order to *understand*
    # its team rather than merely be driven by it: without them a projected
    # contract reads as generic documentation and the agent cannot name the
    # team it belongs to. All four are public routing facts -- room ids and
    # user ids are visible to every member of the room -- and none of them is,
    # or points at, a credential. Optional because a bootstrap file that omits
    # a leader or a personal room is valid; empty fields are simply not
    # projected.
    team_room_id: str = ""
    leader_name: str = ""
    matrix_user_id: str = ""
    personal_room_id: str = ""


@dataclass(frozen=True)
class AssetProjection:
    """What an adapter actually wrote, so the bridge can log and uninstall."""

    files: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class TurnRequest:
    """One bounded unit of work handed to the agent.

    ``session_ref`` is the runtime's own resume handle (Claude Code session id,
    Codex thread id). ``None`` means start fresh. It is keyed by task, not by
    room: one room can carry several concurrent tasks, and sharing a session
    across them is how context bleed happens (upstream issue #603).
    """

    task_id: str
    room_id: str
    prompt: str
    workspace: Path
    session_ref: str | None = None
    timeout_seconds: float = 900.0
    trigger_event_id: str = ""  # for idempotency; see dedup.TurnLedger


@dataclass(frozen=True)
class TurnEvent:
    kind: EventKind
    text: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TurnResult:
    status: TurnStatus
    session_ref: str = ""
    final_text: str = ""
    exit_code: int | None = None
    error: str = ""


@runtime_checkable
class AssetProjector(Protocol):
    """Adapter side: install TeamHarness assets into a concrete runtime."""

    name: str

    def project(self, ctx: AssetContext) -> AssetProjection:
        """Write prompts, skills, and MCP config. Must be idempotent."""

    def unproject(self, ctx: AssetContext) -> AssetProjection:
        """Remove only what ``project`` wrote. Must not touch user assets."""


@runtime_checkable
class RuntimeDriver(Protocol):
    """Bridge side: execute one turn through a headless CLI protocol.

    One driver instance owns at most one live turn at a time; the supervisor
    creates one instance per concurrent task. This keeps the surface free of
    per-task bookkeeping -- ``cancel`` needs no arguments because there is
    never ambiguity about which turn it aborts.

    ``run_turn`` is a *generator*, not a callback sink, and its ``return``
    value is the ``TurnResult`` (read from ``StopIteration.value`` when
    consuming manually, or received naturally via ``yield from``). The
    supervisor owns the clock:

    - Turn finishes on its own: the generator returns, and the driver reports
      ``completed`` / ``failed`` / ``blocked``.
    - Supervisor interrupts (deadline, cancel_task, shutdown): it stops
      consuming and closes the generator. The driver's ``GeneratorExit`` /
      ``cancel`` path must reap the child process; the supervisor synthesizes
      the ``timeout`` / ``cancelled`` result itself, since only it knows why
      it stopped.

    Both stream-json (Claude Code) and JSON-RPC-over-stdio (Codex
    ``app-server``) map onto this shape.

    Consumer obligations (violating any of these silently loses results):

    - Consume with ``yield from`` or read ``StopIteration.value`` explicitly.
      A bare ``for event in gen`` loop discards the ``TurnResult`` -- and a
      driver may legitimately return before its first yield (e.g. spawn
      failure), which a bare loop observes as an empty, successful iteration.
    - A runtime that exits cleanly without committing an outcome is a FAILED
      turn, not a completed one. Drivers must not upgrade accumulated
      assistant text into ``completed`` on their own.
    - ``permission_request`` events and the ``blocked`` status are best-effort
      vocabulary: some runtimes (headless Claude Code among them) surface
      denied permissions as tool errors or a failed result instead. A
      supervisor must not build control flow that requires them to arrive.

    Drivers must emit a ``session_ref`` event as soon as the runtime reveals
    its resume handle -- typically on the first frame, well before the turn
    ends. Waiting for the final result loses the handle on crash, which is
    exactly the bug #828 shipped (``matrix_relay.py`` always passed ``None``
    to ``on_invoke``, so ``--resume`` never fired).
    """

    name: str

    def probe(self) -> DriverProbe:
        """Check binary presence, version, and local auth. No side effects."""

    def run_turn(self, request: TurnRequest) -> Generator[TurnEvent, None, TurnResult]:
        """Yield normalized events; return the ``TurnResult`` on completion."""

    def cancel(self) -> None:
        """Best-effort abort of the live turn. Safe on an already-dead turn."""
