"""ACP (Agent Client Protocol) driver: JSON-RPC 2.0 over newline-delimited stdio.

Wire sequence (docs/development/runner-driver-spec.md section 6):
``initialize`` -> ``session/new`` (or ``session/resume``) -> optional
``session/set_model`` -> ``session/prompt``. The ``session/prompt`` *response*
is the turn terminal; everything the agent says arrives meanwhile as
``session/update`` notifications, and the server may itself send requests back
(``session/request_permission``) that we must answer.

A single reader task owns stdout. Responses are routed to the waiting call by
id, notifications and server-initiated requests are handled inline. Every
message touches the idle watchdog, and every wait is bounded by it.
"""

import asyncio
import contextlib
import json
import re
from collections.abc import Mapping
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
    from repomesh_runner.profiles import AcpConfig, CliProfile

CLIENT_NAME = "repomesh-runner"
CLIENT_VERSION = "0.1.0"
SUCCESS_STOP_REASON = "end_turn"

_EXIT_GRACE_SECONDS = 10.0
_MAX_ORPHAN_RESPONSES = 64
_TOOL_TERMINAL_STATUSES = frozenset({"completed", "failed"})

# Provider-side failures are frequently reported by the CLI as a normal turn:
# the agent narrates "the API call failed" and the transport error only shows up
# on stderr. Sniffing for it keeps a broken run from being reported as success.
_STATUS_CODE = re.compile(r"\b(?:4\d\d|5\d\d)\b")
_TRANSPORT_HINT = re.compile(r"\b(?:http|https|api)\b", re.IGNORECASE)
_EXPLICIT_FAILURE = re.compile(r"api call failed|rate limit", re.IGNORECASE)


def detect_provider_error(text: str) -> str | None:
    """Return the first line that reads like a provider/transport failure.

    Matches either an explicit phrase ("API call failed", "rate limit") or an
    HTTP-looking status code on a line that also mentions http/api.
    """

    for line in text.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        if _EXPLICIT_FAILURE.search(candidate):
            return candidate
        if _STATUS_CODE.search(candidate) and _TRANSPORT_HINT.search(candidate):
            return candidate
    return None


class _Abort(Exception):
    """Internal control-flow signal carrying the terminal status to report."""

    def __init__(self, status: DriverResultStatus, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


class _RpcFailure(_Abort):
    """The peer answered a request with a JSON-RPC ``error`` object."""

    def __init__(self, detail: str) -> None:
        super().__init__(DriverResultStatus.FAILED, detail)


class AcpDriver:
    """ProtocolDriver for the ACP family (kimi)."""

    def __init__(self, process_factory: ProcessFactory) -> None:
        self._process_factory = process_factory

    @property
    def family(self) -> DriverFamily:
        return DriverFamily.ACP

    async def execute(
        self,
        request: DriverRequest,
        profile: "CliProfile",
        observer: DriverObserver,
    ) -> DriverResult:
        config = profile.acp
        if config is None:
            raise DriverError(f"profile {profile.id} is ACP family but carries no acp config")
        spec = SpawnSpec(
            executable=request.executable,
            arguments=tuple(profile.base_arguments),
            working_directory=request.workspace,
            environment=request.environment,
        )
        process = await self._process_factory.spawn(spec)
        return await _AcpRun(process, request, config, observer).run()


class _AcpRun:
    """One ACP conversation over one process."""

    def __init__(
        self,
        process: ProcessHandle,
        request: DriverRequest,
        config: "AcpConfig",
        observer: DriverObserver,
    ) -> None:
        self._process = process
        self._request = request
        self._config = config
        self._observer = observer
        self._watchdog = IdleWatchdog(request.idle_window_seconds, request.tool_window_seconds)
        self._pending: dict[int, asyncio.Future[Mapping[str, Any]]] = {}
        # Responses that arrived before their caller registered a waiter. A
        # conforming server cannot do this, but buffering keeps a fast peer (and
        # the scripted test fake) from stranding a call.
        self._orphans: dict[int, Mapping[str, Any]] = {}
        self._next_request_id = 0
        self._notes: list[str] = []
        self._full_text: list[str] = []
        self._deliverable: list[str] = []
        self._tool_call_count = 0
        self._wire_session_id: str | None = None
        self._native_session_id: str | None = None
        self._stream_ended = asyncio.Event()
        self._aborted = asyncio.Event()
        self._abort_error: _Abort | None = None
        self._reader: asyncio.Task[None] | None = None
        self._stream_waiter: asyncio.Task[bool] | None = None
        self._abort_waiter: asyncio.Task[bool] | None = None

    # ------------------------------------------------------------------ run

    async def run(self) -> DriverResult:
        self._reader = asyncio.ensure_future(self._read_loop())
        self._stream_waiter = asyncio.ensure_future(self._stream_ended.wait())
        self._abort_waiter = asyncio.ensure_future(self._aborted.wait())
        status = DriverResultStatus.SUCCEEDED
        try:
            try:
                if self._request.system_prompt:
                    self._notes.append("system prompt ignored: the ACP surface has no such field")
                await self._handshake()
                await self._start_session()
                await self._select_model()
                await self._run_prompt()
                await self._await_quiescence()
            except _Abort as abort:
                status = abort.status
                self._notes.append(abort.detail)
            except asyncio.CancelledError:
                # Cancellation outranks every other cause (spec section 3.3).
                status = DriverResultStatus.INTERRUPTED
                self._notes.append("run cancelled")
        finally:
            await self._shutdown(success=status is DriverResultStatus.SUCCEEDED)

        if status is DriverResultStatus.SUCCEEDED:
            status = self._promote_provider_error(status)
        return self._build_result(status)

    async def _handshake(self) -> None:
        await self._call(
            "initialize",
            {
                "protocolVersion": self._config.protocol_version,
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
                "clientCapabilities": {},
            },
        )

    async def _start_session(self) -> None:
        cwd = str(self._request.workspace)
        resume = self._request.resume_session_id
        if resume:
            response = await self._call(
                "session/resume",
                {"cwd": cwd, "sessionId": resume, "mcpServers": []},
            )
            reported = _result_of(response).get("sessionId")
            if isinstance(reported, str) and reported.strip() and reported != resume:
                self._notes.append(f"resume replaced session id: {resume} -> {reported}")
                self._adopt_session(reported)
            else:
                self._adopt_session(resume)
            return

        response = await self._call("session/new", {"cwd": cwd, "mcpServers": []})
        reported = _result_of(response).get("sessionId")
        if not isinstance(reported, str) or not reported.strip():
            raise _Abort(DriverResultStatus.FAILED, "session/new returned no session id")
        self._adopt_session(reported)

    def _adopt_session(self, value: str) -> None:
        # The raw value goes back on the wire; only the reported id is
        # sanitized, because that one is later interpolated into command lines.
        self._wire_session_id = value
        self._native_session_id = sanitize_session_id(value)
        if self._native_session_id is None:
            self._notes.append("session id failed sanitization; no session captured")
        self._emit(
            DriverEventKind.SESSION_STARTED,
            {"native_session_id": self._native_session_id},
        )

    async def _select_model(self) -> None:
        model = self._request.model
        if not model:
            return
        try:
            await self._call(
                "session/set_model",
                {"sessionId": self._wire_session_id, "modelId": model},
            )
        except _RpcFailure as failure:
            raise _Abort(
                DriverResultStatus.FAILED,
                f"model selection failed ({failure.detail}); aborting instead of falling back "
                "to the default model, which would misreport what actually executed",
            ) from failure

    async def _run_prompt(self) -> None:
        response = await self._call(
            "session/prompt",
            {
                "sessionId": self._wire_session_id,
                "prompt": [{"type": "text", "text": self._request.prompt}],
            },
        )
        stop_reason = _result_of(response).get("stopReason")
        if stop_reason != SUCCESS_STOP_REASON:
            raise _Abort(
                DriverResultStatus.FAILED,
                f"session/prompt ended with stopReason={stop_reason!r} "
                f"(expected {SUCCESS_STOP_REASON!r})",
            )

    async def _await_quiescence(self) -> None:
        """Give trailing ``session/update`` notifications a moment to land."""

        seconds = max(0.0, float(self._config.quiescence_seconds))
        if seconds <= 0 or self._stream_waiter is None:
            return
        await asyncio.wait({self._stream_waiter}, timeout=seconds)

    # ------------------------------------------------------------------ rpc

    async def _call(self, method: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        self._next_request_id += 1
        request_id = self._next_request_id
        future: asyncio.Future[Mapping[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        except Exception as exc:
            self._pending.pop(request_id, None)
            raise _Abort(
                DriverResultStatus.FAILED,
                f"failed to write {method} request to stdin: {exc}",
            ) from exc

        buffered = self._orphans.pop(request_id, None)
        if buffered is not None:
            self._pending.pop(request_id, None)
            response: Mapping[str, Any] = buffered
        else:
            response = await self._await_response(future, request_id, method)

        error = response.get("error")
        if error is not None:
            detail = _describe_rpc_error(error)
            raise _RpcFailure(f"{method} returned a JSON-RPC error: {detail}")
        return response

    async def _await_response(
        self,
        future: asyncio.Future[Mapping[str, Any]],
        request_id: int,
        method: str,
    ) -> Mapping[str, Any]:
        stream_waiter = self._stream_waiter
        abort_waiter = self._abort_waiter
        if stream_waiter is None or abort_waiter is None:  # pragma: no cover - run() sets both
            raise _Abort(DriverResultStatus.FAILED, "reader tasks were never started")
        waiters: set[asyncio.Future[Any]] = {future, stream_waiter, abort_waiter}
        while True:
            remaining = self._watchdog.remaining()
            if remaining <= 0:
                self._pending.pop(request_id, None)
                raise _Abort(
                    DriverResultStatus.TIMEOUT,
                    f"idle timeout after {self._watchdog.window_seconds:g}s "
                    f"waiting for the {method} response",
                )
            done, _ = await asyncio.wait(
                waiters, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
            )
            if abort_waiter in done and self._abort_error is not None:
                raise self._abort_error
            if future.done():
                return future.result()
            if stream_waiter in done:
                self._pending.pop(request_id, None)
                raise await self._stream_ended_abort()
            # Otherwise the read timed out; re-check the watchdog, which may have
            # been refreshed by unrelated traffic while we waited.

    async def _stream_ended_abort(self) -> _Abort:
        exit_code: int | None = None
        with contextlib.suppress(Exception):
            exit_code = await asyncio.wait_for(self._process.wait(), timeout=_EXIT_GRACE_SECONDS)
        detail = f"stream ended without terminal result (exit code {exit_code})"
        tail = self._stderr_tail()
        if tail:
            detail = f"{detail}; stderr tail: {tail}"
        return _Abort(DriverResultStatus.FAILED, detail)

    def _send(self, message: Mapping[str, Any]) -> None:
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        self._process.write_stdin(f"{payload}\n".encode())

    # --------------------------------------------------------------- reader

    async def _read_loop(self) -> None:
        try:
            async for raw in self._process.stdout_lines():
                self._watchdog.touch()
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    self._emit(DriverEventKind.LOG, {"unparsed_stdout": line[:2000]})
                    continue
                if isinstance(message, Mapping):
                    self._dispatch(message)
        finally:
            self._stream_ended.set()

    def _dispatch(self, message: Mapping[str, Any]) -> None:
        if "method" in message:
            if message.get("id") is not None:
                self._handle_server_request(message)
            else:
                self._handle_notification(message)
            return
        request_id = message.get("id")
        if not isinstance(request_id, int):
            self._emit(DriverEventKind.LOG, {"unroutable_message": dict(message)})
            return
        future = self._pending.pop(request_id, None)
        if future is not None and not future.done():
            future.set_result(message)
            return
        if len(self._orphans) >= _MAX_ORPHAN_RESPONSES:
            self._orphans.pop(next(iter(self._orphans)))
        self._orphans[request_id] = message

    # -------------------------------------------------------- notifications

    def _handle_notification(self, message: Mapping[str, Any]) -> None:
        method = message.get("method")
        params = _mapping(message.get("params"))
        if method != "session/update":
            self._emit(DriverEventKind.LOG, {"method": method, "params": dict(params)})
            return
        update = _mapping(params.get("update"))
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            self._on_message_chunk(update)
        elif kind == "agent_thought_chunk":
            text = _extract_text(update.get("content"))
            if text:
                self._emit(DriverEventKind.THINKING, {"text": text})
        elif kind == "tool_call":
            self._on_tool_call(update)
        elif kind == "tool_call_update":
            self._on_tool_call_update(update)
        else:
            self._emit(DriverEventKind.LOG, {"session_update": kind, "update": dict(update)})

    def _on_message_chunk(self, update: Mapping[str, Any]) -> None:
        text = _extract_text(update.get("content"))
        if not text:
            return
        self._full_text.append(text)
        # Deliverable heuristic: ACP has no result marker, so the summary is the
        # text emitted after the last tool call — narration that precedes tool
        # work is progress reporting, not the answer.
        self._deliverable.append(text)
        self._emit(DriverEventKind.TEXT, {"text": text})

    def _on_tool_call(self, update: Mapping[str, Any]) -> None:
        self._tool_call_count += 1
        self._deliverable.clear()
        self._watchdog.tool_started()
        self._emit(
            DriverEventKind.TOOL_USE,
            {
                "call_id": update.get("toolCallId"),
                "tool_name": _tool_name(update),
                "title": update.get("title"),
                "kind": update.get("kind"),
                "input": update.get("rawInput"),
            },
        )

    def _on_tool_call_update(self, update: Mapping[str, Any]) -> None:
        status = update.get("status")
        if isinstance(status, str) and status in _TOOL_TERMINAL_STATUSES:
            self._watchdog.tool_finished()
        self._emit(
            DriverEventKind.TOOL_RESULT,
            {
                "call_id": update.get("toolCallId"),
                "status": status,
                "output": update.get("content"),
            },
        )

    # ------------------------------------------------------ server requests

    def _handle_server_request(self, message: Mapping[str, Any]) -> None:
        method = message.get("method")
        request_id = message.get("id")
        if method == "session/request_permission":
            self._answer_permission(request_id, _mapping(message.get("params")))
            return
        self._respond(
            request_id,
            error={"code": -32601, "message": f"unsupported method: {method}"},
        )

    def _answer_permission(self, request_id: Any, params: Mapping[str, Any]) -> None:
        tool_call = _mapping(params.get("toolCall"))
        tool_name = _tool_name(tool_call)
        raw_input = tool_call.get("rawInput")
        tool_input = raw_input if isinstance(raw_input, Mapping) else {}
        try:
            decision = self._request.permission_policy.decide(tool_name, tool_input)
        except Exception as exc:
            # A policy that cannot answer must not open the gate.
            decision = PermissionDecision.DENY
            self._notes.append(f"permission policy raised for {tool_name}: {exc}")

        options = params.get("options")
        option_id: str | None = None
        if decision is PermissionDecision.ALLOW:
            option_id = _select_option(options, "allow_once", "allow")
        elif decision is PermissionDecision.DENY:
            option_id = _select_option(options, "reject_once", "reject")

        self._emit(
            DriverEventKind.PERMISSION_REQUEST,
            {
                "tool_name": tool_name,
                "decision": decision.value,
                "call_id": tool_call.get("toolCallId"),
                "option_id": option_id,
            },
        )

        if option_id is None:
            outcome: dict[str, Any] = {"outcome": "cancelled"}
            if decision is not PermissionDecision.ESCALATE:
                self._notes.append(
                    f"no {decision.value} option offered for {tool_name}; cancelled the request"
                )
        else:
            outcome = {"outcome": "selected", "optionId": option_id}
        self._respond(request_id, result={"outcome": outcome})

        if decision is PermissionDecision.ESCALATE:
            self._abort(
                _Abort(
                    DriverResultStatus.INPUT_REQUIRED,
                    f"permission for {tool_name} cannot be decided locally; run needs input",
                )
            )

    def _respond(
        self,
        request_id: Any,
        *,
        result: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
        if error is not None:
            message["error"] = error
        else:
            message["result"] = result if result is not None else {}
        try:
            self._send(message)
        except Exception as exc:
            self._notes.append(f"failed to answer server request {request_id}: {exc}")

    def _abort(self, abort: _Abort) -> None:
        if self._abort_error is None:
            self._abort_error = abort
        self._aborted.set()

    # ------------------------------------------------------- finalization

    async def _shutdown(self, *, success: bool) -> None:
        if success:
            with contextlib.suppress(Exception):
                self._process.close_stdin()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=_EXIT_GRACE_SECONDS)
            except Exception:
                with contextlib.suppress(Exception):
                    await self._process.terminate()
        else:
            with contextlib.suppress(Exception):
                await self._process.terminate()
        for task in (self._reader, self._stream_waiter, self._abort_waiter):
            if task is None or task.done():
                continue
            task.cancel()
            with contextlib.suppress(BaseException):
                await task

    def _promote_provider_error(self, status: DriverResultStatus) -> DriverResultStatus:
        offender = detect_provider_error(self._stderr_tail())
        source = "stderr"
        if offender is None:
            offender = detect_provider_error("".join(self._full_text))
            source = "agent output"
        if offender is None:
            return status
        self._notes.append(
            f"provider failure detected in {source} despite a nominally successful turn: "
            f"{offender}"
        )
        return DriverResultStatus.FAILED

    def _build_result(self, status: DriverResultStatus) -> DriverResult:
        summary = ""
        if status is DriverResultStatus.SUCCEEDED:
            summary = "".join(self._deliverable).strip()
        diagnostics = "\n".join(note for note in self._notes if note)
        if status is not DriverResultStatus.SUCCEEDED:
            tail = self._stderr_tail()
            if tail and tail not in diagnostics:
                diagnostics = f"{diagnostics}\nstderr tail: {tail}".strip()
        return DriverResult(
            status=status,
            summary=summary,
            native_session_id=self._native_session_id,
            diagnostics=diagnostics,
            tool_call_count=self._tool_call_count,
        )

    def _stderr_tail(self) -> str:
        try:
            return self._process.stderr_tail().strip()
        except Exception:
            return ""

    def _emit(self, kind: DriverEventKind, payload: Mapping[str, Any]) -> None:
        # The contract says observers never raise; a misbehaving one still must
        # not take the run down.
        with contextlib.suppress(Exception):
            self._observer(DriverEvent(kind=kind, payload=dict(payload)))


# ---------------------------------------------------------------- helpers


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _result_of(response: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(response.get("result"))


def _tool_name(payload: Mapping[str, Any]) -> str:
    for key in ("title", "kind", "toolName"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return "unknown"


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, Mapping):
        text = content.get("text")
        return text if isinstance(text, str) else ""
    if isinstance(content, list):
        return "".join(_extract_text(block) for block in content)
    return ""


def _select_option(options: Any, preferred_kind: str, kind_prefix: str) -> str | None:
    """Pick the least-privileged option matching a decision."""

    if not isinstance(options, list):
        return None
    candidates = [option for option in options if isinstance(option, Mapping)]
    for option in candidates:
        if option.get("kind") == preferred_kind and isinstance(option.get("optionId"), str):
            return str(option["optionId"])
    for option in candidates:
        kind = option.get("kind")
        if (
            isinstance(kind, str)
            and kind.startswith(kind_prefix)
            and isinstance(option.get("optionId"), str)
        ):
            return str(option["optionId"])
    return None


def _describe_rpc_error(error: Any) -> str:
    if isinstance(error, Mapping):
        message = error.get("message", "unknown error")
        code = error.get("code")
        data = error.get("data")
        detail = f"{message} (code {code})"
        if data is not None:
            detail = f"{detail} data={data!r}"
        return detail
    return str(error)
