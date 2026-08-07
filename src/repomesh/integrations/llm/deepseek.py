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
"""

import json
from dataclasses import dataclass

import httpx
from opentelemetry import trace
from opentelemetry.trace import StatusCode

_tracer = trace.get_tracer("repomesh.llm")

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
    def __init__(self, config: DeepSeekConfig) -> None:
        self._config = config

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
            span.set_status(StatusCode.OK)
            return str(choice["message"]["content"])


def _serialized(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)[:_MAX_MESSAGE_ATTRIBUTE_CHARS]


def make_llm_client(
    api_key: str | None,
    *,
    base_url: str = "https://api.deepseek.com/v1",
    model: str = "deepseek-chat",
) -> DeepSeekClient | None:
    if not api_key:
        return None
    return DeepSeekClient(DeepSeekConfig(api_key=api_key, base_url=base_url, model=model))
