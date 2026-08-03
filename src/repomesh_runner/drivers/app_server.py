"""app-server driver (codex): JSON-RPC over newline-delimited stdio.

Wire behaviour was verified live against codex-cli 0.145.0; the shapes below are
transcribed from that session, not inferred (see
docs/development/runner-driver-spec.md section 6b).

Two things separate this family from ACP:

* codex answers with bare ``{"id": N, "result": {...}}`` frames — the
  ``jsonrpc`` member is absent, and error frames arrive as
  ``{"error": {...}, "id": N}``. Responses are therefore routed by id alone and
  the member is never required on the way in. Outgoing frames keep
  ``"jsonrpc": "2.0"`` (verified accepted, and it is what the standard asks of a
  client).
* the ``turn/start`` *response* is not terminal: it returns immediately with
  ``turn.status == "inProgress"``. The turn ends with a ``turn/completed``
  notification, so the driver ignores the response as an outcome, waits for the
  notification, and treats a stream that ends first as a failure. The idle
  watchdog is the only bound on that wait.

Sequence: ``initialize`` -> ``initialized`` (notification) -> ``thread/start``
(or ``thread/resume``) -> ``turn/start`` -> wait for ``turn/completed``.
"""

import asyncio
import contextlib
import json
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
    from repomesh_runner.profiles import AppServerConfig, CliProfile

CLIENT_NAME = "repomesh-runner"
CLIENT_VERSION = "0.1.0"
SUCCESS_TURN_STATUS = "completed"

# codex marks narration emitted while it works as ``phase: "commentary"``; the
# answer itself arrives as ``phase: "final_answer"``.
COMMENTARY_PHASE = "commentary"

_EXIT_GRACE_SECONDS = 10.0
_MAX_ORPHAN_RESPONSES = 64
# Item types that represent real tool work: they drive tool_call_count and the
# watchdog's long window. userMessage/agentMessage/reasoning are conversation.
_TOOL_ITEM_TYPES = frozenset({"commandExecution", "fileChange"})
_APPROVAL_METHOD_HINTS = ("approval", "permission")


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


class AppServerDriver:
    """ProtocolDriver for the app-server family (codex)."""

    def __init__(self, process_factory: ProcessFactory) -> None:
        self._process_factory = process_factory

    @property
    def family(self) -> DriverFamily:
        return DriverFamily.APP_SERVER

    async def execute(
        self,
        request: DriverRequest,
        profile: "CliProfile",
        observer: DriverObserver,
    ) -> DriverResult:
        config = profile.app_server
        if config is None:
            raise DriverError(
                f"profile {profile.id} is app-server family but carries no app_server config"
            )
        spec = SpawnSpec(
            executable=request.executable,
            arguments=(*profile.base_arguments, *request.extra_arguments),
            working_directory=request.workspace,
            environment=request.environment,
        )
        process = await self._process_factory.spawn(spec)
        return await _AppServerRun(process, request, config, observer).run()


class _AppServerRun:
    """One app-server thread + turn over one process."""

    def __init__(
        self,
        process: ProcessHandle,
        request: DriverRequest,
        config: "AppServerConfig",
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
        self._tool_call_count = 0
        self._wire_thread_id: str | None = None
        self._native_session_id: str | None = None
        self._transcript_path: str | None = None
        # Streaming agent text, keyed by item id, plus every completed
        # agentMessage as (phase, text) in arrival order.
        self._deltas: dict[str, list[str]] = {}
        self._messages: list[tuple[str, str]] = []
        self._final_diff: str | None = None
        self._terminal: tuple[DriverResultStatus, str] | None = None
        self._turn_finished = asyncio.Event()
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
                    self._notes.append(
                        "system prompt ignored: the app-server surface has no such field"
                    )
                await self._handshake()
                await self._start_thread()
                await self._run_turn()
                await self._await_quiescence()
            except _Abort as abort:
                status = abort.status
                if abort.detail:
                    self._notes.append(abort.detail)
            except asyncio.CancelledError:
                # Cancellation outranks every other cause (spec section 3.3).
                status = DriverResultStatus.INTERRUPTED
                self._notes.append("run cancelled")
        finally:
            await self._shutdown(success=status is DriverResultStatus.SUCCEEDED)
        return self._build_result(status)

    async def _handshake(self) -> None:
        await self._call(
            "initialize",
            {"clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION}},
        )
        # codex expects the client to acknowledge the handshake before it will
        # accept thread traffic. No response comes back for this one.
        self._notify("initialized", {})

    async def _start_thread(self) -> None:
        params: dict[str, Any] = {"cwd": str(self._request.workspace)}
        if self._request.model:
            params["model"] = self._request.model
        resume = self._request.resume_session_id
        method = "thread/start"
        if resume:
            method = "thread/resume"
            params["threadId"] = resume
        result = _result_of(await self._call(method, params))
        self._adopt_thread(_mapping(result.get("thread")), source=method)
        if self._wire_thread_id is None:
            raise _Abort(DriverResultStatus.FAILED, f"{method} returned no thread id")
        self._verify_model(result)

    def _adopt_thread(self, thread: Mapping[str, Any], *, source: str) -> None:
        """Pin the thread id and rollout path reported by codex.

        Both ``thread/start``'s result and the ``thread/started`` notification
        carry the same thread object, and either may land first, so adoption is
        idempotent and SESSION_STARTED is emitted once.
        """

        path = thread.get("path")
        if isinstance(path, str) and path.strip() and self._transcript_path is None:
            # Reported directly by codex, never derived from the id.
            self._transcript_path = path.strip()
        thread_id = thread.get("id")
        if not isinstance(thread_id, str) or not thread_id.strip():
            return
        if self._wire_thread_id is not None:
            if thread_id != self._wire_thread_id:
                self._notes.append(
                    f"{source} reported thread id {thread_id} after {self._wire_thread_id}; "
                    "keeping the first"
                )
            return
        self._wire_thread_id = thread_id
        self._native_session_id = sanitize_session_id(thread_id)
        if self._native_session_id is None:
            self._notes.append("thread id failed sanitization; no session captured")
        self._emit(
            DriverEventKind.SESSION_STARTED,
            {
                "native_session_id": self._native_session_id,
                "transcript_path": self._transcript_path,
            },
        )

    def _verify_model(self, result: Mapping[str, Any]) -> None:
        """Fail rather than run silently on a model we did not ask for.

        codex accepts an unknown ``model`` on ``thread/start`` without
        complaint, so the thread's reported model is the only honest check.
        """

        requested = self._request.model
        if not requested:
            return
        reported = result.get("model")
        if isinstance(reported, str) and reported and reported != requested:
            raise _Abort(
                DriverResultStatus.FAILED,
                f"requested model {requested!r} but the thread reports {reported!r}; "
                "aborting instead of running on a substituted model, which would "
                "misreport what actually executed",
            )

    async def _run_turn(self) -> None:
        response = await self._call(
            "turn/start",
            {
                "threadId": self._wire_thread_id,
                "input": [{"type": "text", "text": self._request.prompt}],
            },
        )
        turn = _mapping(_result_of(response).get("turn"))
        # Acknowledgement only: status is "inProgress" here. Treating it as an
        # outcome would report success for a turn that never ran.
        self._emit(
            DriverEventKind.LOG,
            {"turn_accepted": turn.get("id"), "status": turn.get("status")},
        )
        await self._await_turn_terminal()
        status, detail = self._terminal or (
            DriverResultStatus.FAILED,
            "turn ended without a recorded outcome",
        )
        if status is not DriverResultStatus.SUCCEEDED:
            raise _Abort(status, detail)
        if detail:
            self._notes.append(detail)

    async def _await_turn_terminal(self) -> None:
        waiter = asyncio.ensure_future(self._turn_finished.wait())
        try:
            await self._wait_for(waiter, "the turn/completed notification")
        finally:
            waiter.cancel()
            with contextlib.suppress(BaseException):
                await waiter

    async def _await_quiescence(self) -> None:
        """Give trailing notifications (final diff, token usage) a moment."""

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
            try:
                await self._wait_for(future, f"the {method} response")
            finally:
                self._pending.pop(request_id, None)
            response = future.result()

        error = response.get("error")
        if error is not None:
            raise _RpcFailure(
                f"{method} returned a JSON-RPC error: {_describe_rpc_error(error)}"
            )
        return response

    async def _wait_for(self, target: asyncio.Future[Any], description: str) -> None:
        """Wait for ``target``, bounded by the watchdog, stream end and aborts."""

        stream_waiter = self._stream_waiter
        abort_waiter = self._abort_waiter
        if stream_waiter is None or abort_waiter is None:  # pragma: no cover - run() sets both
            raise _Abort(DriverResultStatus.FAILED, "reader tasks were never started")
        waiters: set[asyncio.Future[Any]] = {target, stream_waiter, abort_waiter}
        while True:
            if target.done():
                return
            remaining = self._watchdog.remaining()
            if remaining <= 0:
                raise _Abort(
                    DriverResultStatus.TIMEOUT,
                    f"idle timeout after {self._watchdog.window_seconds:g}s "
                    f"waiting for {description}",
                )
            done, _ = await asyncio.wait(
                waiters, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
            )
            if abort_waiter in done and self._abort_error is not None:
                raise self._abort_error
            if target.done():
                return
            if stream_waiter in done:
                raise await self._stream_ended_abort(description)
            # Otherwise the read timed out; re-check the watchdog, which may have
            # been refreshed by unrelated traffic while we waited.

    async def _stream_ended_abort(self, description: str) -> _Abort:
        if self._terminal is not None:
            # The turn already reported its outcome; the stream simply closed
            # before the frame we were waiting on. Report the real outcome.
            status, detail = self._terminal
            return _Abort(status, detail)
        exit_code: int | None = None
        with contextlib.suppress(Exception):
            exit_code = await asyncio.wait_for(self._process.wait(), timeout=_EXIT_GRACE_SECONDS)
        detail = (
            f"stream ended without terminal result (exit code {exit_code}) "
            f"while waiting for {description}"
        )
        tail = self._stderr_tail()
        if tail:
            detail = f"{detail}; stderr tail: {tail}"
        return _Abort(DriverResultStatus.FAILED, detail)

    def _send(self, message: Mapping[str, Any]) -> None:
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        self._process.write_stdin(f"{payload}\n".encode())

    def _notify(self, method: str, params: Mapping[str, Any]) -> None:
        try:
            self._send({"jsonrpc": "2.0", "method": method, "params": params})
        except Exception as exc:
            raise _Abort(
                DriverResultStatus.FAILED,
                f"failed to write the {method} notification to stdin: {exc}",
            ) from exc

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
        # Responses carry no ``jsonrpc`` member on this surface, so the id is
        # the only routing key.
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
        match method:
            case "thread/started":
                self._adopt_thread(_mapping(params.get("thread")), source="thread/started")
            case "item/started":
                self._on_item(params, completed=False)
            case "item/completed":
                self._on_item(params, completed=True)
            case "item/agentMessage/delta":
                self._on_agent_delta(params)
            case "item/commandExecution/outputDelta":
                self._on_output_delta(params)
            case "turn/diff/updated":
                self._on_diff(params)
            case "turn/completed" | "turn/failed" | "turn/aborted":
                self._on_turn_terminal(str(method), params)
            case _:
                self._emit(DriverEventKind.LOG, {"method": method, "params": dict(params)})

    def _on_item(self, params: Mapping[str, Any], *, completed: bool) -> None:
        item = _mapping(params.get("item"))
        item_type = item.get("type")
        item_id = _item_id(item, params)
        if item_type in _TOOL_ITEM_TYPES:
            self._on_tool_item(item, item_id, item_type, completed=completed)
        elif item_type == "reasoning":
            text = _item_text(item)
            if text:
                self._emit(DriverEventKind.THINKING, {"text": text, "item_id": item_id})
        elif item_type == "agentMessage":
            if completed:
                self._on_agent_message(item, item_id)
            # item/started carries an empty text placeholder; the deltas stream
            # the content and item/completed repeats it in full.
        else:
            self._emit(
                DriverEventKind.LOG,
                {"item_type": item_type, "item_id": item_id, "completed": completed},
            )

    def _on_tool_item(
        self,
        item: Mapping[str, Any],
        item_id: str,
        item_type: Any,
        *,
        completed: bool,
    ) -> None:
        if completed:
            self._watchdog.tool_finished()
            self._emit(
                DriverEventKind.TOOL_RESULT,
                {
                    "call_id": item_id,
                    "tool_name": item_type,
                    "status": item.get("status"),
                    "exit_code": item.get("exitCode"),
                    "output": item.get("aggregatedOutput"),
                },
            )
            return
        self._tool_call_count += 1
        self._watchdog.tool_started()
        self._emit(
            DriverEventKind.TOOL_USE,
            {
                "call_id": item_id,
                "tool_name": item_type,
                "input": _tool_input(item),
            },
        )

    def _on_agent_message(self, item: Mapping[str, Any], item_id: str) -> None:
        streamed = "".join(self._deltas.pop(item_id, []))
        text = _item_text(item) or streamed
        if text and not streamed:
            # No deltas were seen for this item, so nothing has been observed yet.
            self._emit(DriverEventKind.TEXT, {"text": text, "item_id": item_id})
        phase = item.get("phase")
        self._messages.append((phase if isinstance(phase, str) else "", text))

    def _on_agent_delta(self, params: Mapping[str, Any]) -> None:
        delta = params.get("delta")
        if not isinstance(delta, str) or not delta:
            return
        item_id = _text_or_empty(params.get("itemId"))
        self._deltas.setdefault(item_id, []).append(delta)
        self._emit(DriverEventKind.TEXT, {"text": delta, "item_id": item_id})

    def _on_output_delta(self, params: Mapping[str, Any]) -> None:
        # Streaming output of a running command: a progress frame, not the
        # tool's completion, so the watchdog's tool window stays open.
        self._emit(
            DriverEventKind.TOOL_RESULT,
            {
                "call_id": _text_or_empty(params.get("itemId")),
                "output": params.get("delta"),
                "streaming": True,
            },
        )

    def _on_diff(self, params: Mapping[str, Any]) -> None:
        # The cumulative unified diff of the turn. It describes the work; it is
        # never the deliverable text, so it stays out of the summary.
        for key in ("diff", "unifiedDiff"):
            value = params.get(key)
            if isinstance(value, str):
                self._final_diff = value
                break
        self._emit(DriverEventKind.LOG, {"turn_diff": self._final_diff})

    def _on_turn_terminal(self, method: str, params: Mapping[str, Any]) -> None:
        turn = _mapping(params.get("turn"))
        status = turn.get("status")
        error = turn.get("error")
        if error is None:
            error = params.get("error")
        if method == "turn/completed" and status == SUCCESS_TURN_STATUS and error is None:
            self._record_terminal(DriverResultStatus.SUCCEEDED, "")
        elif method == "turn/aborted":
            self._record_terminal(
                DriverResultStatus.INTERRUPTED,
                f"turn aborted by the agent (status={status!r})",
            )
        else:
            detail = f"{method} reported status={status!r}"
            if error is not None:
                detail = f"{detail} error={_describe_rpc_error(error)}"
            self._record_terminal(DriverResultStatus.FAILED, detail)
        self._turn_finished.set()

    def _record_terminal(self, status: DriverResultStatus, detail: str) -> None:
        if self._terminal is None:
            self._terminal = (status, detail)

    # ------------------------------------------------------ server requests

    def _handle_server_request(self, message: Mapping[str, Any]) -> None:
        method = message.get("method")
        request_id = message.get("id")
        if _is_approval_method(method):
            self._answer_approval(request_id, str(method), _mapping(message.get("params")))
            return
        self._respond(
            request_id,
            error={"code": -32601, "message": f"unsupported method: {method}"},
        )

    def _answer_approval(
        self,
        request_id: Any,
        method: str,
        params: Mapping[str, Any],
    ) -> None:
        """Answer a codex approval request from the permission policy.

        No approval was captured on the wire (the observed config approves
        everything locally), so the decision vocabulary follows codex's
        documented review decisions — ``approved`` / ``denied`` / ``abort`` —
        and is covered by fake-process tests only.
        """

        item = _mapping(params.get("item"))
        tool_name = _approval_tool_name(method, params, item)
        tool_input = item or params
        try:
            decision = self._request.permission_policy.decide(tool_name, tool_input)
        except Exception as exc:
            # A policy that cannot answer must not open the gate.
            decision = PermissionDecision.DENY
            self._notes.append(f"permission policy raised for {tool_name}: {exc}")

        verdict = {
            PermissionDecision.ALLOW: "approved",
            PermissionDecision.DENY: "denied",
            PermissionDecision.ESCALATE: "abort",
        }[decision]
        self._emit(
            DriverEventKind.PERMISSION_REQUEST,
            {
                "tool_name": tool_name,
                "decision": decision.value,
                "method": method,
                "call_id": _item_id(item, params),
            },
        )
        self._respond(request_id, result={"decision": verdict})

        if decision is PermissionDecision.ESCALATE:
            self._abort(
                _Abort(
                    DriverResultStatus.INPUT_REQUIRED,
                    f"approval for {tool_name} cannot be decided locally; run needs input",
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

    # -------------------------------------------------------- finalization

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

    def _select_deliverable(self) -> str:
        """The last completed agentMessage that is an answer, not narration."""

        for phase, text in reversed(self._messages):
            if phase != COMMENTARY_PHASE and text.strip():
                return text.strip()
        for _, text in reversed(self._messages):
            if text.strip():
                self._notes.append(
                    "no non-commentary agent message; fell back to the last commentary message"
                )
                return text.strip()
        self._notes.append("turn completed without any agent message")
        return ""

    def _build_result(self, status: DriverResultStatus) -> DriverResult:
        summary = ""
        if status is DriverResultStatus.SUCCEEDED:
            summary = self._select_deliverable()
        diagnostics = "\n".join(note for note in self._notes if note)
        if status is not DriverResultStatus.SUCCEEDED:
            tail = self._stderr_tail()
            if tail and tail not in diagnostics:
                diagnostics = f"{diagnostics}\nstderr tail: {tail}".strip()
        return DriverResult(
            status=status,
            summary=summary,
            native_session_id=self._native_session_id,
            transcript_path=self._transcript_path,
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


def _text_or_empty(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _item_id(item: Mapping[str, Any], params: Mapping[str, Any]) -> str:
    return _text_or_empty(item.get("id")) or _text_or_empty(params.get("itemId"))


def _item_text(item: Mapping[str, Any]) -> str:
    text = item.get("text")
    if isinstance(text, str):
        return text
    return _extract_text(item.get("content"))


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, Mapping):
        return _text_or_empty(content.get("text"))
    if isinstance(content, list):
        return "".join(_extract_text(block) for block in content)
    return ""


def _tool_input(item: Mapping[str, Any]) -> Mapping[str, Any]:
    """The fields of a tool item that describe what it is about to do."""

    return {
        key: item[key]
        for key in ("command", "cwd", "path", "changes", "commandActions")
        if key in item
    }


def _is_approval_method(method: Any) -> bool:
    if not isinstance(method, str):
        return False
    lowered = method.lower()
    return any(hint in lowered for hint in _APPROVAL_METHOD_HINTS)


def _approval_tool_name(
    method: str,
    params: Mapping[str, Any],
    item: Mapping[str, Any],
) -> str:
    item_type = item.get("type")
    if isinstance(item_type, str) and item_type.strip():
        return item_type
    if "command" in params:
        return "commandExecution"
    if "changes" in params or "fileChange" in params:
        return "fileChange"
    return method


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
