"""DeepSeek chat-completions adapter.

The concrete LLM client used by the planning phases. It satisfies the
``LLMClient`` protocols the *repository_intelligence* services declare
(structural typing — no import in either direction), and concrete selection
happens in ``repomesh.bootstrap`` per the dependency rules.

This adapter is also the tracing boundary for planning-phase LLM calls: token
usage and finish reasons only exist in the raw response payload, which
``chat()`` discards, so the span must be produced here rather than by a
wrapper around the ``LLMClient`` protocol. Span attributes follow the
OpenTelemetry GenAI semantic conventions so any OTLP backend that understands
them (AgentScope Studio, Langfuse, ...) renders model, tokens and messages.

The same payload is forwarded to the optional ``usage_sink`` — a plain
callable, so this module never imports the observability module. The sink
runs synchronously inside ``chat()`` (which itself runs on an
``asyncio.to_thread`` worker during planning) and is responsible for being
thread-safe; the composition root wires it to the queue-based recorder.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx
from opentelemetry import metrics, trace
from opentelemetry.trace import StatusCode

_tracer = trace.get_tracer("repomesh.llm")

# Metrics run through the same proxy mechanism as _tracer: counters created here
# resolve to the real MeterProvider once setup_metrics() runs, and stay no-op
# (near-free) when it never does.
_meter = metrics.get_meter("repomesh.llm")
_llm_calls = _meter.create_counter(
    "repomesh.llm.calls",
    unit="1",
    description="DeepSeek chat completion calls, split by outcome",
)
_llm_input_tokens = _meter.create_counter(
    "repomesh.llm.tokens.input",
    unit="token",
    description="Prompt tokens sent to DeepSeek",
)
_llm_output_tokens = _meter.create_counter(
    "repomesh.llm.tokens.output",
    unit="token",
    description="Completion tokens returned by DeepSeek",
)

# Prompts are repo profiles plus requirement text; 32 KiB keeps replay useful
# without letting a runaway prompt bloat every exported span.
_MAX_MESSAGE_ATTRIBUTE_CHARS = 32 * 1024


@dataclass(frozen=True, slots=True)
class DeepSeekConfig:
    api_key: str
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    timeout_seconds: float = 60.0


class DeepSeekClient:
    def __init__(
        self,
        config: DeepSeekConfig,
        *,
        usage_sink: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        self._config = config
        self._usage_sink = usage_sink

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> str:
        with _tracer.start_as_current_span(
            f"chat {self._config.model}",
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": "deepseek",
                "gen_ai.request.model": self._config.model,
                "gen_ai.request.temperature": temperature,
                "gen_ai.input.messages": _serialized(messages),
            },
        ) as span:
            started = time.monotonic()
            try:
                response = httpx.post(
                    f"{self._config.base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {self._config.api_key}"},
                    json={
                        "model": self._config.model,
                        "messages": messages,
                        "temperature": temperature,
                    },
                    timeout=self._config.timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
            except Exception:
                # A failed call is still a call: report it so reliability
                # problems show up on the observability page instead of being
                # silently absent. Re-raise so the span above records the
                # exception and the status stays non-OK.
                _llm_calls.add(
                    1,
                    {
                        "gen_ai.provider.name": "deepseek",
                        "gen_ai.request.model": self._config.model,
                        "repomesh.outcome": "error",
                    },
                )
                if self._usage_sink is not None:
                    self._usage_sink(
                        self._usage_payload(
                            usage={},
                            latency_ms=_millis_since(started),
                            status="error",
                        )
                    )
                raise
            choice = payload["choices"][0]
            usage = payload.get("usage") or {}
            span.set_attributes(
                {
                    "gen_ai.usage.input_tokens": int(usage.get("prompt_tokens") or 0),
                    "gen_ai.usage.output_tokens": int(usage.get("completion_tokens") or 0),
                    "gen_ai.response.finish_reasons": [str(choice.get("finish_reason") or "")],
                    "gen_ai.output.messages": _serialized(choice.get("message") or {}),
                }
            )
            _llm_calls.add(
                1,
                {
                    "gen_ai.provider.name": "deepseek",
                    "gen_ai.request.model": self._config.model,
                    "repomesh.outcome": "ok",
                },
            )
            _llm_input_tokens.add(
                int(usage.get("prompt_tokens") or 0),
                {"gen_ai.request.model": self._config.model},
            )
            _llm_output_tokens.add(
                int(usage.get("completion_tokens") or 0),
                {"gen_ai.request.model": self._config.model},
            )
            span.set_status(StatusCode.OK)
            if self._usage_sink is not None:
                self._usage_sink(
                    self._usage_payload(
                        usage=usage,
                        latency_ms=_millis_since(started),
                        status="ok",
                        finish_reason=choice.get("finish_reason"),
                    )
                )
            return str(choice["message"]["content"])

    def _usage_payload(
        self,
        *,
        usage: dict,
        latency_ms: int,
        status: str,
        finish_reason: object | None = None,
    ) -> dict[str, object]:
        return {
            "provider": "deepseek",
            "model": self._config.model,
            "operation": "chat",
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
            "finish_reason": str(finish_reason) if finish_reason else None,
            "latency_ms": latency_ms,
            "status": status,
        }


def _millis_since(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _serialized(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)[:_MAX_MESSAGE_ATTRIBUTE_CHARS]


def make_llm_client(
    api_key: str | None,
    *,
    base_url: str = "https://api.deepseek.com/v1",
    model: str = "deepseek-chat",
    usage_sink: Callable[[dict[str, object]], None] | None = None,
) -> DeepSeekClient | None:
    if not api_key:
        return None
    return DeepSeekClient(
        DeepSeekConfig(api_key=api_key, base_url=base_url, model=model),
        usage_sink=usage_sink,
    )
