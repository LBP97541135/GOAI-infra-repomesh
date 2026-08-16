"""HTTP response shapes for the observability API.

These are the single source of truth for the frontend's observe contract —
``frontend/src/api/contract.ts`` mirrors them by hand, and the mirror must be
kept in sync whenever a field changes here.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ObserveStepMetric(BaseModel):
    """Roll-up per discovery step (1..4); ``step`` is null outside discovery."""

    step: int | None
    calls: int
    prompt_tokens: int
    completion_tokens: int


class ObserveModelMetric(BaseModel):
    model: str
    calls: int
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_usd: float


class ObserveDailyPoint(BaseModel):
    """One trailing-window bucket; ``date`` is a calendar day (UTC, YYYY-MM-DD)."""

    date: str
    calls: int
    prompt_tokens: int
    completion_tokens: int


class ObserveErrorRow(BaseModel):
    """One failed call (``status != 'ok'``), newest first, capped at 5."""

    created_at: datetime
    model: str
    operation: str
    finish_reason: str | None
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float | None


class ObserveSummary(BaseModel):
    """System-level dashboard over the trailing window."""

    calls: int
    success_calls: int
    error_calls: int
    success_rate: float | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    avg_latency_ms: float | None
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    first_usage_at: datetime | None
    last_usage_at: datetime | None
    by_model: list[ObserveModelMetric]
    by_step: list[ObserveStepMetric]
    daily: list[ObserveDailyPoint]
    recent_errors: list[ObserveErrorRow]


class ObserveIssueRow(BaseModel):
    """Issue-level roll-up: how much inference one issue consumed."""

    issue_id: UUID
    calls: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    avg_latency_ms: float | None
    last_usage_at: datetime | None


class ObserveIssuesResponse(BaseModel):
    issues: list[ObserveIssueRow]


class ObserveIssueLogGroup(BaseModel):
    """One issue's log aggregate, for the logs page's "by issue" view."""

    issue_id: UUID
    count: int
    last_at: datetime | None = None


class IssueLogGroupsResponse(BaseModel):
    issues: list[ObserveIssueLogGroup]


class ObserveTraceIssueGroup(BaseModel):
    """One issue's inferred activity window + suspected trace sessions.

    Approximate attribution: trace sessions are task-keyed, so a session
    counts when its first-seen time falls inside the issue's activity
    window (usage ∪ logs) padded by a slack margin.
    """

    issue_id: UUID
    activity_start: datetime | None = None
    activity_end: datetime | None = None
    suspected_sessions: int
    last_session_at: datetime | None = None


class TraceIssueGroupsResponse(BaseModel):
    issues: list[ObserveTraceIssueGroup]


class AlertRulePayload(BaseModel):
    """Create/update body for an alert rule."""

    name: str | None = None
    metric: str | None = None
    operator: str | None = None
    threshold: float | None = None
    window_minutes: int | None = None
    enabled: bool | None = None


class AlertRuleOut(BaseModel):
    id: UUID
    name: str
    metric: str
    operator: str
    threshold: float
    window_minutes: int
    enabled: bool
    created_at: datetime
    updated_at: datetime


class AlertRulesResponse(BaseModel):
    rules: list[AlertRuleOut]


class AlertEventOut(BaseModel):
    """One firing/resolved transition of a rule."""

    id: UUID
    rule_id: UUID
    rule_name: str | None
    status: str
    message: str
    value: float
    window_minutes: int
    triggered_at: datetime
    resolved_at: datetime | None


class AlertEventsResponse(BaseModel):
    events: list[AlertEventOut]


class TraceSessionOut(BaseModel):
    """One parsed agent session (CoPaw), as shown on the trace sessions page."""

    id: UUID
    session_id: str
    agent_name: str
    runtime: str
    source_key: str
    event_count: int
    first_seen_at: datetime
    parsed_at: datetime | None
    parsing_error: str | None
    object_mtime: datetime
    object_size: int


class TraceSessionsResponse(BaseModel):
    sessions: list[TraceSessionOut]
    #: Id of the last returned row; set only when more pages exist.
    next_cursor: UUID | None = None


class TraceEventOut(BaseModel):
    """One normalized event; a tool event merges its use/result blocks.

    ``agent_name`` / ``session_external_id`` are populated by the cross-session
    endpoint (a join) and stay ``None`` in the session-detail timeline.
    """

    id: UUID
    session_id: UUID
    seq: int
    ts: datetime
    event_type: str
    name: str
    role: str | None
    summary: str | None
    token_count: int | None
    latency_ms: int | None
    status: str
    payload: dict | None = None
    agent_name: str | None = None
    session_external_id: str | None = None


class TraceEventsResponse(BaseModel):
    events: list[TraceEventOut]
    #: Session detail pages key on the last event's seq; the global stream
    #: keys on the last event's id. Only the relevant one is set.
    next_seq: int | None = None
    next_cursor: UUID | None = None


class LogEntryOut(BaseModel):
    id: UUID
    ts: datetime
    level: str
    source: str
    issue_id: UUID | None = None
    message: str
    exc_info: str | None = None


class LogEntriesResponse(BaseModel):
    logs: list[LogEntryOut]
    next_cursor: UUID | None = None
