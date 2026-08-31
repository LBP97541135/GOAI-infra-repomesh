"""Shared OpenTelemetry bootstrap and span-attribute contract for both planes.

The attribute names below are the cross-plane observability contract described in
``docs/development/observability-instrumentation-plan-20260807.md``. Exported spans keep
these names forever, so renaming one orphans every trace already stored — treat the
values like Runtime v1 wire fields: additions are cheap, renames are breaking.

This module lives in ``repomesh_runner`` because both processes need it and the
dependency direction only allows ``repomesh`` → ``repomesh_runner``.

``setup_tracing`` is deliberately opt-in: without an endpoint no TracerProvider is
installed, ``opentelemetry.trace`` keeps its no-op proxy provider, and every
``tracer.start_span`` call in the codebase stays near-free. Instrumentation sites
therefore never need their own enabled/disabled check.
"""

import functools
import inspect
import logging
from collections.abc import Callable
from typing import Any, TypeVar

from opentelemetry import trace

_logger = logging.getLogger(__name__)

__all__ = [
    "SpanAttributes",
    "setup_logs",
    "setup_metrics",
    "setup_tracing",
    "traced",
    "tracing_enabled",
]

_TRACES_PATH = "/v1/traces"
_METRICS_PATH = "/v1/metrics"
_LOGS_PATH = "/v1/logs"

_F = TypeVar("_F", bound=Callable[..., Any])


class SpanAttributes:
    """RepoMesh span/resource attribute names — the irreversible part.

    Values follow the ``repomesh.*`` namespace next to the OpenTelemetry GenAI
    conventions (``gen_ai.*``). UUIDs are serialized with ``str()``.
    """

    ORGANIZATION_ID = "repomesh.organization_id"
    PROJECT_ID = "repomesh.project_id"
    CHANGESET_ID = "repomesh.changeset_id"
    REPOSITORY_ID = "repomesh.repository_id"
    TASK_ID = "repomesh.task_id"
    RUN_ID = "repomesh.run_id"
    CORRELATION_ID = "repomesh.correlation_id"
    ATTEMPT = "repomesh.attempt"
    ADAPTER = "repomesh.adapter"
    WORKER_AGENT_ID = "repomesh.worker_agent_id"
    # Capability governance (skill registry + MCP guard) attributes.
    TOOL_NAME = "repomesh.tool_name"
    MCP_SERVER = "repomesh.mcp_server"
    SKILL_ID = "repomesh.skill_id"
    SKILL_VERSION = "repomesh.skill_version"
    OUTCOME = "repomesh.outcome"
    LATENCY_MS = "repomesh.latency_ms"


def tracing_enabled() -> bool:
    """True when a real SDK TracerProvider is installed (``setup_tracing`` ran)."""

    from opentelemetry.sdk.trace import TracerProvider

    return isinstance(trace.get_tracer_provider(), TracerProvider)


def traced(name: str) -> Callable[[_F], _F]:
    """Wrap a sync or async callable in a span named ``name``.

    Keeps business methods down to one annotation line; result attributes are
    added inside the method via ``trace.get_current_span().set_attribute(...)``.
    Exceptions are recorded and set the span status to ERROR (the context
    manager's defaults). With tracing off the no-op tracer makes this near-free.
    """

    def decorate(fn: _F) -> _F:
        span_name = name
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                tracer = trace.get_tracer("repomesh")
                # Studio renders spans without an explicit status as UNSET,
                # indistinguishable from "never finished" — mark success.
                with tracer.start_as_current_span(span_name) as span:
                    result = await fn(*args, **kwargs)
                    span.set_status(trace.StatusCode.OK)
                    return result

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = trace.get_tracer("repomesh")
            with tracer.start_as_current_span(span_name) as span:
                result = fn(*args, **kwargs)
                span.set_status(trace.StatusCode.OK)
                return result

        return wrapper  # type: ignore[return-value]

    return decorate


def setup_tracing(
    endpoint: str | None, *, service_name: str, headers: str | None = None
) -> bool:
    """Install the global TracerProvider exporting OTLP/HTTP to ``endpoint``.

    ``endpoint`` is the collector base URL (e.g. ``http://localhost:3000`` for a
    local AgentScope Studio); the standard ``/v1/traces`` path is appended when
    missing. Alibaba Cloud AgentLoop hands out a base receiver
    (``.../apm/trace/opentelemetry``) that does **not** end in a signal path, so
    it also gets ``/v1/traces`` appended — always give this function the base
    URL, never a hand-built signal path. ``headers`` is an optional
    ``"k=v,k2=v2"`` string forwarded to every export request (AgentLoop requires
    ``x-arms-license-key``, ``x-arms-project`` and ``x-cms-workspace``). ``None``
    or empty means tracing stays off and the call is a no-op.

    Returns whether tracing is active. The global provider can only be installed
    once per process, so a second call (uvicorn reload, test re-entry) keeps the
    existing provider instead of fighting the OTel SDK over it.
    """

    if not endpoint:
        return False
    if tracing_enabled():
        return True

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=_traces_url(endpoint), headers=_parse_headers(headers)
            )
        )
    )
    trace.set_tracer_provider(provider)
    _logger.info("tracing enabled: service=%s endpoint=%s", service_name, endpoint)
    return True


def setup_metrics(
    endpoint: str | None,
    *,
    service_name: str,
    headers: str | None = None,
    interval_seconds: int = 60,
) -> bool:
    """Install a MeterProvider exporting OTLP/HTTP metrics to ``endpoint``.

    Same contract as :func:`setup_tracing`: ``endpoint`` is a base URL (the
    ``/v1/metrics`` signal path is appended when missing), ``headers`` is an
    optional ``"k=v,k2=v2"`` string, and ``None``/empty disables metrics. The
    global provider is installed at most once per process.
    """

    if not endpoint:
        return False
    if metrics_enabled():
        return True

    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource

    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=_metrics_url(endpoint), headers=_parse_headers(headers)),
        export_interval_millis=interval_seconds * 1000,
    )
    provider = MeterProvider(
        metric_readers=[reader], resource=Resource.create({"service.name": service_name})
    )
    from opentelemetry import metrics

    metrics.set_meter_provider(provider)
    _logger.info("metrics enabled: service=%s endpoint=%s", service_name, endpoint)
    return True


def metrics_enabled() -> bool:
    """True when a real SDK MeterProvider is installed (``setup_metrics`` ran)."""

    from opentelemetry import metrics
    from opentelemetry.sdk.metrics import MeterProvider

    return isinstance(metrics.get_meter_provider(), MeterProvider)


def setup_logs(
    endpoint: str | None,
    *,
    service_name: str,
    headers: str | None = None,
    level: int = logging.WARNING,
) -> bool:
    """Export Python ``logging`` records as OTLP/HTTP logs to ``endpoint``.

    Uses the OpenTelemetry ``LoggingHandler``, which attaches the active
    ``trace_id``/``span_id`` to every exported record — that is what makes the
    "log ⇄ trace" drill-down in the console work. Same base-URL + headers
    contract as :func:`setup_tracing`; ``level`` gates how much is exported
    (default ``WARNING`` keeps the volume low). ``None``/empty disables logs.
    """

    if not endpoint:
        return False
    if logs_enabled():
        return True

    from opentelemetry._logs import set_logger_provider
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.resources import Resource

    provider = LoggerProvider(
        resource=Resource.create({"service.name": service_name})
    )
    provider.add_log_record_processor(
        BatchLogRecordProcessor(
            OTLPLogExporter(endpoint=_logs_url(endpoint), headers=_parse_headers(headers))
        )
    )
    set_logger_provider(provider)
    logging.getLogger().addHandler(LoggingHandler(level=level, logger_provider=provider))
    _logger.info("logs enabled: service=%s endpoint=%s level=%s", service_name, endpoint, level)
    return True


def logs_enabled() -> bool:
    """True when an OTLP logging handler is installed (``setup_logs`` ran)."""

    from opentelemetry.sdk._logs import LoggingHandler

    return any(
        isinstance(handler, LoggingHandler) for handler in logging.getLogger().handlers
    )


def _parse_headers(headers: str | None) -> dict[str, str] | None:
    """Turn ``"k=v,k2=v2"`` into the header dict OTLPExporter expects."""
    if not headers:
        return None
    parsed: dict[str, str] = {}
    for pair in headers.split(","):
        key, sep, value = pair.partition("=")
        if sep and key.strip():
            parsed[key.strip()] = value.strip()
    return parsed or None


def _traces_url(endpoint: str) -> str:
    base = endpoint.rstrip("/")
    return base if base.endswith(_TRACES_PATH) else f"{base}{_TRACES_PATH}"


def _metrics_url(endpoint: str) -> str:
    base = endpoint.rstrip("/")
    return base if base.endswith(_METRICS_PATH) else f"{base}{_METRICS_PATH}"


def _logs_url(endpoint: str) -> str:
    base = endpoint.rstrip("/")
    return base if base.endswith(_LOGS_PATH) else f"{base}{_LOGS_PATH}"
