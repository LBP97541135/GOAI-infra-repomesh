"""Runtime enforcement of the MCP server policies stored in the registry.

``capabilities/mcp/servers.json`` declared timeout, retry, audit, and
degraded-mode contracts that nothing executed. ``McpCallGuard`` is that
missing executor: callers wrap an outbound MCP tool call, and the guard
enforces the stored policy — timeout via ``asyncio.wait_for``, retries only
for read-only operations with one shared audit id, degraded mode refusing
write operations before dispatch, and one structured audit record per call
through the standard ``logging`` pipeline (the unified-log page picks those
up with no new sink).
"""

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from uuid import uuid4

from opentelemetry import trace as _otel_trace

_logger = logging.getLogger("repomesh.mcp.audit")
_tracer = _otel_trace.get_tracer("repomesh")

# Attribute names mirror repomesh_runner.telemetry.SpanAttributes; business
# modules may not import the runner package, so the contract strings are
# restated here and a test asserts they never drift.
_ATTR_SERVER = "repomesh.mcp_server"
_ATTR_TOOL = "repomesh.tool_name"
_ATTR_OUTCOME = "repomesh.outcome"
_ATTR_LATENCY = "repomesh.latency_ms"


class McpDegradedRefused(RuntimeError):
    """The server is degraded and this operation is a write; refused pre-dispatch."""

    def __init__(self, server_id: str, operation: str) -> None:
        super().__init__(f"mcp server {server_id} is degraded; write operation {operation} refused")
        self.server_id = server_id
        self.operation = operation


@dataclass(frozen=True, slots=True)
class McpPolicy:
    """The runtime-facing slice of ``McpServerPolicyRecord``."""

    id: str
    timeout_seconds: int = 30
    max_retries: int = 0
    retryable_only_reads: bool = True
    degraded_block_writes: bool = True
    required_task_features: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class McpCallResult:
    outcome: str  # success | timeout | degraded_refused | error
    latency_ms: float
    attempts: int
    audit_id: str
    #: The invoke's return value on success. The guard wraps a call, it does
    #: not own the call's result — the Worker MCP endpoint needs the
    #: ``WorkerExecutionStarted`` the execution service answered with.
    value: object = None
    #: The exception the invoke raised, when it did. The guard records a
    #: failure as an ``error`` outcome instead of raising it, so this field is
    #: the only place the cause survives for the caller to translate.
    error: BaseException | None = None


#: Operations whose names carry these verbs never retry: they mutate remote
#: state and a blind retry can duplicate it. Idempotency keys are a per-tool
#: contract RepoMesh cannot verify, so writes stay single-shot. ``start`` is
#: here because starting an execution mints a run — a re-fire after a lost
#: response is a second run, whatever the executor's own dedup may promise.
_WRITE_VERBS = ("write", "create", "start", "update", "delete", "merge", "submit", "push", "mutation")


def _is_read_only(operation: str) -> bool:
    lowered = operation.lower()
    return not any(verb in lowered for verb in _WRITE_VERBS)


def _args_hash(args: dict[str, object] | None) -> str:
    encoded = json.dumps(args or {}, ensure_ascii=True, sort_keys=True, default=str)
    return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


class McpCallGuard:
    """Enforce one server's policy over its outbound tool calls.

    ``policy_provider`` resolves the current policy per call so an operator's
    PUT takes effect without redeploying; a missing policy falls back to the
    struct defaults (30s, no retry) rather than failing open into unbounded
    calls.
    """

    def __init__(self, policy_provider=None) -> None:
        self._policy_provider = policy_provider
        self._degraded: set[str] = set()

    def mark_degraded(self, server_id: str) -> None:
        self._degraded.add(server_id)

    def mark_healthy(self, server_id: str) -> None:
        self._degraded.discard(server_id)

    def is_degraded(self, server_id: str) -> bool:
        return server_id in self._degraded

    async def _policy_for(self, server_id: str) -> McpPolicy:
        if self._policy_provider is None:
            return McpPolicy(id=server_id)
        policy = self._policy_provider(server_id)
        if asyncio.iscoroutine(policy):
            policy = await policy
        return policy or McpPolicy(id=server_id)

    async def call(
        self,
        *,
        server_id: str,
        operation: str,
        invoke,
        args: dict[str, object] | None = None,
        task_features: frozenset[str] = frozenset(),
    ) -> McpCallResult:
        """Run one tool call under the server's policy.

        ``invoke`` is an async callable receiving no arguments (the caller
        closed over the client and tool arguments); the guard owns timing,
        retries, degraded gating, and the audit record.
        """

        policy = await self._policy_for(server_id)
        read_only = _is_read_only(operation)
        audit_id = str(uuid4())
        start = time.monotonic()
        attempts = 0
        max_attempts = 1
        if read_only and policy.retryable_only_reads and not self.is_degraded(server_id):
            max_attempts = 1 + policy.max_retries

        with _tracer.start_as_current_span(f"mcp.{server_id}.{operation}") as span:
            span.set_attribute(_ATTR_SERVER, server_id)
            span.set_attribute(_ATTR_TOOL, operation)
            while attempts < max_attempts:
                attempts += 1
                try:
                    returned = await asyncio.wait_for(invoke(), timeout=policy.timeout_seconds)
                    latency = (time.monotonic() - start) * 1000
                    result = McpCallResult("success", latency, attempts, audit_id, value=returned)
                    break
                except TimeoutError:
                    latency = (time.monotonic() - start) * 1000
                    if attempts >= max_attempts:
                        result = McpCallResult("timeout", latency, attempts, audit_id)
                        break
                    continue
                except McpDegradedRefused:
                    raise
                except Exception as exc:
                    latency = (time.monotonic() - start) * 1000
                    result = McpCallResult("error", latency, attempts, audit_id, error=exc)
                    break
            span.set_attribute(_ATTR_OUTCOME, result.outcome)
            span.set_attribute(_ATTR_LATENCY, round(result.latency_ms, 1))

        _logger.info(
            "mcp_call server=%s operation=%s outcome=%s attempts=%d latency_ms=%.1f "
            "args_hash=%s audit_id=%s read_only=%s degraded=%s",
            server_id,
            operation,
            result.outcome,
            attempts,
            result.latency_ms,
            _args_hash(args),
            audit_id,
            read_only,
            self.is_degraded(server_id),
        )
        return result

    async def call_gated(
        self,
        *,
        server_id: str,
        operation: str,
        invoke,
        args: dict[str, object] | None = None,
        task_features: frozenset[str] = frozenset(),
    ) -> McpCallResult:
        """``call`` plus the degraded-mode write gate, checked before dispatch."""

        if self.is_degraded(server_id) and not _is_read_only(operation):
            policy = await self._policy_for(server_id)
            if policy.degraded_block_writes:
                _logger.info(
                    "mcp_call server=%s operation=%s outcome=degraded_refused args_hash=%s",
                    server_id,
                    operation,
                    _args_hash(args),
                )
                raise McpDegradedRefused(server_id, operation)
        return await self.call(
            server_id=server_id,
            operation=operation,
            invoke=invoke,
            args=args,
            task_features=task_features,
        )
