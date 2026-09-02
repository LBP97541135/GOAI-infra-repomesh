"""decision-chain v0.1 —— Phase 4 相似检索 API 测试（走查）。

``GET /api/v1/decision-chains/{project_id}/similar``：同仓库命中并排除自身、
top_k 截断、空 hits 200（诚实数据）、action token 鉴权（缺配 503 / 无头或
错 token 401）、组织隔离。种子复用 Phase 3 的完整链（项目 A：ts-notify /
ts-order），peer 项目直接向 Postgres 存储投影分类节点（共享 ts-notify）。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

from api.test_issue_discovery import _configure, _seed
from fastapi.testclient import TestClient
from test_decision_chain_trace_api import _seed_full_chain

from repomesh.bootstrap.app import create_app
from repomesh.bootstrap.container import ApplicationContainer
from repomesh.modules.decision_chain import (
    DecisionStatus,
    DecisionStep,
    NodeActor,
    PostgresDecisionChainStore,
)
from repomesh.modules.decision_chain.contracts import DecisionNodeInput
from repomesh.settings import get_settings

_TOKEN = "audit-secret"
_HEADERS = {"Authorization": f"Bearer {_TOKEN}"}


def _client(container: ApplicationContainer, monkeypatch) -> TestClient:
    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", _TOKEN)
    get_settings.cache_clear()
    return TestClient(create_app(container))


def _peer_node(org: UUID, *, repos: list[str], at: datetime) -> DecisionNodeInput:
    """One classification sheet for a peer project (directly projected)."""
    return DecisionNodeInput(
        event_id=uuid4(),
        project_id=uuid4(),
        organization_id=org,
        step=DecisionStep.CLASSIFICATION,
        status=DecisionStatus.PROPOSED,
        actor=NodeActor(type="llm"),
        business_time=at,
        event_type="ClassificationDecided",
        evidence_refs={"result": ["sha256:peer"], "process": []},
        payload_summary={"required": list(repos)},
        affected_repository_ids=list(repos),
    )


def _seed_peer(
    container: ApplicationContainer, *, org: UUID, repos: list[str], at: datetime
) -> UUID:
    store = PostgresDecisionChainStore(container.database)
    view = asyncio.run(store.append(_peer_node(org, repos=repos, at=at)))
    return view.project_id


def test_similar_api_requires_the_action_token(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    client = _client(application_container, monkeypatch)
    url = f"/api/v1/decision-chains/{uuid4()}/similar?organization_id={uuid4()}"
    assert client.get(url).status_code == 401
    assert (
        client.get(url, headers={"Authorization": "Bearer wrong"}).status_code == 401
    )


def test_similar_api_fails_closed_without_a_configured_token(
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
        f"/api/v1/decision-chains/{uuid4()}/similar?organization_id={uuid4()}"
    )
    assert response.status_code == 503


def test_similar_returns_empty_hits_when_overlap_cannot_be_proven(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    """从未见过的项目（无链、无仓库信息）→ 200 + 空 hits（诚实数据）。"""

    _configure(monkeypatch)
    organization_id, _, _, _ = _seed(application_container)
    try:
        client = _client(application_container, monkeypatch)
        response = client.get(
            f"/api/v1/decision-chains/{uuid4()}/similar?organization_id={organization_id}",
            headers=_HEADERS,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["hits"] == []
        assert UUID(body["project_id"])
        assert UUID(body["organization_id"]) == organization_id
    finally:
        get_settings.cache_clear()


def test_similar_walkthrough_returns_sharing_projects(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    """走查：项目 A（ts-notify/ts-order）→ 带出共享 ts-notify 的 peer。"""

    org, project_a, _ = _seed_full_chain(application_container, monkeypatch)
    base = datetime(2026, 8, 28, 11, 0, tzinfo=UTC)
    peer_project = _seed_peer(
        application_container, org=org, repos=["ts-notify"], at=base
    )
    client = _client(application_container, monkeypatch)

    response = client.get(
        f"/api/v1/decision-chains/{project_a}/similar?organization_id={org}",
        headers=_HEADERS,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert UUID(body["project_id"]) == project_a
    assert UUID(body["organization_id"]) == org

    hits = body["hits"]
    assert [UUID(hit["project_id"]) for hit in hits] == [peer_project]
    hit = hits[0]
    assert hit["step"] == "classification"
    assert hit["status"] == "proposed"
    assert hit["affected_repository_ids"] == ["ts-notify"]
    assert UUID(hit["decision_id"])
    assert hit["payload_summary"]["required"] == ["ts-notify"]
    # 命中卡是需求级的：peer 没有需求快照 → requirement_text 为 None
    # （诚实缺口，卡头回退到决策单行），绝不从 payload 碎片里杜撰标题。
    assert hit["requirement_text"] is None

    # 反向走查（peer → 项目 A）：项目 A 有需求快照，命中必须带回需求根句。
    reverse = client.get(
        f"/api/v1/decision-chains/{peer_project}/similar?organization_id={org}",
        headers=_HEADERS,
    )
    assert reverse.status_code == 200, reverse.text
    reverse_hits = reverse.json()["hits"]
    assert [UUID(h["project_id"]) for h in reverse_hits] == [project_a]
    assert isinstance(reverse_hits[0]["requirement_text"], str)
    assert reverse_hits[0]["requirement_text"].strip()


def test_similar_respects_top_k_and_excludes_self(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    org, project_a, _ = _seed_full_chain(application_container, monkeypatch)
    base = datetime(2026, 8, 28, 11, 0, tzinfo=UTC)
    _seed_peer(application_container, org=org, repos=["ts-notify"], at=base)
    peer_c = _seed_peer(
        application_container,
        org=org,
        repos=["ts-notify"],
        at=base + timedelta(minutes=5),
    )
    client = _client(application_container, monkeypatch)

    response = client.get(
        f"/api/v1/decision-chains/{project_a}/similar?organization_id={org}&top_k=1",
        headers=_HEADERS,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # 最新的一条命中；项目 A 自身不出现。
    assert [UUID(hit["project_id"]) for hit in body["hits"]] == [peer_c]
    assert all(UUID(hit["project_id"]) != project_a for hit in body["hits"])


def test_similar_is_scoped_to_the_organization(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    org, project_a, _ = _seed_full_chain(application_container, monkeypatch)
    other_org = uuid4()
    _seed_peer(
        application_container,
        org=other_org,
        repos=["ts-notify"],
        at=datetime(2026, 8, 28, 11, 0, tzinfo=UTC),
    )
    client = _client(application_container, monkeypatch)

    response = client.get(
        f"/api/v1/decision-chains/{project_a}/similar?organization_id={org}",
        headers=_HEADERS,
    )
    assert response.status_code == 200, response.text
    assert response.json()["hits"] == []
