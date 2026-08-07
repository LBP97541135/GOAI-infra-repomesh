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
