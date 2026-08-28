"""decision-chain v0.1 —— L3 语义 API 测试（similar mode + embeddings refresh）。

- ``mode=semantic`` 无 embedding 端点 / 无 ``query_text`` → 回退 structural，
  响应 ``mode`` 字段如实报告实际服务的模式（fail-safe）。
- ``mode=semantic`` 配置 fake 客户端 + 已嵌入 peer → 带 ``score`` 的语义命中。
- ``POST /decision-chains/embeddings/refresh``：未配置 → 0；配置 → 回填计数且
  幂等（第二次 0）；鉴权与 similar/trace 端点一致（缺配 503 / 错 token 401）。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from test_decision_chain_similar_api import _HEADERS, _client
from test_decision_chain_trace_api import _seed_full_chain

from repomesh.bootstrap.container import ApplicationContainer
from repomesh.modules.decision_chain import (
    DecisionStatus,
    DecisionStep,
    NodeActor,
    PostgresDecisionChainStore,
    PostgresDecisionEmbeddingStore,
)
from repomesh.modules.decision_chain.contracts import DecisionNodeInput


def _patch_embedding_client(monkeypatch, client) -> None:
    """Patch the container's provider at class level (slots dataclass: instance
    attribute assignment is rejected)."""
    monkeypatch.setattr(
        ApplicationContainer, "embedding_client", lambda self: client
    )


class _FakeEmbeddings:
    """固定向量：查询与 peer 向量一致 → score 1.0，便于断言。"""

    def __init__(self, vector: list[float]) -> None:
        self._vector = vector
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [list(self._vector) for _ in texts]


def _peer_node(org: UUID, *, repos: list[str]) -> DecisionNodeInput:
    return DecisionNodeInput(
        event_id=uuid4(),
        project_id=uuid4(),
        organization_id=org,
        step=DecisionStep.CLASSIFICATION,
        status=DecisionStatus.PROPOSED,
        actor=NodeActor(type="llm"),
        business_time=datetime(2026, 8, 28, 11, 0, tzinfo=UTC),
        event_type="ClassificationDecided",
        evidence_refs={"result": ["sha256:peer"], "process": []},
        payload_summary={"required": list(repos)},
        affected_repository_ids=list(repos),
    )


def _embed_peer(
    container: ApplicationContainer, *, org: UUID, repos: list[str], vector: list[float]
) -> tuple[UUID, UUID]:
    """Project one peer sheet into Postgres and store its vector."""
    chain_store = PostgresDecisionChainStore(container.database)
    embed_store = PostgresDecisionEmbeddingStore(container.database)
    view = asyncio.run(chain_store.append(_peer_node(org, repos=repos)))
    asyncio.run(embed_store.upsert(view.decision_id, vector))
    return view.project_id, view.decision_id


def test_semantic_mode_falls_back_to_structural_without_embedding(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    org, project_a, _ = _seed_full_chain(application_container, monkeypatch)
    _patch_embedding_client(monkeypatch, None)
    client = _client(application_container, monkeypatch)

    response = client.get(
        f"/api/v1/decision-chains/{project_a}/similar"
        f"?organization_id={org}&mode=semantic&query_text=fix+email",
        headers=_HEADERS,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "structural", "未配置 embedding → 如实回退 structural"


def test_semantic_mode_without_query_text_falls_back_to_structural(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    org, project_a, _ = _seed_full_chain(application_container, monkeypatch)
    vector = [1.0, 0.0]
    _embed_peer(application_container, org=org, repos=["ts-notify"], vector=vector)
    _patch_embedding_client(monkeypatch, _FakeEmbeddings(vector))
    client = _client(application_container, monkeypatch)

    response = client.get(
        f"/api/v1/decision-chains/{project_a}/similar"
        f"?organization_id={org}&mode=semantic",
        headers=_HEADERS,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "structural", "无 query_text → 回退 structural"
    assert body["hits"], "结构回退仍给出同仓库命中"


def test_semantic_mode_returns_scored_hits_when_configured(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    org, project_a, _ = _seed_full_chain(application_container, monkeypatch)
    vector = [1.0, 0.0, 0.0, 0.0]
    peer_project, peer_decision = _embed_peer(
        application_container, org=org, repos=["ts-notify"], vector=vector
    )
    _patch_embedding_client(monkeypatch, _FakeEmbeddings(vector))
    client = _client(application_container, monkeypatch)

    response = client.get(
        f"/api/v1/decision-chains/{project_a}/similar"
        f"?organization_id={org}&mode=semantic&query_text=fix+email",
        headers=_HEADERS,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "semantic"
    hits = body["hits"]
    assert [UUID(hit["project_id"]) for hit in hits] == [peer_project]
    assert hits[0]["decision_id"] == str(peer_decision)
    assert hits[0]["score"] == pytest.approx(1.0)
    assert all(hit["score"] is not None for hit in hits)


def test_semantic_mode_degrades_on_embedding_error(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    org, project_a, _ = _seed_full_chain(application_container, monkeypatch)

    class _BrokenEmbeddings:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("endpoint down")

    _patch_embedding_client(monkeypatch, _BrokenEmbeddings())
    client = _client(application_container, monkeypatch)

    response = client.get(
        f"/api/v1/decision-chains/{project_a}/similar"
        f"?organization_id={org}&mode=semantic&query_text=fix+email",
        headers=_HEADERS,
    )
    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "structural", "embedding 异常 → 回退且不报错"


def test_refresh_endpoint_requires_the_action_token(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    client = _client(application_container, monkeypatch)
    url = "/api/v1/decision-chains/embeddings/refresh"
    assert client.post(url).status_code == 401
    assert (
        client.post(url, headers={"Authorization": "Bearer wrong"}).status_code == 401
    )


def test_refresh_endpoint_reports_zero_without_embedding(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    org, project_a, _ = _seed_full_chain(application_container, monkeypatch)
    _patch_embedding_client(monkeypatch, None)
    client = _client(application_container, monkeypatch)

    response = client.post("/api/v1/decision-chains/embeddings/refresh", headers=_HEADERS)
    assert response.status_code == 200, response.text
    assert response.json() == {"refreshed": 0}, "未配置 embedding → 诚实报告 no-op"


def test_refresh_endpoint_embeds_pending_when_configured(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    org, project_a, _ = _seed_full_chain(application_container, monkeypatch)
    _embed_peer(application_container, org=org, repos=["ts-notify"], vector=[1.0, 0.0])
    _patch_embedding_client(monkeypatch, _FakeEmbeddings([1.0, 0.0]))
    client = _client(application_container, monkeypatch)

    first = client.post("/api/v1/decision-chains/embeddings/refresh", headers=_HEADERS)
    assert first.status_code == 200, first.text
    assert first.json()["refreshed"] >= 1, "未嵌入的决策单被回填"

    second = client.post("/api/v1/decision-chains/embeddings/refresh", headers=_HEADERS)
    assert second.json()["refreshed"] == 0, "幂等：第二次无待嵌入节点"
