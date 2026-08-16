"""Console endpoints for LLM usage observability.

Mounts as ``/api/v1/observe/*`` (the ``api_router`` adds the version prefix).
Auth mirrors the other console-facing routers: a Bearer token equal to
``agent_action_token`` — the same token the frontend's API client already
sends as ``VITE_API_TOKEN``.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response

from repomesh.modules.observability.infrastructure.alerting import (
    SUPPORTED_METRICS,
    SUPPORTED_OPERATORS,
    AlertingEvaluator,
    AlertingStore,
)
from repomesh.modules.observability.infrastructure.log_query import (
    VALID_LOG_LEVELS,
    LogQueryStore,
)
from repomesh.modules.observability.infrastructure.trace_query import (
    VALID_EVENT_TYPES,
    VALID_STATUSES,
    TraceQueryStore,
)
from repomesh.modules.observability.infrastructure.usage_query import UsageQueryStore
from repomesh.settings import get_settings

from .models import (
    AlertEventOut,
    AlertEventsResponse,
    AlertRuleOut,
    AlertRulePayload,
    AlertRulesResponse,
    IssueLogGroupsResponse,
    LogEntriesResponse,
    ObserveIssueLogGroup,
    ObserveIssueRow,
    ObserveIssuesResponse,
    ObserveSummary,
    ObserveTraceIssueGroup,
    TraceEventsResponse,
    TraceIssueGroupsResponse,
    TraceSessionsResponse,
)

router = APIRouter(prefix="/observe", tags=["observability"])


def _authorized_container(request: Request):
    expected = get_settings().agent_action_token
    if not expected:
        raise HTTPException(
            status_code=503, detail="agent action token is not configured"
        )
    if request.headers.get("Authorization") != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="invalid agent action token")
    return request.app.state.container


def _service(request: Request) -> UsageQueryStore:
    return _authorized_container(request).usage_query_store()


def _alerting(request: Request) -> AlertingStore:
    return _authorized_container(request).alerting_store()


def _evaluator(request: Request) -> AlertingEvaluator:
    return _authorized_container(request).alerting_evaluator()


def _trace_query(request: Request) -> TraceQueryStore:
    return _authorized_container(request).trace_query_store()


def _log_query(request: Request) -> LogQueryStore:
    return _authorized_container(request).log_query_store()


@router.get("/summary", response_model=ObserveSummary)
async def observe_summary(
    request: Request, days: int = Query(default=7, ge=1, le=90)
) -> ObserveSummary:
    """System-level dashboard over the trailing ``days`` window."""
    data = await _service(request).summary(days=days)
    return ObserveSummary(**data)


@router.get("/issues", response_model=ObserveIssuesResponse)
async def observe_issues(request: Request) -> ObserveIssuesResponse:
    """Per-issue usage roll-up, most recently active first.

    The usage page's issue table is the consumer. Logs have their own
    issue grouping endpoint (``GET /observe/logs/issues``); trace sessions
    are task-keyed, so neither appears here.
    """
    rows = await _service(request).issues()
    return ObserveIssuesResponse(issues=[ObserveIssueRow(**row) for row in rows])


@router.get("/logs/issues", response_model=IssueLogGroupsResponse)
async def list_log_issue_groups(request: Request) -> IssueLogGroupsResponse:
    """Log lines grouped by issue, most recently active first."""
    groups = await _log_query(request).issue_groups()
    return IssueLogGroupsResponse(
        issues=[ObserveIssueLogGroup(**group) for group in groups]
    )


# --- Alerts ---------------------------------------------------------------


@router.get("/alert-rules", response_model=AlertRulesResponse)
async def list_alert_rules(request: Request) -> AlertRulesResponse:
    """All alert rules (disabled ones included)."""
    store = _alerting(request)
    await store.ensure_default_rules()
    rules = await store.list_rules()
    return AlertRulesResponse(rules=[AlertRuleOut(**r) for r in rules])


@router.post("/alert-rules", response_model=AlertRuleOut, status_code=201)
async def create_alert_rule(
    request: Request, payload: AlertRulePayload
) -> AlertRuleOut:
    """Create an alert rule."""
    name = (payload.name or "").strip()
    metric = payload.metric or ""
    operator = payload.operator or ""
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    if metric not in SUPPORTED_METRICS:
        raise HTTPException(status_code=422, detail=f"unsupported metric: {metric}")
    if operator not in SUPPORTED_OPERATORS:
        raise HTTPException(
            status_code=422, detail=f"unsupported operator: {operator}"
        )
    if payload.threshold is None:
        raise HTTPException(status_code=422, detail="threshold is required")
    rule = await _alerting(request).create_rule(
        name=name,
        metric=metric,
        operator=operator,
        threshold=payload.threshold,
        window_minutes=payload.window_minutes or 1440,
        enabled=True if payload.enabled is None else payload.enabled,
    )
    return AlertRuleOut(**rule)


@router.put("/alert-rules/{rule_id}", response_model=AlertRuleOut)
async def update_alert_rule(
    request: Request, rule_id: UUID, payload: AlertRulePayload
) -> AlertRuleOut:
    """Update an alert rule (name/metric/operator/threshold/window/enabled)."""
    if payload.metric is not None and payload.metric not in SUPPORTED_METRICS:
        raise HTTPException(status_code=422, detail=f"unsupported metric: {payload.metric}")
    if payload.operator is not None and payload.operator not in SUPPORTED_OPERATORS:
        raise HTTPException(
            status_code=422, detail=f"unsupported operator: {payload.operator}"
        )
    if payload.window_minutes is not None and payload.window_minutes < 1:
        raise HTTPException(status_code=422, detail="window_minutes must be >= 1")
    rule = await _alerting(request).update_rule(
        rule_id,
        **payload.model_dump(exclude_unset=True, exclude_none=True),
    )
    if rule is None:
        raise HTTPException(status_code=404, detail="alert rule not found")
    return AlertRuleOut(**rule)


@router.delete("/alert-rules/{rule_id}", status_code=204)
async def delete_alert_rule(request: Request, rule_id: UUID) -> Response:
    """Delete an alert rule (its events cascade)."""
    deleted = await _alerting(request).delete_rule(rule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="alert rule not found")
    return Response(status_code=204)


@router.get("/alerts", response_model=AlertEventsResponse)
async def list_alert_events(
    request: Request, days: int = Query(default=7, ge=1, le=90)
) -> AlertEventsResponse:
    """Firing/resolved alert history over the trailing ``days``."""
    events = await _alerting(request).list_events(days=days)
    return AlertEventsResponse(events=[AlertEventOut(**e) for e in events])


@router.get("/alerts/active", response_model=AlertEventsResponse)
async def list_active_alerts(request: Request) -> AlertEventsResponse:
    """Currently-firing, unresolved alerts."""
    events = await _alerting(request).list_active_events()
    return AlertEventsResponse(events=[AlertEventOut(**e) for e in events])


@router.post("/alerts/evaluate", response_model=AlertEventsResponse)
async def evaluate_alerts(request: Request) -> AlertEventsResponse:
    """Run one evaluation pass now (the console's "evaluate now")."""
    evaluator = _evaluator(request)
    await evaluator.evaluate_now()
    events = await _alerting(request).list_active_events()
    return AlertEventsResponse(events=[AlertEventOut(**e) for e in events])


# --- Traces ----------------------------------------------------------------


@router.get("/trace/issues", response_model=TraceIssueGroupsResponse)
async def list_trace_issue_groups(
    request: Request,
    limit: int = Query(default=100, ge=1, le=200),
) -> TraceIssueGroupsResponse:
    """Trace sessions grouped by issue via temporal overlap (approximate).

    Trace sessions are task-keyed, so each issue's activity window (usage ∪
    logs) is padded and matched against session first-seen times; the result
    is a heuristic, not a foreign key. Drilling into one group uses
    ``GET /observe/trace/sessions?issue_id=…``.
    """
    groups = await _trace_query(request).issue_groups(limit=limit)
    return TraceIssueGroupsResponse(
        issues=[ObserveTraceIssueGroup(**group) for group in groups]
    )


@router.get("/trace/sessions", response_model=TraceSessionsResponse)
async def list_trace_sessions(
    request: Request,
    agent_name: str | None = Query(default=None),
    issue_id: Annotated[UUID | None, Query()] = None,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: Annotated[UUID | None, Query()] = None,
) -> TraceSessionsResponse:
    """Parsed agent sessions, newest first (keyset-paginated).

    With ``issue_id`` the list is narrowed to sessions whose first-seen time
    falls inside that issue's inferred activity window (approximate).
    """
    if issue_id is not None:
        data = await _trace_query(request).sessions_for_issue(
            issue_id=issue_id, limit=limit, cursor=cursor
        )
    else:
        data = await _trace_query(request).list_sessions(
            agent_name=agent_name or None,
            limit=limit,
            cursor=cursor,
        )
    return TraceSessionsResponse(**data)


@router.get("/trace/sessions/{session_id}/events", response_model=TraceEventsResponse)
async def list_trace_session_events(
    request: Request,
    session_id: UUID,
    limit: int = Query(default=100, ge=1, le=500),
    after_seq: int = Query(default=0, ge=0),
) -> TraceEventsResponse:
    """One session's events in trace order, ``seq``-keyset paginated."""
    data = await _trace_query(request).session_events(
        session_id=session_id, limit=limit, after_seq=after_seq
    )
    if data is None:
        raise HTTPException(status_code=404, detail="trace session not found")
    return TraceEventsResponse(**data)


@router.get("/trace/events", response_model=TraceEventsResponse)
async def list_trace_events(
    request: Request,
    event_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    agent_name: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Annotated[UUID | None, Query()] = None,
) -> TraceEventsResponse:
    """Cross-session event stream, newest first, filterable."""
    if event_type is not None and event_type not in VALID_EVENT_TYPES:
        raise HTTPException(status_code=422, detail=f"unsupported event_type: {event_type}")
    if status is not None and status not in VALID_STATUSES:
        raise HTTPException(status_code=422, detail=f"unsupported status: {status}")
    data = await _trace_query(request).list_events(
        event_type=event_type,
        status=status,
        agent_name=agent_name or None,
        limit=limit,
        cursor=cursor,
    )
    return TraceEventsResponse(**data)


# --- Logs -----------------------------------------------------------------


@router.get("/logs", response_model=LogEntriesResponse)
async def list_log_entries(
    request: Request,
    level: str | None = Query(default=None),
    source: str | None = Query(default=None),
    issue_id: Annotated[UUID | None, Query()] = None,
    query: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Annotated[UUID | None, Query()] = None,
) -> LogEntriesResponse:
    """Unified process-log search, newest first (keyset-paginated).

    ``source`` and ``query`` are case-insensitive substrings; ``issue_id``
    narrows to lines attributed to one planning issue via
    ``extra={"issue_id": …}``.
    """
    if level is not None and level not in VALID_LOG_LEVELS:
        raise HTTPException(status_code=422, detail=f"unsupported level: {level}")
    data = await _log_query(request).list_logs(
        level=level or None,
        source=source or None,
        issue_id=issue_id,
        query=query or None,
        limit=limit,
        cursor=cursor,
    )
    return LogEntriesResponse(**data)
