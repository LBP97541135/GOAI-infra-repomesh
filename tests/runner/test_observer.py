"""Span-level tests for the driver-event instrumentation (line B).

Same exporter discipline as ``tests/test_planning_tracing.py``: attach an extra
SimpleSpanProcessor to whatever provider the process already has, never replace
it — the global TracerProvider can only be installed once.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from opentelemetry import trace
from opentelemetry.trace import StatusCode
from test_executor import make_task  # tests/runner is on sys.path under pytest

from repomesh_runner.drivers.base import (
    DriverEvent,
    DriverEventKind,
    DriverFamily,
    DriverResult,
    DriverResultStatus,
)
from repomesh_runner.observer import (
    MAX_PAYLOAD_CHARS,
    OtelDriverObserver,
    compose_observers,
    otel_task_observer,
)
from repomesh_runner.telemetry import SpanAttributes

# ---------------------------------------------------------------------------
# Exporter fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def span_exporter():
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


@pytest.fixture(autouse=True)
def _clear_spans(span_exporter):
    span_exporter.clear()
    yield


def _named(span_exporter, fragment: str) -> list:
    return [s for s in span_exporter.get_finished_spans() if fragment in (s.name or "")]


def _one(span_exporter, fragment: str):
    spans = _named(span_exporter, fragment)
    assert len(spans) == 1, f"expected exactly one {fragment!r} span, got {len(spans)}"
    return spans[0]


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class ScriptedDriver:
    """Replays a fixed event script through the observer, then returns a result."""

    def __init__(
        self,
        events: list[DriverEvent],
        result: DriverResult | None = None,
        *,
        raises: Exception | None = None,
    ) -> None:
        self._events = events
        self._result = result or DriverResult(
            status=DriverResultStatus.SUCCEEDED, summary="done", tool_call_count=1
        )
        self._raises = raises

    @property
    def family(self) -> DriverFamily:
        return DriverFamily.STREAM_JSON

    async def execute(self, request, profile, observer) -> DriverResult:  # type: ignore[no-untyped-def]
        for event in self._events:
            observer(event)
        if self._raises is not None:
            raise self._raises
        return self._result


def _full_script() -> list[DriverEvent]:
    return [
        DriverEvent(
            kind=DriverEventKind.SESSION_STARTED,
            payload={"native_session_id": "sess-9f2c", "transcript_path": "/t/x.jsonl"},
        ),
        DriverEvent(
            kind=DriverEventKind.TOOL_USE,
            payload={
                "call_id": "call-1",
                "tool_name": "Edit",
                "input": {"file_path": "src/pricing/rules.py"},
            },
        ),
        DriverEvent(
            kind=DriverEventKind.TOOL_RESULT,
            payload={"call_id": "call-1", "output": "1 line changed"},
        ),
        DriverEvent(kind=DriverEventKind.TEXT, payload={"text": "added the discount field"}),
    ]


def _run(observer_target, script: list[DriverEvent]) -> None:
    for event in script:
        observer_target(event)


# ---------------------------------------------------------------------------
# Span shape
# ---------------------------------------------------------------------------


def test_full_event_sequence_builds_the_expected_span_tree(span_exporter) -> None:
    task = make_task(worker_agent_id=uuid4())

    with OtelDriverObserver(task) as observer:
        _run(observer, _full_script())
        observer.record_result(
            DriverResult(
                status=DriverResultStatus.SUCCEEDED,
                summary="done",
                native_session_id="sess-9f2c",
                tool_call_count=1,
            )
        )

    root = _one(span_exporter, "invoke_agent claude-code")
    tool = _one(span_exporter, "execute_tool Edit")

    root_attrs = dict(root.attributes or {})
    assert root_attrs["gen_ai.operation.name"] == "invoke_agent"
    assert root_attrs["gen_ai.agent.name"] == "claude-code"
    assert root_attrs["gen_ai.conversation.id"] == str(task.run_id)
    assert root_attrs["agentscope.agent.reply_id"] == "sess-9f2c"
    assert root_attrs[SpanAttributes.RUN_ID] == str(task.run_id)
    assert root_attrs[SpanAttributes.TASK_ID] == str(task.task_id)
    assert root_attrs[SpanAttributes.PROJECT_ID] == str(task.project_id)
    assert root_attrs[SpanAttributes.ORGANIZATION_ID] == str(task.organization_id)
    assert root_attrs[SpanAttributes.CORRELATION_ID] == str(task.correlation_id)
    assert root_attrs[SpanAttributes.REPOSITORY_ID] == str(task.repository.repository_id)
    assert root_attrs[SpanAttributes.ATTEMPT] == 1
    assert root_attrs[SpanAttributes.ADAPTER] == "claude-code"
    assert root_attrs[SpanAttributes.WORKER_AGENT_ID] == str(task.worker_agent_id)
    assert root.status.status_code is StatusCode.OK

    # The tool span is parented explicitly, not by ambient context.
    assert tool.parent is not None
    assert tool.parent.span_id == root.context.span_id
    assert tool.parent.trace_id == root.context.trace_id

    tool_attrs = dict(tool.attributes or {})
    assert tool_attrs["gen_ai.operation.name"] == "execute_tool"
    assert tool_attrs["gen_ai.tool.name"] == "Edit"
    assert tool_attrs["gen_ai.tool.call.id"] == "call-1"
    assert json.loads(tool_attrs["gen_ai.tool.call.arguments"]) == {
        "file_path": "src/pricing/rules.py"
    }
    assert json.loads(tool_attrs["gen_ai.tool.call.result"]) == "1 line changed"
    assert tool.status.status_code is StatusCode.OK
    assert "repomesh.tool_result_missing" not in tool_attrs

    # Text lands as an event on the root, not as its own span.
    text_events = [e for e in root.events if e.name == "agent_text"]
    assert len(text_events) == 1
    assert dict(text_events[0].attributes)["gen_ai.output.messages"] == "added the discount field"


def test_thinking_becomes_a_root_event_and_long_text_is_truncated(span_exporter) -> None:
    with OtelDriverObserver(make_task()) as observer:
        observer(DriverEvent(kind=DriverEventKind.THINKING, payload={"text": "x" * 5000}))
        observer(DriverEvent(kind=DriverEventKind.TEXT, payload={"text": ""}))
        observer(DriverEvent(kind=DriverEventKind.LOG, payload={"reason": "watchdog"}))

    root = _one(span_exporter, "invoke_agent")
    names = [event.name for event in root.events]
    assert names == ["agent_thinking"]  # empty text and LOG add nothing
    body = dict(root.events[0].attributes)["gen_ai.output.messages"]
    assert len(body) < 5000 and body.endswith("...[truncated]")


def test_oversized_tool_payloads_are_truncated(span_exporter) -> None:
    with OtelDriverObserver(make_task()) as observer:
        observer(
            DriverEvent(
                kind=DriverEventKind.TOOL_USE,
                payload={"call_id": "c", "tool_name": "Read", "input": {"blob": "y" * 20000}},
            )
        )
        observer(
            DriverEvent(
                kind=DriverEventKind.TOOL_RESULT,
                payload={"call_id": "c", "output": "z" * 20000},
            )
        )

    attrs = dict(_one(span_exporter, "execute_tool Read").attributes or {})
    for key in ("gen_ai.tool.call.arguments", "gen_ai.tool.call.result"):
        assert len(attrs[key]) <= MAX_PAYLOAD_CHARS + len("...[truncated]")
        assert attrs[key].endswith("...[truncated]")


def test_unserializable_tool_input_does_not_break_the_span(span_exporter) -> None:
    with OtelDriverObserver(make_task()) as observer:
        observer(
            DriverEvent(
                kind=DriverEventKind.TOOL_USE,
                payload={"call_id": "c", "tool_name": "Weird", "input": {"p": Path("a/b")}},
            )
        )
        observer(DriverEvent(kind=DriverEventKind.TOOL_RESULT, payload={"call_id": "c"}))

    attrs = dict(_one(span_exporter, "execute_tool Weird").attributes or {})
    assert "a" in attrs["gen_ai.tool.call.arguments"]


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


def test_permission_deny_shows_up_as_event_and_denied_tools(span_exporter) -> None:
    with OtelDriverObserver(make_task()) as observer:
        observer(
            DriverEvent(
                kind=DriverEventKind.PERMISSION_REQUEST,
                payload={"tool_name": "Read", "decision": "allow"},
            )
        )
        observer(
            DriverEvent(
                kind=DriverEventKind.PERMISSION_REQUEST,
                payload={"tool_name": "Bash", "decision": "deny"},
            )
        )
        # A repeat of the same denial must not duplicate the aggregate.
        observer(
            DriverEvent(
                kind=DriverEventKind.PERMISSION_REQUEST,
                payload={"tool_name": "Bash", "decision": "deny"},
            )
        )

    root = _one(span_exporter, "invoke_agent")
    events = [e for e in root.events if e.name == "permission_request"]
    assert len(events) == 3
    assert dict(events[1].attributes) == {
        "gen_ai.tool.name": "Bash",
        "repomesh.permission.decision": "deny",
    }
    assert dict(root.attributes or {})["repomesh.denied_tools"] == ("Bash",)


def test_escalate_is_not_counted_as_a_denial(span_exporter) -> None:
    with OtelDriverObserver(make_task()) as observer:
        observer(
            DriverEvent(
                kind=DriverEventKind.PERMISSION_REQUEST,
                payload={"tool_name": "Bash", "decision": "escalate"},
            )
        )

    assert "repomesh.denied_tools" not in dict(_one(span_exporter, "invoke_agent").attributes or {})


# ---------------------------------------------------------------------------
# Unclosed spans
# ---------------------------------------------------------------------------


def test_unpaired_tool_use_is_closed_when_the_task_ends(span_exporter) -> None:
    with OtelDriverObserver(make_task()) as observer:
        observer(
            DriverEvent(
                kind=DriverEventKind.TOOL_USE,
                payload={"call_id": "orphan", "tool_name": "Bash", "input": {"command": "ls"}},
            )
        )
        observer.record_result(DriverResult(status=DriverResultStatus.TIMEOUT))

    tool = _one(span_exporter, "execute_tool Bash")
    assert tool.end_time is not None
    assert tool.status.status_code is StatusCode.ERROR
    assert tool.status.description == "tool_result_missing"
    assert dict(tool.attributes or {})["repomesh.tool_result_missing"] is True

    root = _one(span_exporter, "invoke_agent")
    assert root.status.status_code is StatusCode.ERROR
    assert dict(root.attributes or {})["repomesh.driver.status"] == "timeout"


def test_a_reused_call_id_closes_the_stale_span(span_exporter) -> None:
    use = DriverEvent(
        kind=DriverEventKind.TOOL_USE,
        payload={"call_id": "dup", "tool_name": "Grep", "input": {}},
    )
    with OtelDriverObserver(make_task()) as observer:
        observer(use)
        observer(use)
        observer(DriverEvent(kind=DriverEventKind.TOOL_RESULT, payload={"call_id": "dup"}))

    spans = _named(span_exporter, "execute_tool Grep")
    assert len(spans) == 2
    assert {s.status.status_code for s in spans} == {StatusCode.OK, StatusCode.ERROR}


def test_a_result_for_an_unknown_call_id_is_ignored(span_exporter) -> None:
    with OtelDriverObserver(make_task()) as observer:
        observer(
            DriverEvent(kind=DriverEventKind.TOOL_RESULT, payload={"call_id": "?", "output": "x"})
        )

    assert not _named(span_exporter, "execute_tool")


def test_driver_exception_marks_the_root_span(span_exporter) -> None:
    with pytest.raises(RuntimeError), OtelDriverObserver(make_task()) as observer:
        observer(
            DriverEvent(
                kind=DriverEventKind.TOOL_USE,
                payload={"call_id": "c", "tool_name": "Bash", "input": {}},
            )
        )
        raise RuntimeError("driver blew up")

    root = _one(span_exporter, "invoke_agent")
    assert root.status.status_code is StatusCode.ERROR
    assert any(e.name == "exception" for e in root.events)
    # The open tool span still gets closed.
    assert _one(span_exporter, "execute_tool Bash").end_time is not None


def test_events_after_the_scope_closed_are_dropped(span_exporter) -> None:
    observer = OtelDriverObserver(make_task())
    with observer:
        pass
    observer(DriverEvent(kind=DriverEventKind.TEXT, payload={"text": "late"}))
    observer.record_result(DriverResult(status=DriverResultStatus.SUCCEEDED, summary="s"))

    assert len(_named(span_exporter, "invoke_agent")) == 1
    assert not _one(span_exporter, "invoke_agent").events


# ---------------------------------------------------------------------------
# Composition + executor wiring
# ---------------------------------------------------------------------------


def test_compose_observers_delivers_to_everyone_even_when_one_raises() -> None:
    seen_a: list[DriverEvent] = []
    seen_b: list[DriverEvent] = []

    def boom(event: DriverEvent) -> None:
        seen_a.append(event)
        raise ValueError("downstream is broken")

    combined = compose_observers(boom, seen_b.append, None)
    event = DriverEvent(kind=DriverEventKind.TEXT, payload={"text": "hi"})
    combined(event)

    assert seen_a == [event] and seen_b == [event]


def test_compose_observers_passes_a_single_observer_through() -> None:
    seen: list[DriverEvent] = []
    sink = seen.append
    assert compose_observers(None, sink) is sink


async def test_executor_traces_the_run_and_still_feeds_the_outer_observer(
    span_exporter, tmp_path: Path
) -> None:
    from repomesh_runner.executor import DriverExecutor

    seen: list[DriverEvent] = []
    driver = ScriptedDriver(_full_script())
    executor = DriverExecutor(
        drivers={DriverFamily.STREAM_JSON: driver},
        workspace_root=tmp_path,
        binary_resolver=lambda names: f"C:/fake/{names[0]}.exe",
        observer=seen.append,
        task_observer_factory=OtelDriverObserver,
    )

    await executor.execute(make_task())

    assert [event.kind for event in seen] == [
        DriverEventKind.SESSION_STARTED,
        DriverEventKind.TOOL_USE,
        DriverEventKind.TOOL_RESULT,
        DriverEventKind.TEXT,
    ]
    root = _one(span_exporter, "invoke_agent claude-code")
    tool = _one(span_exporter, "execute_tool Edit")
    assert tool.parent.span_id == root.context.span_id
    assert root.status.status_code is StatusCode.OK


async def test_executor_closes_the_root_span_when_the_driver_raises(
    span_exporter, tmp_path: Path
) -> None:
    from repomesh_runner.executor import DriverExecutor

    driver = ScriptedDriver(_full_script()[:2], raises=RuntimeError("spawn failed"))
    executor = DriverExecutor(
        drivers={DriverFamily.STREAM_JSON: driver},
        workspace_root=tmp_path,
        binary_resolver=lambda names: f"C:/fake/{names[0]}.exe",
        task_observer_factory=OtelDriverObserver,
    )

    with pytest.raises(RuntimeError):
        await executor.execute(make_task())

    assert _one(span_exporter, "invoke_agent").status.status_code is StatusCode.ERROR
    assert _one(span_exporter, "execute_tool Edit").end_time is not None


async def test_driver_runs_normally_and_emits_nothing_without_a_factory(
    span_exporter, tmp_path: Path
) -> None:
    """The default executor path: no factory, no per-task observer, no spans."""

    from repomesh_runner.executor import DriverExecutor

    seen: list[DriverEvent] = []
    executor = DriverExecutor(
        drivers={DriverFamily.STREAM_JSON: ScriptedDriver(_full_script())},
        workspace_root=tmp_path,
        binary_resolver=lambda names: f"C:/fake/{names[0]}.exe",
        observer=seen.append,
    )

    result = await executor.execute(make_task())

    assert result.summary == "done"
    assert len(seen) == 4
    assert not _named(span_exporter, "invoke_agent")


async def test_untraced_factory_returns_no_observer(monkeypatch, span_exporter, tmp_path) -> None:
    """``otel_task_observer`` is the zero-cost gate when tracing is off."""

    import repomesh_runner.observer as observer_module
    from repomesh_runner.executor import DriverExecutor

    monkeypatch.setattr(observer_module, "tracing_enabled", lambda: False)
    assert otel_task_observer(make_task()) is None

    executor = DriverExecutor(
        drivers={DriverFamily.STREAM_JSON: ScriptedDriver(_full_script())},
        workspace_root=tmp_path,
        binary_resolver=lambda names: f"C:/fake/{names[0]}.exe",
        task_observer_factory=otel_task_observer,
    )

    result = await executor.execute(make_task())

    assert result.summary == "done"
    assert not _named(span_exporter, "invoke_agent")


def test_credentials_never_reach_a_span_attribute(span_exporter) -> None:
    task = make_task(credential_refs=("vault://gh-token", "vault://anthropic-key"))

    with OtelDriverObserver(task) as observer:
        observer.record_result(
            DriverResult(
                status=DriverResultStatus.FAILED,
                diagnostics="ANTHROPIC_API_KEY=sk-super-secret was rejected",
            )
        )

    root = _one(span_exporter, "invoke_agent")
    serialized = json.dumps(dict(root.attributes or {}), default=str)
    assert "vault://" not in serialized
    assert "sk-super-secret" not in serialized
    assert "ANTHROPIC_API_KEY" not in serialized
    # Only the terminal status value survives from the failure path.
    assert dict(root.attributes or {})["repomesh.driver.status"] == "failed"
