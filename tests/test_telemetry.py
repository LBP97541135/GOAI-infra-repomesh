"""Tests for the shared telemetry bootstrap (step 0 of the observability plan).

The global TracerProvider can only be installed once per process, so these tests
never assert that tracing is *off* — another test file may already have installed
a provider. They only assert the return values and the install-once semantics.
"""

from repomesh_runner.telemetry import SpanAttributes, setup_tracing, tracing_enabled

# Renaming any of these orphans every span already exported. A rename must be a
# deliberate contract change, not a refactor — this test is the tripwire.
FROZEN_ATTRIBUTE_NAMES = {
    "ORGANIZATION_ID": "repomesh.organization_id",
    "PROJECT_ID": "repomesh.project_id",
    "CHANGESET_ID": "repomesh.changeset_id",
    "REPOSITORY_ID": "repomesh.repository_id",
    "TASK_ID": "repomesh.task_id",
    "RUN_ID": "repomesh.run_id",
    "CORRELATION_ID": "repomesh.correlation_id",
    "ATTEMPT": "repomesh.attempt",
    "ADAPTER": "repomesh.adapter",
    "WORKER_AGENT_ID": "repomesh.worker_agent_id",
}


def test_attribute_names_are_a_frozen_contract() -> None:
    for constant, value in FROZEN_ATTRIBUTE_NAMES.items():
        assert getattr(SpanAttributes, constant) == value


def test_setup_tracing_without_endpoint_reports_disabled() -> None:
    # No global-state assertion here: whether tracing is currently enabled
    # depends on which test files ran before this one.
    assert setup_tracing(None, service_name="repomesh-test") is False
    assert setup_tracing("", service_name="repomesh-test") is False


def test_setup_tracing_installs_the_sdk_provider_exactly_once() -> None:
    from opentelemetry import trace

    assert setup_tracing("http://localhost:4318", service_name="repomesh-test") is True
    assert tracing_enabled() is True
    provider = trace.get_tracer_provider()

    # A second call (uvicorn reload, repeated create_app) keeps the provider.
    assert setup_tracing("http://elsewhere:4318", service_name="other") is True
    assert trace.get_tracer_provider() is provider


def test_traces_url_appends_standard_path_to_agentloop_receiver() -> None:
    from repomesh_runner.telemetry import _traces_url

    # AgentLoop 接入点是 base URL：实测直接 POST /apm/trace/opentelemetry 返回 404，
    # 追加 /v1/traces 后（/apm/trace/opentelemetry/v1/traces）接收端可达（405=需 POST）。
    agentloop = (
        "https://proj-xtrace-xxx.cn-hangzhou.log.aliyuncs.com/apm/trace/opentelemetry"
    )
    assert _traces_url(agentloop) == f"{agentloop}/v1/traces"
    # Legacy collector base URLs still get the standard path appended.
    assert _traces_url("http://localhost:4318") == "http://localhost:4318/v1/traces"
    assert _traces_url("http://localhost:4318/") == "http://localhost:4318/v1/traces"
    assert (
        _traces_url("http://localhost:4318/v1/traces") == "http://localhost:4318/v1/traces"
    )


def test_parse_headers_turns_kv_string_into_dict() -> None:
    from repomesh_runner.telemetry import _parse_headers

    assert _parse_headers(None) is None
    assert _parse_headers("") is None
    assert _parse_headers("a=1") == {"a": "1"}
    assert _parse_headers("a=1,b=two words") == {"a": "1", "b": "two words"}
    assert _parse_headers("a=1,,b=2") == {"a": "1", "b": "2"}
    assert _parse_headers("a=1,malformed") == {"a": "1"}
    assert _parse_headers("onlykey") is None


def test_setup_tracing_accepts_agentloop_endpoint_and_headers() -> None:
    # AgentLoop-style full receiver path + auth headers must be accepted without
    # raising; the install-once guard makes this a no-op re-entry.
    assert (
        setup_tracing(
            "https://proj-xtrace-xxx.cn-hangzhou.log.aliyuncs.com/apm/trace/opentelemetry",
            service_name="repomesh-agentloop-test",
            headers="x-arms-license-key=k,x-arms-project=p,x-cms-workspace=w",
        )
        is True
    )


def test_metrics_and_logs_urls_append_signal_paths() -> None:
    from repomesh_runner.telemetry import _logs_url, _metrics_url

    base = "https://proj-xtrace-xxx.cn-hangzhou.log.aliyuncs.com/apm/trace/opentelemetry"
    assert _metrics_url(base) == f"{base}/v1/metrics"
    assert _logs_url(base) == f"{base}/v1/logs"
    assert _metrics_url(f"{base}/v1/metrics") == f"{base}/v1/metrics"
    assert _logs_url(f"{base}/v1/logs") == f"{base}/v1/logs"


def test_setup_metrics_without_endpoint_is_noop() -> None:
    from repomesh_runner.telemetry import setup_metrics

    assert setup_metrics(None, service_name="x") is False
    assert setup_metrics("", service_name="x") is False


def test_setup_metrics_installs_meter_provider_once() -> None:
    from opentelemetry import metrics
    from opentelemetry.sdk.metrics import MeterProvider

    from repomesh_runner.telemetry import setup_metrics

    assert setup_metrics("http://localhost:4318", service_name="repomesh-m-test") is True
    provider = metrics.get_meter_provider()
    assert isinstance(provider, MeterProvider)
    # Re-entry keeps the existing provider.
    assert setup_metrics("http://elsewhere:4318", service_name="other") is True
    assert metrics.get_meter_provider() is provider


def test_setup_logs_without_endpoint_is_noop() -> None:
    from repomesh_runner.telemetry import setup_logs

    assert setup_logs(None, service_name="x") is False


def test_setup_logs_installs_logging_handler() -> None:
    import logging

    from repomesh_runner.telemetry import logs_enabled, setup_logs

    assert setup_logs("http://localhost:4318", service_name="repomesh-l-test") is True
    assert logs_enabled() is True
    assert any(isinstance(h, logging.Handler) for h in logging.getLogger().handlers)
