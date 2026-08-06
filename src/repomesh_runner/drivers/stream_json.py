"""stream-json protocol driver (claude-code family).

Speaks the NDJSON dialect of ``claude -p --output-format stream-json
--input-format stream-json``: one JSON object per line on stdout, one user
frame written to stdin, and ``control_request`` / ``control_response``
round-trips for permission prompts.

Argv note — the driver never reads ``profile.permission_arguments`` itself.
``DriverRequest`` is a frozen contract and does not carry the permission mode,
so the executor resolves the mode against the profile and hands the result down
as ``extra_arguments``; :func:`build_arguments` splices those in right after
``base_arguments``. Whatever the mode maps to, the CLI must stay in a mode that
asks before each tool via ``control_request``: that channel is where the
request's ``PermissionPolicy`` enforces the platform's deny rules, and a CLI-side
bypass flag would silence it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from repomesh_runner.drivers.base import (
    DriverError,
    DriverEvent,
    DriverEventKind,
    DriverFamily,
    DriverObserver,
    DriverRequest,
    DriverResult,
    DriverResultStatus,
    PermissionDecision,
    sanitize_session_id,
)
from repomesh_runner.drivers.supervision import (
    IdleWatchdog,
    ProcessFactory,
    ProcessHandle,
    SpawnSpec,
)

if TYPE_CHECKING:
    # Deferred: profiles imports drivers.base, so a runtime import here closes
    # a cycle whenever profiles is imported first.
    from repomesh_runner.profiles import CliProfile

DENY_MESSAGE = "denied by RepoMesh policy"
MISSING_TERMINAL_DIAGNOSTIC = "stream ended without terminal result"

_EXIT_WAIT_SECONDS = 5.0
_MAX_LOG_CHARS = 500
_TRANSCRIPT_KEYS = ("transcript_path", "transcript")


def _as_mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_blocks(value: object) -> Sequence[Mapping[str, Any]]:
    if not isinstance(value, list):
        return ()
    return tuple(block for block in value if isinstance(block, Mapping))


def build_arguments(request: DriverRequest, profile: CliProfile) -> tuple[str, ...]:
    """Assemble argv after the binary, prompt excluded.

    ``resume_flag is None`` means the surface has no resume support, so a
    requested resume id is dropped instead of guessed at. The id is sanitized
    again here: it reaches an argv position where a leading dash would read as
    a flag.
    """

    arguments: list[str] = list(profile.base_arguments)
    arguments += request.extra_arguments
    if request.model and profile.model_flag:
        arguments += [profile.model_flag, request.model]
    if request.system_prompt and profile.system_prompt_flag:
        arguments += [profile.system_prompt_flag, request.system_prompt]
    if request.resume_session_id and profile.resume_flag:
        resume_id = sanitize_session_id(request.resume_session_id)
        if resume_id is not None:
            arguments += [profile.resume_flag, resume_id]
    return tuple(arguments)


class StreamJsonDriver:
    """Protocol driver for :data:`DriverFamily.STREAM_JSON` profiles."""

    def __init__(self, process_factory: ProcessFactory) -> None:
        self._process_factory = process_factory

    @property
    def family(self) -> DriverFamily:
        return DriverFamily.STREAM_JSON

    async def execute(
        self,
        request: DriverRequest,
        profile: CliProfile,
        observer: DriverObserver,
    ) -> DriverResult:
        if profile.family is not DriverFamily.STREAM_JSON:
            raise DriverError(f"{profile.id}: not a stream-json profile")
        config = profile.stream_json
        if config is None:
            raise DriverError(f"{profile.id}: stream_json config is missing")

        arguments = build_arguments(request, profile)
        if not config.prompt_via_stdin:
            arguments = (*arguments, request.prompt)

        handle = await self._process_factory.spawn(
            SpawnSpec(
                executable=request.executable,
                arguments=arguments,
                working_directory=request.workspace,
                environment=request.environment,
            )
        )
        run = _Run(request, observer, handle)
        writer: asyncio.Task[None] | None = None
        try:
            if config.prompt_via_stdin:
                # Independent task: the prompt must not be written from the
                # same step that drains stdout (the claude banner deadlock).
                writer = asyncio.create_task(run.write_prompt())
                await asyncio.sleep(0)
            return await run.read_until_terminal()
        except asyncio.CancelledError:
            # Cancellation is reported as a structured INTERRUPTED result
            # instead of being re-raised: the executor needs one terminal state
            # per run, and INTERRUPTED already carries "no deliverable" through
            # the DriverResult invariant. Callers that need the raised form can
            # observe the status.
            return run.interrupted()
        finally:
            if writer is not None:
                writer.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await writer
            await run.shutdown()


class _Run:
    """Mutable state of a single stream-json execution."""

    def __init__(
        self,
        request: DriverRequest,
        observer: DriverObserver,
        handle: ProcessHandle,
    ) -> None:
        self._request = request
        self._observer = observer
        self._handle = handle
        self._watchdog = IdleWatchdog(
            request.idle_window_seconds,
            request.tool_window_seconds,
        )
        self._session_id: str | None = None
        self._transcript_path: str | None = None
        self._tool_call_count = 0
        self._candidate_summary = ""

    # -- process I/O -----------------------------------------------------

    async def write_prompt(self) -> None:
        self._write_frame(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": self._request.prompt}],
                },
            }
        )
        # stdin stays open on purpose: control_response frames travel back up
        # the same pipe, and an early close ends the turn mid-flight.

    def _write_frame(self, frame: Mapping[str, object]) -> None:
        self._handle.write_stdin(json.dumps(frame).encode() + b"\n")

    async def read_until_terminal(self) -> DriverResult:
        lines = self._handle.stdout_lines()
        while True:
            try:
                line = await asyncio.wait_for(
                    anext(lines, None),
                    timeout=self._watchdog.remaining(),
                )
            except TimeoutError:
                # This read is the only activity source in the loop. If its exact remaining
                # budget elapsed, no concurrent frame can have refreshed the watchdog.
                return self.timed_out()
            if line is None:
                # ``wait_for`` cancels the pending async-generator read on timeout. Some
                # process adapters surface that cancellation as EOF on the following read,
                # so re-check the watchdog before classifying it as a missing terminal frame.
                if self._watchdog.expired():
                    return self.timed_out()
                break
            self._watchdog.touch()
            terminal = self._consume(line)
            if terminal is not None:
                return terminal
        return await self._missing_terminal()

    async def shutdown(self) -> None:
        with contextlib.suppress(Exception):
            self._handle.close_stdin()
        # Unconditional terminate: after its terminal frame the CLI is still
        # blocked on stdin, so nothing exits on its own. terminate() is a no-op
        # for an already-dead process.
        with contextlib.suppress(Exception):
            await self._handle.terminate()

    # -- frame dispatch --------------------------------------------------

    def _consume(self, line: bytes) -> DriverResult | None:
        text = line.decode(errors="replace").strip()
        if not text:
            return None
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            self._log("non-json stdout line", text)
            return None
        if not isinstance(event, Mapping):
            self._log("unexpected stdout payload", text)
            return None

        match event.get("type"):
            case "system":
                self._on_system(event)
            case "assistant":
                self._on_assistant(event)
            case "user":
                self._on_user(event)
            case "control_request":
                return self._on_control_request(event)
            case "result":
                return self._on_result(event)
            case _:
                self._log("unhandled stream-json event", text)
        return None

    def _on_system(self, event: Mapping[str, Any]) -> None:
        for key in _TRANSCRIPT_KEYS:
            value = event.get(key)
            if isinstance(value, str) and value.strip():
                self._transcript_path = value.strip()
                break
        session_id = sanitize_session_id(event.get("session_id"))
        if session_id is None or self._session_id is not None:
            return
        self._session_id = session_id
        self._emit(
            DriverEventKind.SESSION_STARTED,
            {"native_session_id": session_id, "transcript_path": self._transcript_path},
        )

    def _on_assistant(self, event: Mapping[str, Any]) -> None:
        blocks = _as_blocks(_as_mapping(event.get("message")).get("content"))
        texts: list[str] = []
        calls_a_tool = False
        for block in blocks:
            match block.get("type"):
                case "text":
                    text = block.get("text")
                    if isinstance(text, str) and text.strip():
                        texts.append(text.strip())
                        self._emit(DriverEventKind.TEXT, {"text": text})
                case "thinking":
                    thinking = block.get("thinking") or block.get("text") or ""
                    self._emit(DriverEventKind.THINKING, {"text": str(thinking)})
                case "tool_use":
                    calls_a_tool = True
                    self._tool_call_count += 1
                    self._watchdog.tool_started()
                    self._emit(
                        DriverEventKind.TOOL_USE,
                        {
                            "call_id": str(block.get("id") or ""),
                            "tool_name": str(block.get("name") or ""),
                            "input": _as_mapping(block.get("input")),
                        },
                    )
        # A turn that calls a tool is narrating what it is about to do; only a
        # turn that ends without tool calls can stand in as the deliverable.
        if texts and not calls_a_tool:
            self._candidate_summary = "\n".join(texts)

    def _on_user(self, event: Mapping[str, Any]) -> None:
        for block in _as_blocks(_as_mapping(event.get("message")).get("content")):
            if block.get("type") != "tool_result":
                continue
            self._watchdog.tool_finished()
            self._emit(
                DriverEventKind.TOOL_RESULT,
                {
                    "call_id": str(block.get("tool_use_id") or ""),
                    "output": block.get("content"),
                },
            )

    def _on_control_request(self, event: Mapping[str, Any]) -> DriverResult | None:
        payload = _as_mapping(event.get("request"))
        if payload.get("subtype") != "can_use_tool":
            self._log("unhandled control_request", str(payload.get("subtype")))
            return None
        tool_name = str(payload.get("tool_name") or "")
        tool_input = _as_mapping(payload.get("input"))
        decision = self._decide(tool_name, tool_input)
        self._emit(
            DriverEventKind.PERMISSION_REQUEST,
            {"tool_name": tool_name, "decision": decision.value},
        )
        if decision is PermissionDecision.ESCALATE:
            return self.result(
                DriverResultStatus.INPUT_REQUIRED,
                diagnostics=self._with_stderr(
                    f"permission escalation required for tool {tool_name!r}: "
                    "no local policy decision"
                ),
            )
        response: dict[str, object] = (
            {"behavior": "allow", "updatedInput": dict(tool_input)}
            if decision is PermissionDecision.ALLOW
            else {"behavior": "deny", "message": DENY_MESSAGE}
        )
        self._write_frame(
            {
                "type": "control_response",
                "response": {
                    "subtype": "success",
                    "request_id": event.get("request_id"),
                    "response": response,
                },
            }
        )
        return None

    def _on_result(self, event: Mapping[str, Any]) -> DriverResult:
        session_id = sanitize_session_id(event.get("session_id"))
        succeeded = event.get("is_error") is False
        if session_id is not None and (succeeded or not self._echoes_unopened_resume(session_id)):
            self._session_id = session_id
        raw_text = event.get("result")
        text = raw_text.strip() if isinstance(raw_text, str) else ""
        if "is_error" not in event:
            # Fail-closed: an undeclared outcome is not a terminal success.
            return self.result(
                DriverResultStatus.FAILED,
                diagnostics=self._with_stderr("terminal result frame did not declare is_error"),
            )
        if event["is_error"]:
            detail = text or str(event.get("error") or "provider reported an error")
            subtype = event.get("subtype")
            if isinstance(subtype, str) and subtype:
                detail = f"{subtype}: {detail}"
            return self.result(DriverResultStatus.FAILED, diagnostics=self._with_stderr(detail))
        return self.result(DriverResultStatus.SUCCEEDED, summary=text or self._candidate_summary)

    def _echoes_unopened_resume(self, session_id: str) -> bool:
        """True when the id is only the resume id we asked for, unconfirmed.

        A session is confirmed to exist only when the CLI announces it on the
        init/system frame (:meth:`_on_system`, the sole other writer of
        ``_session_id``). When a ``--resume`` of an id the CLI never opened
        fails, its error result frame still echoes that id back, and reporting
        it would tell the platform "the session is alive, retry with it" for a
        session that will never resume. The rule is structural rather than a
        match on the vendor's error text, which is not a contract.
        """

        if self._session_id is not None:
            return False
        requested = sanitize_session_id(self._request.resume_session_id)
        return requested is not None and session_id == requested

    def _decide(
        self,
        tool_name: str,
        tool_input: Mapping[str, object],
    ) -> PermissionDecision:
        try:
            return self._request.permission_policy.decide(tool_name, tool_input)
        except Exception:
            # A policy that cannot answer must never read as an approval.
            return PermissionDecision.ESCALATE

    # -- results ---------------------------------------------------------

    def result(
        self,
        status: DriverResultStatus,
        *,
        summary: str = "",
        diagnostics: str = "",
    ) -> DriverResult:
        return DriverResult(
            status=status,
            summary=summary if status is DriverResultStatus.SUCCEEDED else "",
            native_session_id=self._session_id,
            transcript_path=self._transcript_path,
            diagnostics=diagnostics,
            tool_call_count=self._tool_call_count,
        )

    def interrupted(self) -> DriverResult:
        return self.result(
            DriverResultStatus.INTERRUPTED,
            diagnostics=self._with_stderr("run cancelled before a terminal result"),
        )

    def timed_out(self) -> DriverResult:
        window = self._watchdog.window_seconds
        return self.result(
            DriverResultStatus.TIMEOUT,
            diagnostics=self._with_stderr(
                f"idle watchdog fired after {window:g}s without protocol activity"
            ),
        )

    async def _missing_terminal(self) -> DriverResult:
        exit_code = await self._exit_code()
        suffix = "unavailable" if exit_code is None else str(exit_code)
        return self.result(
            DriverResultStatus.FAILED,
            diagnostics=self._with_stderr(f"{MISSING_TERMINAL_DIAGNOSTIC} (exit code {suffix})"),
        )

    async def _exit_code(self) -> int | None:
        try:
            return await asyncio.wait_for(self._handle.wait(), timeout=_EXIT_WAIT_SECONDS)
        except TimeoutError:
            return None

    def _with_stderr(self, detail: str) -> str:
        tail = ""
        with contextlib.suppress(Exception):
            tail = self._handle.stderr_tail().strip()
        return f"{detail}\nstderr tail:\n{tail}" if tail else detail

    # -- observation -----------------------------------------------------

    def _emit(
        self,
        kind: DriverEventKind,
        payload: Mapping[str, object] | None = None,
    ) -> None:
        with contextlib.suppress(Exception):
            self._observer(DriverEvent(kind=kind, payload=dict(payload or {})))

    def _log(self, reason: str, detail: str) -> None:
        self._emit(
            DriverEventKind.LOG,
            {"reason": reason, "message": detail[:_MAX_LOG_CHARS]},
        )
