"""OpenTelemetry GenAI spans for one driver execution (line B of the plan).

``OtelDriverObserver`` turns the driver event stream (``DriverEventKind``, the
same set emitted by ``stream_json`` / ``acp`` / ``app_server``) into the GenAI
span shapes described in
``docs/development/observability-instrumentation-plan-20260807.md``:

    invoke_agent {adapter_id}          ← one per task, the root
    └── execute_tool {tool_name}       ← one per TOOL_USE/TOOL_RESULT pair

The observer is **stateful and per task**: it holds the root span plus the
open tool spans keyed by ``call_id``. ``DriverExecutor`` therefore builds a
fresh one per ``execute()`` call and fans events out to it alongside the
long-lived observer passed to the constructor — see ``compose_observers``.

Two constraints shape the implementation:

* Observer callbacks fire inside the driver's own coroutines, so the ambient
  OTel context is whatever that task happens to carry. Parenting is therefore
  always **explicit** via ``trace.set_span_in_context(root)``; nothing here
  relies on ``start_as_current_span``.
* **Credential redline**: no environment variables, tokens, or
  ``credential_refs`` ever reach a span attribute. Driver diagnostics (which
  carry raw stderr) are deliberately not exported either — only the terminal
  status value is.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable, Mapping
from types import TracebackType

from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

from repomesh_runner.contracts import RunnerTask
from repomesh_runner.drivers.base import (
    DriverEvent,
    DriverEventKind,
    DriverObserver,
    DriverResult,
    DriverResultStatus,
)
from repomesh_runner.telemetry import SpanAttributes, tracing_enabled

__all__ = [
    "OtelDriverObserver",
    "compose_observers",
    "otel_task_observer",
]

TRACER_NAME = "repomesh.runner"

# GenAI semantic-convention attribute names used by this module. Kept local
# rather than in SpanAttributes: that class owns the repomesh.* contract, these
# are upstream OpenTelemetry names.
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_AGENT_NAME = "gen_ai.agent.name"
GEN_AI_CONVERSATION_ID = "gen_ai.conversation.id"
GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"
GEN_AI_TOOL_CALL_ARGUMENTS = "gen_ai.tool.call.arguments"
GEN_AI_TOOL_CALL_RESULT = "gen_ai.tool.call.result"
GEN_AI_OUTPUT_MESSAGES = "gen_ai.output.messages"

# AgentScope v2 correlates a Studio run by reply id; the provider session id is
# the closest thing the Runner has.
AGENTSCOPE_REPLY_ID = "agentscope.agent.reply_id"

DENIED_TOOLS = "repomesh.denied_tools"
PERMISSION_DECISION = "repomesh.permission.decision"
DRIVER_STATUS = "repomesh.driver.status"
TOOL_CALL_COUNT = "repomesh.driver.tool_call_count"
TOOL_RESULT_MISSING = "repomesh.tool_result_missing"

OPERATION_INVOKE_AGENT = "invoke_agent"
OPERATION_EXECUTE_TOOL = "execute_tool"

EVENT_PERMISSION_REQUEST = "permission_request"
EVENT_AGENT_TEXT = "agent_text"
EVENT_AGENT_THINKING = "agent_thinking"

# Tool payloads are agent-authored and unbounded; span attributes are not the
# place to store a 4 MB file read.
MAX_PAYLOAD_CHARS = 8192
MAX_TEXT_CHARS = 2048

_TRUNCATION_MARK = "...[truncated]"

_DENY_DECISIONS = frozenset({"deny", "denied", "reject", "rejected"})


def compose_observers(*observers: DriverObserver | None) -> DriverObserver:
    """Fan one driver event out to every observer given.

    Each observer is isolated: one raising must not starve the others, because
    the drivers wrap the whole observer call in a single ``suppress(Exception)``
    and would otherwise drop the remaining ones silently.
    """

    active = [observer for observer in observers if observer is not None]
    if len(active) == 1:
        return active[0]

    def fan_out(event: DriverEvent) -> None:
        for observer in active:
            with contextlib.suppress(Exception):
                observer(event)

    return fan_out


def otel_task_observer(task: RunnerTask) -> OtelDriverObserver | None:
    """A per-task observer, or ``None`` when tracing is not installed.

    Returning ``None`` keeps the whole path — composition, span creation, JSON
    truncation — out of the picture for the default (untraced) deployment.
    """

    if not tracing_enabled():
        return None
    return OtelDriverObserver(task)


class OtelDriverObserver:
    """Driver observer that emits one ``invoke_agent`` trace per task.

    Lifecycle (driven by :class:`~repomesh_runner.executor.DriverExecutor`)::

        with OtelDriverObserver(task) as observer:
            result = await driver.execute(request, profile, observer)
            observer.record_result(result)

    The root span opens on ``__enter__`` rather than on ``SESSION_STARTED`` so
    that a run which dies before the provider reports a session still produces a
    span. ``__exit__`` closes every span the run left open, so no exception path
    can leak one.
    """

    def __init__(self, task: RunnerTask, *, tracer: trace.Tracer | None = None) -> None:
        self._task = task
        self._tracer = tracer or trace.get_tracer(TRACER_NAME)
        self._root: Span | None = None
        self._tool_spans: dict[str, Span] = {}
        self._denied_tools: list[str] = []
        self._result_recorded = False

    # ------------------------------------------------------------------ scope

    def __enter__(self) -> OtelDriverObserver:
        self._root = self._tracer.start_span(
            f"{OPERATION_INVOKE_AGENT} {self._task.adapter_id}",
            attributes=_root_attributes(self._task),
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        root = self._root
        self._close_orphan_tool_spans()
        if root is None:
            return
        if exc is not None:
            root.record_exception(exc)
            root.set_status(Status(StatusCode.ERROR, type(exc).__name__))
        root.end()
        self._root = None

    def record_result(self, result: DriverResult) -> None:
        """Map the terminal ``DriverResult`` onto the root span status.

        ``diagnostics`` is intentionally not exported: it carries raw process
        stderr, which is exactly where a leaked token would show up.
        """

        root = self._root
        if root is None:
            return
        self._result_recorded = True
        root.set_attribute(DRIVER_STATUS, result.status.value)
        root.set_attribute(TOOL_CALL_COUNT, result.tool_call_count)
        if result.native_session_id:
            root.set_attribute(AGENTSCOPE_REPLY_ID, result.native_session_id)
        if result.status is DriverResultStatus.SUCCEEDED:
            root.set_status(Status(StatusCode.OK))
        else:
            root.set_status(Status(StatusCode.ERROR, result.status.value))

    # --------------------------------------------------------------- observer

    def __call__(self, event: DriverEvent) -> None:
        root = self._root
        if root is None:
            return
        payload = event.payload
        match event.kind:
            case DriverEventKind.SESSION_STARTED:
                self._on_session_started(root, payload)
            case DriverEventKind.TOOL_USE:
                self._on_tool_use(root, payload)
            case DriverEventKind.TOOL_RESULT:
                self._on_tool_result(payload)
            case DriverEventKind.PERMISSION_REQUEST:
                self._on_permission_request(root, payload)
            case DriverEventKind.TEXT:
                self._on_message(root, EVENT_AGENT_TEXT, payload)
            case DriverEventKind.THINKING:
                self._on_message(root, EVENT_AGENT_THINKING, payload)
            case _:
                # LOG is driver bookkeeping (watchdog notes, parse failures); it
                # has its own sink and would only add noise to the trace.
                return

    def _on_session_started(self, root: Span, payload: Mapping[str, object]) -> None:
        session_id = payload.get("native_session_id")
        if isinstance(session_id, str) and session_id:
            root.set_attribute(AGENTSCOPE_REPLY_ID, session_id)

    def _on_tool_use(self, root: Span, payload: Mapping[str, object]) -> None:
        call_id = _text(payload.get("call_id"))
        tool_name = _text(payload.get("tool_name")) or "tool"
        # A repeated call id means the provider reused it; close the stale span
        # rather than dropping it on the floor.
        self._end_orphan(self._tool_spans.pop(call_id, None))
        span = self._tracer.start_span(
            f"{OPERATION_EXECUTE_TOOL} {tool_name}",
            context=trace.set_span_in_context(root),
            attributes={
                GEN_AI_OPERATION_NAME: OPERATION_EXECUTE_TOOL,
                GEN_AI_TOOL_NAME: tool_name,
                GEN_AI_TOOL_CALL_ID: call_id,
                GEN_AI_TOOL_CALL_ARGUMENTS: _json_snippet(
                    payload.get("input"), MAX_PAYLOAD_CHARS
                ),
            },
        )
        self._tool_spans[call_id] = span

    def _on_tool_result(self, payload: Mapping[str, object]) -> None:
        span = self._tool_spans.pop(_text(payload.get("call_id")), None)
        if span is None:
            # A result for a call this observer never saw (resumed session,
            # streaming delta before the item was announced). Nothing to close.
            return
        span.set_attribute(
            GEN_AI_TOOL_CALL_RESULT, _json_snippet(payload.get("output"), MAX_PAYLOAD_CHARS)
        )
        span.set_status(Status(StatusCode.OK))
        span.end()

    def _on_permission_request(self, root: Span, payload: Mapping[str, object]) -> None:
        tool_name = _text(payload.get("tool_name"))
        decision = _text(payload.get("decision"))
        root.add_event(
            EVENT_PERMISSION_REQUEST,
            {GEN_AI_TOOL_NAME: tool_name, PERMISSION_DECISION: decision},
        )
        if decision.lower() in _DENY_DECISIONS and tool_name not in self._denied_tools:
            self._denied_tools.append(tool_name)
            root.set_attribute(DENIED_TOOLS, tuple(self._denied_tools))

    def _on_message(self, root: Span, name: str, payload: Mapping[str, object]) -> None:
        text = _text(payload.get("text"))
        if not text:
            return
        root.add_event(name, {GEN_AI_OUTPUT_MESSAGES: _truncate(text, MAX_TEXT_CHARS)})

    # ---------------------------------------------------------------- helpers

    def _close_orphan_tool_spans(self) -> None:
        for span in self._tool_spans.values():
            self._end_orphan(span)
        self._tool_spans.clear()

    @staticmethod
    def _end_orphan(span: Span | None) -> None:
        """End a tool span that never got its TOOL_RESULT."""

        if span is None:
            return
        span.set_attribute(TOOL_RESULT_MISSING, True)
        span.set_status(Status(StatusCode.ERROR, "tool_result_missing"))
        span.end()


def _root_attributes(task: RunnerTask) -> dict[str, object]:
    attributes: dict[str, object] = {
        GEN_AI_OPERATION_NAME: OPERATION_INVOKE_AGENT,
        GEN_AI_AGENT_NAME: task.adapter_id,
        GEN_AI_CONVERSATION_ID: str(task.run_id),
        SpanAttributes.RUN_ID: str(task.run_id),
        SpanAttributes.TASK_ID: str(task.task_id),
        SpanAttributes.PROJECT_ID: str(task.project_id),
        SpanAttributes.ORGANIZATION_ID: str(task.organization_id),
        SpanAttributes.CORRELATION_ID: str(task.correlation_id),
        SpanAttributes.ATTEMPT: task.attempt,
        SpanAttributes.ADAPTER: task.adapter_id,
    }
    repository = getattr(task, "repository", None)
    repository_id = getattr(repository, "repository_id", None)
    if repository_id is not None:
        attributes[SpanAttributes.REPOSITORY_ID] = str(repository_id)
    if task.worker_agent_id is not None:
        attributes[SpanAttributes.WORKER_AGENT_ID] = str(task.worker_agent_id)
    return attributes


def _text(value: object) -> str:
    return value if isinstance(value, str) else ("" if value is None else str(value))


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + _TRUNCATION_MARK


def _json_snippet(value: object, limit: int) -> str:
    """JSON for a span attribute, truncated, never raising on odd payloads."""

    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = _text(value)
    return _truncate(text, limit)


# Re-exported for callers that only need the callable shape.
TaskObserverFactory = Callable[[RunnerTask], "OtelDriverObserver | None"]
