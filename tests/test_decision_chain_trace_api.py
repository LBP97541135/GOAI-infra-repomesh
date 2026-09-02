"""decision-chain v0.1 —— Phase 3 追溯 API 测试（审计场景走查）。

``GET /api/v1/decision-chains/{project_id}``：完整六节点链 + 证据指针 +
需求根（§6.1）、404（无链且无需求）、空链 200（有需求根但投影未跑）、
以及 action token 鉴权（缺配 503 / 无头或错 token 401）。种子复用
Phase 1/2 的 discovery 会话与 task/pr 事件，经 ``platform.audit_events``
投影出 6 个节点后走真实 API。
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID, uuid4

from api.test_issue_discovery import (
    ANALYSIS_OK,
    CANDIDATES,
    INTEGRATION,
    ScriptedLLM,
    _configure,
    _confirmation,
    _create_issue,
    _seed,
)
from fastapi.testclient import TestClient
from test_decision_chain_events import (
    _discovery_session,
    _observe_pr,
    _plan_task,
)

from repomesh.bootstrap.app import create_app
from repomesh.bootstrap.container import ApplicationContainer
from repomesh.modules.delivery import PostgresDeliveryAuditLog
from repomesh.settings import get_settings

_TOKEN = "audit-secret"
_HEADERS = {"Authorization": f"Bearer {_TOKEN}"}


def _client(container: ApplicationContainer, monkeypatch) -> TestClient:
    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", _TOKEN)
    get_settings.cache_clear()
    return TestClient(create_app(container))


def _seed_full_chain(
    container: ApplicationContainer, monkeypatch,
) -> tuple[UUID, UUID, str]:
    """Project a six-node chain into Postgres; returns (org, project, evidence)."""

    _configure(monkeypatch)
    organization_id, leader_id, _, _ = _seed(container)
    app = replace(
        container,
        llm_client=ScriptedLLM(
            ANALYSIS_OK,
            CANDIDATES,
            _confirmation("REQUIRED"),
            _confirmation("MAYBE"),
            INTEGRATION,
        ),
    )
    try:
        with _discovery_session(app, leader_id) as (project_id, evidence):
            tasks_planned, task_id = _plan_task(
                organization_id, project_id, leader_id
            )
            (observed_first, observed_second), _, _ = _observe_pr(
                organization_id, project_id, leader_id, task_id
            )
            audit = PostgresDeliveryAuditLog(app.database)
            for event in (tasks_planned, observed_first, observed_second):
                asyncio.run(audit.append(event))
            projection = app.decision_chain_projection_service()
            assert asyncio.run(projection.drain()) == 6
            return organization_id, project_id, evidence
    finally:
        get_settings.cache_clear()


def test_trace_api_requires_the_action_token(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    client = _client(application_container, monkeypatch)
    url = f"/api/v1/decision-chains/{uuid4()}?organization_id={uuid4()}"
    assert client.get(url).status_code == 401
    assert (
        client.get(url, headers={"Authorization": "Bearer wrong"}).status_code == 401
    )


def test_trace_api_fails_closed_without_a_configured_token(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    # The repo's .env hard-codes a token, so unsetting the OS variable is not
    # enough; patch the router's own get_settings reference to report none.
    import repomesh.modules.decision_chain.api.router as decision_chain_api

    def _no_token() -> SimpleNamespace:
        return SimpleNamespace(agent_action_token=None)

    monkeypatch.setattr(decision_chain_api, "get_settings", _no_token)
    client = TestClient(create_app(application_container))
    response = client.get(
        f"/api/v1/decision-chains/{uuid4()}?organization_id={uuid4()}"
    )
    assert response.status_code == 503


def test_trace_404_when_nothing_exists_for_the_project(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    _configure(monkeypatch)
    organization_id, _, _, _ = _seed(application_container)
    try:
        client = _client(application_container, monkeypatch)
        response = client.get(
            f"/api/v1/decision-chains/{uuid4()}?organization_id={organization_id}",
            headers=_HEADERS,
        )
        assert response.status_code == 404
    finally:
        get_settings.cache_clear()


def test_trace_returns_empty_chain_when_only_the_requirement_exists(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    """An issue created but not yet classified: 200, empty chain, root present."""

    _configure(monkeypatch)
    organization_id, leader_id, _, _ = _seed(application_container)
    try:
        with TestClient(create_app(application_container)) as client:
            issue_id = _create_issue(client, leader_id)
        project = UUID(issue_id)
        client = _client(application_container, monkeypatch)
        response = client.get(
            f"/api/v1/decision-chains/{project}?organization_id={organization_id}",
            headers=_HEADERS,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["nodes"] == []
        assert body["legacy_gaps"] == []
        assert body["requirement"]["plan_version"] == 1
        assert body["requirement"]["text"] == "订单完成后没有收到通知邮件"
    finally:
        get_settings.cache_clear()


def test_audit_walkthrough_returns_the_full_chain(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    """审计场景走查：输入需求 id → 完整链路（六节点、证据、需求根）。"""

    org, project, evidence = _seed_full_chain(application_container, monkeypatch)
    client = _client(application_container, monkeypatch)
    response = client.get(
        f"/api/v1/decision-chains/{project}?organization_id={org}",
        headers=_HEADERS,
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert UUID(body["project_id"]) == project
    assert UUID(body["organization_id"]) == org
    assert body["legacy_gaps"] == []

    # §6.1 链根：plan_version=1 快照自带需求文本。
    assert body["requirement"]["plan_version"] == 1
    assert body["requirement"]["text"]
    assert UUID(body["requirement"]["snapshot_id"])

    nodes = body["nodes"]
    assert [node["step"] for node in nodes] == [
        "classification",
        "confirmation",
        "integration",
        "task",
        "pr",
        "pr",
    ]
    assert all(node["source"] == "event" for node in nodes)
    assert all(node["event_type"] for node in nodes)

    by_step: dict[str, list[dict]] = {}
    for node in nodes:
        by_step.setdefault(node["step"], []).append(node)

    classification = by_step["classification"][0]
    confirmation = by_step["confirmation"][0]
    integration = by_step["integration"][0]
    task = by_step["task"][0]
    prs = by_step["pr"]

    # §4.1 链字段 + actor 映射。
    assert classification["upstream_ref"] is None
    assert classification["actor"]["type"] == "llm"
    assert classification["status"] == "proposed"
    assert classification["version"] == 1
    assert confirmation["upstream_ref"] == classification["decision_id"]
    assert confirmation["actor"]["type"] == "human"
    assert confirmation["status"] == "confirmed"
    assert integration["upstream_ref"] == confirmation["decision_id"]
    assert task["upstream_ref"] == integration["decision_id"]
    assert all(pr["actor"]["type"] == "service" for pr in prs)
    assert all(pr["actor"]["agent_id"] is None for pr in prs)
    assert all(pr["upstream_ref"] == task["decision_id"] for pr in prs)

    # §6.2 证据指针：分类/确认带 evidence_version 指纹。
    assert classification["evidence_refs"] == {
        "result": [evidence],
        "process": [],
    }
    assert confirmation["evidence_refs"] == {
        "result": [evidence],
        "process": [],
    }

    # §4.2 有效分档落在确认节点（分类保留 LLM 判定）。
    assert confirmation["payload_summary"]["effective_tiers"] == {
        "ts-notify": "REQUIRED",
        "ts-order": "MAYBE",
    }
