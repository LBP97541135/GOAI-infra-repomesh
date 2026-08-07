"""Span-level tests for planning-phase instrumentation (line A).

An InMemorySpanExporter is attached to the global provider once per session.
The provider may already exist (installed by another test file) — attaching an
extra processor is always allowed, only *replacing* the provider is not.
"""

from __future__ import annotations

import json

import httpx
import pytest
from opentelemetry import trace

from repomesh.integrations.llm import DeepSeekClient, DeepSeekConfig
from repomesh.modules.repository_intelligence.application.confirmation import (
    ConfirmationService,
)
from repomesh.modules.repository_intelligence.application.discovery import (
    RepositoryDiscoveryService,
)
from repomesh.modules.repository_intelligence.application.plan_integration import (
    PlanIntegrationService,
)
from repomesh.modules.repository_intelligence.domain import AutoCard, RepositoryProfile

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


def _spans_named(span_exporter, fragment: str) -> list:
    return [s for s in span_exporter.get_finished_spans() if fragment in (s.name or "")]


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _make_profile(name: str = "billing-api") -> RepositoryProfile:
    return RepositoryProfile(
        id=name,
        name=name,
        url=f"https://github.com/org/{name}",
        auto_card=AutoCard(
            top_dirs=("src",),
            deps=("fastapi",),
            recent_commits=("add invoices",),
            exposed_apis=("/api/v1/invoices",),
            low_signal=False,
        ),
    )


class _FakeLLM:
    def __init__(self, response: str) -> None:
        self._response = response

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> str:
        return self._response


class _FakeCatalog:
    async def list(self) -> list[RepositoryProfile]:
        return []


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_chat_span_carries_genai_attributes_and_usage(span_exporter, monkeypatch) -> None:
    payload = {
        "choices": [
            {
                "message": {"role": "assistant", "content": "hello"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 321, "completion_tokens": 45},
    }
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(payload))

    client = DeepSeekClient(DeepSeekConfig(api_key="k"))
    result = client.chat([{"role": "user", "content": "hi"}], temperature=0.3)

    assert result == "hello"
    (span,) = _spans_named(span_exporter, "chat deepseek-chat")
    attrs = dict(span.attributes or {})
    assert attrs["gen_ai.operation.name"] == "chat"
    assert attrs["gen_ai.provider.name"] == "deepseek"
    assert attrs["gen_ai.request.model"] == "deepseek-chat"
    assert attrs["gen_ai.request.temperature"] == 0.3
    assert attrs["gen_ai.usage.input_tokens"] == 321
    assert attrs["gen_ai.usage.output_tokens"] == 45
    assert attrs["gen_ai.response.finish_reasons"] == ("stop",)
    assert json.loads(attrs["gen_ai.input.messages"]) == [{"role": "user", "content": "hi"}]
    assert json.loads(attrs["gen_ai.output.messages"])["content"] == "hello"


def test_chat_span_records_http_error(span_exporter, monkeypatch) -> None:
    def _boom(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", _boom)
    client = DeepSeekClient(DeepSeekConfig(api_key="k"))

    with pytest.raises(httpx.ConnectError):
        client.chat([{"role": "user", "content": "hi"}])

    (span,) = _spans_named(span_exporter, "chat deepseek-chat")
    assert not span.status.is_ok
    assert any(e.name == "exception" for e in span.events)


def test_confirmation_produces_per_repo_child_spans(span_exporter) -> None:
    response = json.dumps(
        {
            "status": "REQUIRED",
            "confidence": 0.9,
            "reason": "invoice API lives here",
            "plan_summary": "add discount field",
        }
    )
    profile = _make_profile("billing-api")
    service = ConfirmationService(_FakeLLM(response), {"billing-api": profile})

    summary = service.confirm(["billing-api"], "Add discount support")

    assert summary.required[0].repository == "billing-api"
    (root,) = _spans_named(span_exporter, "planning.confirmation")
    (child,) = _spans_named(span_exporter, "confirm billing-api")
    assert child.parent is not None and child.parent.span_id == root.context.span_id
    attrs = dict(child.attributes or {})
    assert attrs["repomesh.repository_name"] == "billing-api"
    assert attrs["repomesh.confirmation.status"] == "REQUIRED"
    assert attrs["repomesh.confirmation.confidence"] == 0.9


def test_integration_fallback_is_marked_on_span(span_exporter) -> None:
    from repomesh.modules.repository_intelligence.application.confirmation import (
        ConfirmationResult,
        ConfirmationSummary,
        RepositoryPlan,
    )

    summary = ConfirmationSummary(
        required=[
            ConfirmationResult(
                repository="billing-api",
                status="REQUIRED",
                confidence=0.9,
                plan=RepositoryPlan(),
            )
        ],
        maybe=[],
        excluded=[],
        supplemented_repos=[],
    )
    service = PlanIntegrationService(_FakeLLM("this is not json at all"))

    plan = service.integrate("Add discount support", summary)

    assert plan.task_dag  # fallback still yields a runnable plan
    (span,) = _spans_named(span_exporter, "planning.integration")
    assert dict(span.attributes or {})["repomesh.plan_fallback"] is True


async def test_discovery_span_marks_keyword_fallback(span_exporter) -> None:
    service = RepositoryDiscoveryService(_FakeCatalog(), llm_client=None)

    await service.discover("Add discount support")

    (span,) = _spans_named(span_exporter, "planning.discovery")
    attrs = dict(span.attributes or {})
    assert attrs["repomesh.discovery.llm_used"] is False
    assert attrs["repomesh.discovery.candidate_count"] == 0
