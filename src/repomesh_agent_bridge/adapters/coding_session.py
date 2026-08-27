"""The real coding session: codex, over the Runner driver, behind a restriction.

This is the PR 4 production side of :class:`~repomesh_agent_bridge.ports.CodingSessionPort`
(decisions H-6, H-7, H-10..H-13). It turns a room turn into one ``codex app-server``
execution by consuming the Runner's ``ProtocolDriver.execute`` — the driver stack
is *used*, never copied (ADR 0004 decision 4) — and it launches the CLI only
through the restricted process factory, so the CLI never sees the operator's
environment, a writable view of a real repository, or a process the Bridge cannot
kill.

Three properties are structural rather than a matter of reading the branches:

* **The room never sees a transcript.** The only text that reaches a room is
  ``DriverResult.summary`` — the deliverable the driver already selected out of
  the stream — or one of a handful of canned lines. The observer this adapter
  hands the driver projects *nothing*: THINKING, TOOL_USE, TOOL_RESULT, LOG,
  PERMISSION_REQUEST and every raw frame are dropped where they arrive, so there
  is no path by which one becomes a :class:`~repomesh_agent_bridge.contracts.RoomObservation`.
* **Every tool call is denied.** The permission policy has one return value —
  ``DENY`` — so a codex turn cannot run a command, and the Bridge never has to
  answer an escalation it has no channel for this tier.
* **A failed turn tells the room nothing.** Diagnostics go to this machine's
  log; the room gets one canned line with no summary, path or command in it,
  which is the same discipline the supervisor keeps for its own failures.

``ensure_ready`` is the startup gate. It is the one place that spawns before the
Bridge takes a message on, and it reaps whatever it spawned before it returns or
raises, because ``close`` is only registered against a session that got past it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from repomesh_runner.drivers.base import (
    DriverEvent,
    DriverEventKind,
    DriverRequest,
    DriverResult,
    DriverResultStatus,
    PermissionDecision,
    ProtocolDriver,
)
from repomesh_runner.drivers.supervision import (
    ProcessHandle,
    SpawnSpec,
)
from repomesh_runner.drivers.supervision import (
    resolve_binary as _default_resolve_binary,
)
from repomesh_runner.profiles import CliProfile, get_profile

from ..contracts import RoomObservation, SessionNotReady
from ..instance_lock import default_state_dir
from ..ports import TurnOutcome, TurnRequest
from .restricted_process import (
    RestrictedProcessFactory,
    SessionDirs,
    prepare_session_dirs,
)

__all__ = ["CODEX_PROFILE_ID", "DriverCodingSession", "session_root"]

_logger = logging.getLogger(__name__)

CODEX_PROFILE_ID = "codex"

# codex ships on this machine as an npm launcher (``codex.CMD``) that shells out
# to ``node``; the child therefore has to be able to locate both executables. The
# node binary is resolved the same way codex is, and a missing one is refused in
# the same segment as a missing codex.
_NODE_BINARIES = ("node",)

# Driver watchdog windows (H-13). Both sit below the supervisor's 900s turn
# timeout so the driver expires first and returns a structured TIMEOUT, rather
# than racing the supervisor's ``asyncio.timeout`` for the same deadline and
# leaving the less informative layer to report the failure.
_IDLE_WINDOW_SECONDS = 180.0
_TOOL_WINDOW_SECONDS = 840.0

_HANDSHAKE_TIMEOUT_SECONDS = 30.0

# One JSON-RPC ``initialize`` request, sent during the readiness handshake to
# prove codex actually starts under the restricted environment. The client
# speaks strict JSON-RPC even though codex's own replies omit the member.
_INITIALIZE_FRAME = (
    json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"clientInfo": {"name": "repomesh-agent-bridge", "version": "0.1.0"}},
        },
        separators=(",", ":"),
    )
    + "\n"
).encode()

# The only things a room is ever told about a turn that did not deliver. No
# summary, no diagnostics, no path — a room is a place other people read, and the
# detail belongs to this machine's log.
_FAILED_NOTE = "I could not finish that turn. The details are in this machine's log."
_BLOCKED_NOTE = (
    "That turn needs input I cannot provide from here, so I stopped. The details are in "
    "this machine's log."
)
_EMPTY_SUCCESS_NOTE = "I finished that turn, but it produced no text to show."

BinaryResolver = Callable[[tuple[str, ...]], str | None]


def session_root(worker_agent_id: UUID, state_dir: Path | None = None) -> Path:
    """Where one worker's coding-session directories live.

    Beside the state file and the instance lock, under the same ``state_dir`` and
    the same worker dimension (mirrors ``state_path``): the session's workspace,
    codex-home and tmp belong to the same per-worker island as everything else
    this instance owns, and never to the operator's own ``~/.codex``.
    """

    return (state_dir or default_state_dir()) / "sessions" / str(worker_agent_id)


class _DenyAllPolicy:
    """A permission policy that never opens the gate.

    Every tool a codex turn asks to run is denied, and nothing is ever allowed or
    escalated. One return value, so the guarantee that no tool runs and no
    escalation is ever produced is a property of the type, not of a reader
    checking the branches. Escalation in particular would ask the driver to end
    the turn as INPUT_REQUIRED, an answer this tier has no channel to resolve.
    """

    def decide(self, tool_name: str, tool_input: Mapping[str, object]) -> PermissionDecision:
        del tool_name, tool_input
        return PermissionDecision.DENY


class _SessionObserver:
    """Consumes driver events and projects none of them into the room.

    The room's whole view of a turn is ``DriverResult.summary``. The single thing
    kept here is the native session id a SESSION_STARTED announces — a fallback
    for the id the result carries, since a turn that fails may still have started
    a thread. Every other event kind, TEXT included, is deliberately dropped, so
    no THINKING block or tool frame can reach an observation.
    """

    def __init__(self) -> None:
        self.native_session_id: str | None = None

    def __call__(self, event: DriverEvent) -> None:
        if event.kind is DriverEventKind.SESSION_STARTED:
            value = event.payload.get("native_session_id")
            if isinstance(value, str) and value:
                self.native_session_id = value


class DriverCodingSession:
    """codex behind the Runner's app-server driver and the restricted factory.

    Assembled by ``run`` for a ``codingProfile: codex`` enrollment. The ``driver``
    holds the same restricted ``factory`` this adapter is given: the driver uses
    it to spawn each turn's process, and the adapter uses it to probe isolation
    and to run the readiness handshake, so a single containment boundary covers
    everything the CLI ever does.
    """

    def __init__(
        self,
        driver: ProtocolDriver,
        factory: RestrictedProcessFactory,
        *,
        session_dir: Path,
        worker_name: str,
        profile: CliProfile | None = None,
        binaries: tuple[str, ...] = (CODEX_PROFILE_ID,),
        resolve_binary: BinaryResolver = _default_resolve_binary,
    ) -> None:
        self._driver = driver
        self._factory = factory
        self._session_dir = Path(session_dir)
        self._worker_name = worker_name
        self._profile = profile or get_profile(CODEX_PROFILE_ID)
        self._binaries = binaries
        self._resolve_binary = resolve_binary
        self._binary: str | None = None
        self._path: str | None = None
        self._dirs: SessionDirs | None = None
        self._closed = False

    # -- startup gate (H-10) ------------------------------------------------

    async def ensure_ready(self) -> None:
        """Prove codex can serve under this machine's restrictions, or refuse.

        Three segments, each backed by something real. First codex and the node
        it launches through are located; a missing one is a refusal that names
        it, and the pair fixes the child's ``PATH`` (below). Then the restricted
        factory's isolation probe runs; anything the machine cannot actually
        enforce means the Bridge declines to launch a real session rather than
        launching one it cannot contain. Finally codex itself is started under
        the restriction and asked to answer an ``initialize`` handshake — the
        failure this catches is the experimental branch's: node/codex cannot
        start under a hand-built environment missing something they need, while
        the Bridge has already begun taking messages on. A successful handshake
        that is not logged in is still turned away, with the one command that
        fixes it.

        This method spawns and therefore must reap: the probe reaps its own
        child, and the handshake process is terminated before this returns or
        raises, because ``close`` is only owed a session that got past the gate.
        """

        binary = self._resolve_binary(self._binaries)
        if binary is None:
            raise SessionNotReady(
                f"coding CLI {self._binaries[0]!r} is not installed or not on PATH; "
                "install it and start the Bridge again"
            )
        node_binary = self._resolve_binary(_NODE_BINARIES)
        if node_binary is None:
            raise SessionNotReady(
                f"codex needs Node.js to start but {_NODE_BINARIES[0]!r} is not installed or "
                "not on PATH; install Node.js and start the Bridge again"
            )
        path_value = _executable_path(node_binary, binary)
        report = await self._factory.probe()
        if not report.required_ok:
            raise SessionNotReady(
                "this machine cannot contain a coding session, so the Bridge will not "
                f"run one:\n{report.summary()}"
            )
        dirs = prepare_session_dirs(self._session_dir, reset_workspace=True)
        await self._handshake(binary, path_value, dirs)
        auth = dirs.codex_home / "auth.json"
        if not auth.is_file():
            raise SessionNotReady(
                "codex is installed but not logged in for this Bridge: no auth.json under "
                f"its CODEX_HOME. Run `CODEX_HOME={dirs.codex_home} codex login` and start "
                "the Bridge again"
            )
        self._binary = binary
        self._path = path_value
        self._dirs = dirs

    async def _handshake(self, binary: str, path_value: str, dirs: SessionDirs) -> None:
        """Start codex under the restriction, read one line, reap it."""

        spec = SpawnSpec(
            executable=binary,
            arguments=tuple(self._profile.base_arguments),
            working_directory=dirs.workspace,
            environment=self._environment(dirs, path_value),
        )
        handle = await self._factory.spawn(spec)
        try:
            line = await self._probe_initialize(handle)
            if not line:
                tail = _safe_stderr(handle)
                reason = (
                    "codex did not answer an initialize handshake under the restricted "
                    "environment; it may be missing a variable it needs to start"
                )
                if tail:
                    reason = f"{reason} (stderr tail: {tail})"
                raise SessionNotReady(reason)
        finally:
            with contextlib.suppress(Exception):
                await asyncio.shield(handle.terminate())

    @staticmethod
    async def _probe_initialize(handle: ProcessHandle) -> bytes | None:
        try:
            handle.write_stdin(_INITIALIZE_FRAME)
            return await asyncio.wait_for(
                _first_nonempty_line(handle), timeout=_HANDSHAKE_TIMEOUT_SECONDS
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Any spawn/read failure is the finding: codex could not start and
            # speak here, which is exactly what the gate exists to catch.
            return None

    # -- one turn (H-11..H-13) ----------------------------------------------

    async def respond(self, turn: TurnRequest) -> TurnOutcome:
        """Serve one turn, resuming its thread when a handle was issued.

        The driver runs in its own task so cancellation stays deterministic. The
        app-server driver swallows ``CancelledError`` into an INTERRUPTED result
        (``app_server.py``), so awaiting it inline would let the supervisor's
        ``asyncio.timeout`` accounting be broken by a turn that reported a value
        where the timeout expected an exception. Shielding the child instead
        means a cancelled ``respond`` cancels the child, waits for it to reap its
        subprocess, and re-raises ``CancelledError`` unchanged (H-12).
        """

        if self._dirs is None or self._binary is None or self._path is None:
            raise SessionNotReady("respond was reached before ensure_ready opened the session")

        request = DriverRequest(
            executable=self._binary,
            workspace=self._dirs.workspace,
            prompt=turn.prompt,
            permission_policy=_DenyAllPolicy(),
            environment=self._environment(self._dirs, self._path),
            model=None,
            resume_session_id=turn.native_session_id,
            idle_window_seconds=_IDLE_WINDOW_SECONDS,
            tool_window_seconds=_TOOL_WINDOW_SECONDS,
        )
        observer = _SessionObserver()
        task = asyncio.ensure_future(self._driver.execute(request, self._profile, observer))
        try:
            result = await asyncio.shield(task)
        except asyncio.CancelledError:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
            raise
        return self._to_outcome(turn, result, observer)

    def _to_outcome(
        self, turn: TurnRequest, result: DriverResult, observer: _SessionObserver
    ) -> TurnOutcome:
        """Map one ``DriverResult`` to a room outcome (H-11).

        ``native_session_id`` is passed through for every status — a failed turn
        may already have announced a thread — falling back to the id the observer
        saw. Only a SUCCEEDED turn's summary reaches the room; every other status
        is one canned line, and the driver's diagnostics go to the log alone.
        """

        native = result.native_session_id or observer.native_session_id
        if result.status is DriverResultStatus.SUCCEEDED:
            body = result.summary.strip() or _EMPTY_SUCCESS_NOTE
            return TurnOutcome(
                observations=(self._note(turn, body),),
                native_session_id=native,
                status="completed",
            )
        if result.status is DriverResultStatus.INPUT_REQUIRED:
            note, status = _BLOCKED_NOTE, "blocked"
        else:
            # FAILED, TIMEOUT and INTERRUPTED all reach the room as one line.
            note, status = _FAILED_NOTE, "failed"
        if result.diagnostics:
            _logger.warning(
                "codex turn %s in %s ended %s: %s",
                turn.trigger_event_id,
                turn.room_id,
                result.status.value,
                result.diagnostics,
            )
        return TurnOutcome(
            observations=(self._note(turn, note),),
            native_session_id=native,
            status=status,
        )

    def _note(self, turn: TurnRequest, body: str) -> RoomObservation:
        """One room observation, built only from text this adapter chose to show.

        The id and timestamp are placeholders the outbox overwrites when it
        derives the durable ones from the trigger, exactly as the inert stand-in
        does, so the values here never reach a room or a wire.
        """

        return RoomObservation(
            observation_id=uuid4(),
            emitted_at=datetime.now(UTC),
            worker_name=self._worker_name,
            room_id=turn.room_id,
            kind="note",
            body=body,
        )

    def _environment(self, dirs: SessionDirs, path_value: str) -> dict[str, str]:
        """The only variables the child ever sees (H-13, minimal-non-secret form).

        An explicit allowlist, never a merge of ``os.environ``: SCM credentials
        and the control-plane token must not reach the CLI through inherited
        variables. ``SystemRoot`` (and ``windir``) are present because node/codex
        cannot start without them on Windows; ``CODEX_HOME`` points codex at the
        Bridge's own session state — never the operator's ``~/.codex`` — so a
        restart can still resume the thread; ``TMP``/``TEMP`` name the one
        writable scratch directory the restriction leaves it.

        ``PATH`` carries *only* the node and codex directories, so the npm
        launcher can find the interpreter it shells out to. It is not the
        operator's ``PATH`` and holds nothing else: adding it does not widen what
        the child may write — that boundary is the Low-integrity token, which is
        indifferent to ``PATH`` — and it names no secret and none of the
        operator's other ``PATH`` entries. A live probe confirmed these six keys
        are sufficient for codex to answer an ``initialize`` handshake.
        """

        environment: dict[str, str] = {}
        for key in ("SystemRoot", "windir"):
            value = os.environ.get(key)
            if value:
                environment[key] = value
        environment["CODEX_HOME"] = str(dirs.codex_home)
        environment["TMP"] = str(dirs.tmp)
        environment["TEMP"] = str(dirs.tmp)
        environment["PATH"] = path_value
        return environment

    async def close(self) -> None:
        """Release the session. Safe when nothing was ever spawned, and safe twice.

        The driver owns each turn's process and reaps it as the turn ends (or when
        a cancelled ``respond`` tears it down), and ``ensure_ready`` reaps its own
        probe, so there is no long-lived process for this method to end. It
        exists to satisfy the port's shutdown contract, not to cover a leak.
        """

        self._closed = True


def _executable_path(*binaries: str) -> str:
    """A ``PATH`` of just the directories the given executables live in.

    Absolute, de-duplicated, in argument order. It is the whole of the child's
    ``PATH``: nothing from the operator's own ``PATH`` reaches the CLI, and the
    only reason it exists is that codex's npm launcher shells out to ``node``.
    """

    directories: list[str] = []
    for binary in binaries:
        directory = str(Path(binary).resolve().parent)
        if directory not in directories:
            directories.append(directory)
    return os.pathsep.join(directories)


async def _first_nonempty_line(handle: ProcessHandle) -> bytes | None:
    async for line in handle.stdout_lines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def _safe_stderr(handle: ProcessHandle) -> str:
    try:
        return handle.stderr_tail().strip()
    except Exception:
        return ""
