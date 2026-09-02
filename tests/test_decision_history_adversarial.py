"""对抗性测试审查：三个使用场景下历史决策功能的故障模式验证。

审查对象是"用户怎么用上历史决策"的两条真实消费路径：

- 场景 B：pipeline 自动注入（``_history_context`` → 相似历史进分类 prompt）
- 场景 C：similar API 主动查询（``mode=semantic`` + ``query_text``）

本文件验证"会不会出错"——把每个场景中最可能翻车的边界（数据空值、
provider 构造异常、空白输入）真实跑一遍，期望行为统一为：
历史检索是增强而非前置（fail-safe），任何异常都应降级而不是报错。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from test_decision_chain_embedding_api import (
    _FakeEmbeddings,
    _patch_embedding_client,
)
from test_decision_chain_similar_api import _HEADERS, _client
from test_decision_chain_trace_api import _seed_full_chain

from repomesh.bootstrap.container import ApplicationContainer
from repomesh.modules.repository_intelligence.application.discovery_chain import (
    _format_history_context,
)
from repomesh.modules.repository_intelligence.ports import SimilarDecisionSheet


def _sheet(*, business_time: datetime | None) -> SimilarDecisionSheet:
    return SimilarDecisionSheet(
        decision_id=uuid4(),
        project_id=uuid4(),
        step="confirmation",
        status="confirmed",
        affected_repository_ids=("ts-core-service",),
        payload_summary={},
        business_time=business_time,  # type: ignore[arg-type]
    )


# --- 场景 B：pipeline 注入 -------------------------------------------------
# 分类时历史上下文是增强而非前置：格式化/渲染绝不能中断分类流程。
# 审查发现：``_history_context`` 的 try 只包住了 port 调用，``_format_history_context``
# 在 try 之外 —— 若渲染抛异常，会直接打断 ``run_classification``。


def test_history_render_survives_missing_business_time() -> None:
    """B1: 缺失时间戳的相似历史必须仍可渲染（降级为 unknown 日期）。

    当前行为：``{None:%Y-%m-%d}`` 抛 TypeError —— 该调用在 fail-safe
    try 之外，会中断整个分类流程。
    """
    rendered = _format_history_context(
        [_sheet(business_time=datetime(2026, 8, 28, tzinfo=UTC))] + [_sheet(business_time=None)]
    )
    assert rendered is not None, "空/异常历史渲染 → None（无历史注入），绝不抛异常"
    assert "unknown" in rendered, "缺失日期渲染为 unknown，而不是中断分类"


def test_history_render_survives_missing_payload() -> None:
    """B2: payload_summary 非 dict（None/缺失）必须仍可渲染。"""
    sheet = replace(_sheet(business_time=datetime(2026, 8, 28, tzinfo=UTC)), payload_summary=None)  # type: ignore[arg-type]
    rendered = _format_history_context([sheet])
    assert rendered is not None, "payload 缺失 → None（无历史注入），绝不抛异常"


# --- 场景 C：similar API ---------------------------------------------------
# fail-safe 契约：没有有效查询文本、embedding provider 构造失败、embedding
# 调用失败，都必须如实回退 structural，而不是 500 或真实调用。


def test_semantic_whitespace_query_text_does_not_call_embeddings(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    """C1: 空白 ``query_text`` 等价于无查询 —— 不得触发真实 embedding 调用。

    当前行为：``_semantic_hits`` 只查 ``not query_text``，空白字符串通过检查，
    会对垃圾输入真实调用 provider（计费 + 返回无意义排名）。
    """
    org, project_a, _ = _seed_full_chain(application_container, monkeypatch)
    fake = _FakeEmbeddings([1.0, 0.0])
    _patch_embedding_client(monkeypatch, fake)
    client = _client(application_container, monkeypatch)

    response = client.get(
        f"/api/v1/decision-chains/{project_a}/similar"
        f"?organization_id={org}&mode=semantic&query_text=%20%20",
        headers=_HEADERS,
    )
    assert response.status_code == 200, response.text
    assert fake.calls == [], "空白 query_text 不应触发任何 embedding 调用"
    assert response.json()["mode"] == "structural", "空白查询 → 回退 structural"


def test_semantic_survives_embedding_client_construction_error(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    """C2: embedding provider 构造失败（``embedding_client()`` 抛异常）→ 回退。

    当前行为：``container.embedding_client()`` 在 ``_semantic_hits`` 的 try 之外，
    provider 构造异常直接变成 500。
    """
    org, project_a, _ = _seed_full_chain(application_container, monkeypatch)

    def _boom(self: ApplicationContainer) -> object:  # noqa: ANN001
        raise RuntimeError("provider configuration invalid")

    monkeypatch.setattr(ApplicationContainer, "embedding_client", _boom)
    client = _client(application_container, monkeypatch)

    response = client.get(
        f"/api/v1/decision-chains/{project_a}/similar"
        f"?organization_id={org}&mode=semantic&query_text=fix+email",
        headers=_HEADERS,
    )
    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "structural", "provider 构造异常 → 回退 structural"


def test_semantic_survives_empty_embedding_response(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    """C3: provider 对非空输入返回空向量列表 → 回退，不得 IndexError 500。"""

    class _EmptyEmbeddings:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            return []

    org, project_a, _ = _seed_full_chain(application_container, monkeypatch)
    _patch_embedding_client(monkeypatch, _EmptyEmbeddings())
    client = _client(application_container, monkeypatch)

    response = client.get(
        f"/api/v1/decision-chains/{project_a}/similar"
        f"?organization_id={org}&mode=semantic&query_text=fix+email",
        headers=_HEADERS,
    )
    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "structural", "空 embedding 响应 → 回退 structural"


def test_semantic_mode_degradation_never_hides_scores_from_structural(
    application_container: ApplicationContainer, monkeypatch,
) -> None:
    """C4: 语义失败回退后，结构命中的 ``score`` 必须为 None（诚实标注）。"""
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
    assert body["mode"] == "structural"
    assert all(hit["score"] is None for hit in body["hits"]), (
        "结构回退不携带 score（语义分数才有意义）"
    )
