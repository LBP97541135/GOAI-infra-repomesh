"""McpCallGuard tests: timeout, read-only retry, degraded write refusal, audit."""

from __future__ import annotations

import asyncio
import logging

import pytest

from repomesh.modules.capability_management.mcp_guard import (
    McpCallGuard,
    McpDegradedRefused,
    McpPolicy,
)


def _policy(**overrides: object) -> McpPolicy:
    values = {"id": "github", "timeout_seconds": 1, "max_retries": 2, "retryable_only_reads": True}
    values.update(overrides)
    return McpPolicy(**values)  # type: ignore[arg-type]


def test_guard_attribute_names_match_runner_span_contract() -> None:
    """The guard restates span names because business modules cannot import
    repomesh_runner; if either side renames, this test catches the drift."""

    from repomesh.modules.capability_management import mcp_guard
    from repomesh_runner.telemetry import SpanAttributes

    assert mcp_guard._ATTR_SERVER == SpanAttributes.MCP_SERVER
    assert mcp_guard._ATTR_TOOL == SpanAttributes.TOOL_NAME
    assert mcp_guard._ATTR_OUTCOME == SpanAttributes.OUTCOME
    assert mcp_guard._ATTR_LATENCY == SpanAttributes.LATENCY_MS


@pytest.mark.asyncio
async def test_success_records_one_attempt() -> None:
    guard = McpCallGuard(policy_provider=lambda server_id: _policy())
    calls = []

    result = await guard.call(
        server_id="github",
        operation="github.issues.read",
        invoke=lambda: calls.append(1) or asyncio.sleep(0),
        args={"number": 1},
    )

    assert result.outcome == "success"
    assert result.attempts == 1
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_success_returns_invoke_value() -> None:
    guard = McpCallGuard(policy_provider=lambda server_id: _policy())

    async def fetch_issue() -> dict[str, int]:
        return {"number": 7}

    result = await guard.call(
        server_id="github", operation="github.issues.read", invoke=fetch_issue
    )

    assert result.outcome == "success"
    assert result.value == {"number": 7}


@pytest.mark.asyncio
async def test_failed_and_timed_out_calls_have_no_value() -> None:
    guard = McpCallGuard(policy_provider=lambda server_id: _policy(max_retries=1))

    async def fail() -> dict[str, int]:
        raise RuntimeError("boom")

    async def hang() -> dict[str, int]:
        await asyncio.sleep(10)

    failed = await guard.call(
        server_id="github", operation="github.issues.read", invoke=fail
    )
    timed_out = await guard.call(
        server_id="github", operation="github.issues.read", invoke=hang
    )

    assert failed.outcome == "error"
    assert failed.value is None
    assert timed_out.outcome == "timeout"
    assert timed_out.value is None


@pytest.mark.asyncio
async def test_timeout_retries_read_only_then_reports(caplog: pytest.LogCaptureFixture) -> None:
    guard = McpCallGuard(policy_provider=lambda server_id: _policy(max_retries=2))

    async def hang() -> None:
        await asyncio.sleep(10)

    with caplog.at_level(logging.INFO, logger="repomesh.mcp.audit"):
        result = await guard.call(
            server_id="github", operation="github.issues.read", invoke=hang
        )

    assert result.outcome == "timeout"
    assert result.attempts == 3  # 1 + max_retries
    assert any("outcome=timeout" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_write_operations_never_retry() -> None:
    guard = McpCallGuard(policy_provider=lambda server_id: _policy(max_retries=2))
    calls = []

    async def fail() -> None:
        calls.append(1)
        raise RuntimeError("write blew up")

    result = await guard.call(
        server_id="github", operation="github.contents.write", invoke=fail
    )

    assert result.outcome == "error"
    assert result.attempts == 1
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_degraded_refuses_writes_before_dispatch() -> None:
    guard = McpCallGuard()
    guard.mark_degraded("github")
    dispatched = []

    async def never() -> None:
        dispatched.append(1)

    with pytest.raises(McpDegradedRefused):
        await guard.call_gated(
            server_id="github", operation="github.contents.write", invoke=never
        )
    assert dispatched == []

    # Reads still pass in degraded mode.
    async def read() -> None:
        return None

    result = await guard.call_gated(
        server_id="github", operation="github.issues.read", invoke=read
    )
    assert result.outcome == "success"


@pytest.mark.asyncio
async def test_missing_policy_falls_back_to_safe_defaults() -> None:
    guard = McpCallGuard(policy_provider=lambda server_id: None)
    calls = []

    async def read_only() -> None:
        calls.append(1)

    result = await guard.call(server_id="unknown", operation="unknown.read", invoke=read_only)

    assert result.outcome == "success"
    assert result.attempts == 1
    assert len(calls) == 1
