"""Cross-module contracts for the observability module.

Only one thing crosses a module boundary today: the ambient discovery
context. The discovery chain tags each of its LLM calls with the issue and
step they belong to; the usage recorder (same module, infrastructure layer)
reads the tag back. Repository-intelligence writes it through *contracts* so
the read side has a stable import target — a ContextVar living in either
module's internals would couple the other module to non-contract code.

``asyncio`` copies contextvars into tasks (``create_task``) and into worker
threads (``to_thread``, Python 3.9+), which is what makes this design safe
despite the pipeline's shape: the endpoint sets the value, the discovery step
runs on a worker thread, and the synchronous ``chat()`` call that lands in
the usage sink sees the same value with no argument threaded through the
``LLMClient`` protocol.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class UsageContext:
    """Ambient attribution for an in-flight LLM call.

    Set by the discovery chain before it starts a step, read by the usage
    recorder on the same call's sink path. Both fields are optional: a call
    may come from a code path that never set a context (a future consumer,
    a script) and must still be recorded.
    """

    issue_id: UUID | None = None
    step: int | None = None


current_usage_context: ContextVar[UsageContext | None] = ContextVar(
    "repomesh.observability.current_usage_context", default=None
)


class UsageRecorder(Protocol):
    """Synchronous sink for LLM usage observations.

    Implementations must be safe to call from worker threads — the planning
    pipeline runs ``chat()`` inside ``asyncio.to_thread``. The payload is a
    plain dict so the emitting adapter (``integrations.llm``) never imports
    this module; the shape is fixed by what ``DeepSeekClient`` emits and the
    recorder normalises defensively.
    """

    def record(self, usage: dict[str, object]) -> None: ...
