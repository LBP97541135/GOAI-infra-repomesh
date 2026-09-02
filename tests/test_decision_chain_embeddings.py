"""decision-chain v0.1 —— L3 语义检索测试（客户端 / 文本 / 余弦 / 服务 / 适配器）。

L3 引入向量检索（落地方案 B8：写路径永不调用 embedding，查询向量在读时
生成）。本文件覆盖四个层次：

- 客户端 ``integrations.llm.embeddings``：无 base_url 禁用语义、OpenAI 兼容
  ``POST /embeddings`` 调用形态（model/input/Bearer）、``data[].index`` 排序
  保证输入顺序、provider 错误上抛（由混合适配器捕获回退）。
- 纯函数 ``_text_for``（5 步确定性摘要）与 ``_cosine``（空/长度守卫）。
- 应用服务 ``DecisionEmbeddingService``（批量回填、幂等、分批）与
  ``DecisionChainSemanticSearchService``（项目折叠、排除自身、余弦排序、同仓
  硬过滤、top_k、组织隔离）。
- 混合适配器 ``DecisionHistoryVectorStore``——query_text 缺失 / embedding 失败
  / 语义空命中三种路径回退结构化（Phase 4b fail-safe 延续）。

末尾用 Postgres 孪生核对 embedding 存储的读写语义与内存一致。
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from test_decision_chain_projection import (
    _chain_events,
    _classification_event,
    _store_services,
    _task_event,
)

from repomesh.integrations.llm import embeddings as embeddings_module
from repomesh.integrations.llm.embeddings import (
    EmbeddingConfig,
    OpenAICompatibleEmbeddings,
    make_embedding_client,
)
from repomesh.modules.decision_chain import (
    DecisionChainSemanticSearchService,
    DecisionEmbeddingService,
    InMemoryDecisionEmbeddingStore,
)
from repomesh.modules.decision_chain.application import _cosine, _text_for
from repomesh.modules.decision_chain.contracts import (
    DecisionChainSummaryView,
    DecisionNodeInput,
    DecisionNodeView,
    DecisionStatus,
    DecisionStep,
    NodeActor,
    NodeSource,
    SemanticDecisionHit,
)
from repomesh.modules.repository_intelligence.infrastructure import (
    DecisionHistoryVectorStore,
)
from repomesh.modules.repository_intelligence.ports import SimilarDecisionSheet

# --- 构造辅助 ---------------------------------------------------------------


def _node_view(
    *,
    step: DecisionStep = DecisionStep.CLASSIFICATION,
    status: DecisionStatus = DecisionStatus.PROPOSED,
    repos: list[str] | None = None,
    payload: dict | None = None,
    at: datetime | None = None,
    version: int = 1,
) -> DecisionNodeView:
    business_time = at or datetime(2026, 8, 28, 9, 0, tzinfo=UTC)
    return DecisionNodeView(
        decision_id=uuid4(),
        event_id=uuid4(),
        project_id=uuid4(),
        organization_id=uuid4(),
        step=step,
        version=version,
        status=status,
        actor=NodeActor(type="llm", agent_id=str(uuid4())),
        upstream_ref=None,
        evidence_refs={"result": ["sha256:test"], "process": []},
        payload_summary=payload or {},
        affected_repository_ids=list(repos or ["ts-notify"]),
        business_time=business_time,
        recorded_at=business_time,
        source=NodeSource.EVENT,
        event_type=f"{step.value}Decided",
    )


def _classified(
    org: UUID, project: UUID, leader: UUID, *, repos: list[str], at: datetime
) -> object:
    """A classification event whose repositories differ from the shared fixture."""
    event = _classification_event(org, project, leader, at=at)
    event.payload["classification"]["required"] = list(repos)
    event.payload["affected_repository_ids"] = list(repos)
    return event


class _FakeEmbeddings:
    """Deterministic per-text vector; records every call for batch assertions."""

    def __init__(self, vector_size: int = 4) -> None:
        self._size = vector_size
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [
            [float((hash(text) + offset) % 100) / 100.0 for offset in range(self._size)]
            for text in texts
        ]


class _EchoEmbeddings:
    """Fixed-vector embedding for deterministic cosine assertions."""

    def __init__(self, vector: list[float]) -> None:
        self._vector = vector

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(self._vector) for _ in texts]


class _FailingEmbeddings:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embedding endpoint down")


class _RecordingSemantic:
    def __init__(self, hits: list[SemanticDecisionHit]) -> None:
        self._hits = hits
        self.raise_on_call = False
        self.calls: list[dict] = []

    async def find_similar(
        self,
        *,
        organization_id: UUID,
        project_id: UUID,
        query_embedding: list[float],
        top_k: int = 5,
        same_repository_ids: tuple[str, ...] = (),
    ) -> list[SemanticDecisionHit]:
        self.calls.append(
            {
                "organization_id": organization_id,
                "project_id": project_id,
                "query_embedding": query_embedding,
                "top_k": top_k,
                "same_repository_ids": same_repository_ids,
            }
        )
        if self.raise_on_call:
            raise RuntimeError("semantic service unavailable")
        return self._hits


class _RecordingStructural:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def find_similar(
        self,
        *,
        organization_id: UUID,
        project_id: UUID,
        repository_ids: tuple[str, ...],
        top_k: int = 5,
    ) -> list[SimilarDecisionSheet]:
        self.calls.append({"repository_ids": repository_ids, "top_k": top_k})
        return [
            SimilarDecisionSheet(
                decision_id=uuid4(),
                project_id=project_id,
                step="classification",
                status="proposed",
                affected_repository_ids=tuple(repository_ids),
                payload_summary={},
                business_time=datetime(2026, 8, 28, 9, 0, tzinfo=UTC),
            )
        ]


def _semantic_hit(
    *,
    project: UUID,
    repos: list[str],
    at: datetime,
    score: float = 0.9,
) -> SemanticDecisionHit:
    return SemanticDecisionHit(
        score=score,
        decision=DecisionChainSummaryView(
            decision_id=uuid4(),
            project_id=project,
            organization_id=uuid4(),
            step=DecisionStep.CLASSIFICATION,
            version=1,
            status=DecisionStatus.PROPOSED,
            affected_repository_ids=list(repos),
            payload_summary={"required": list(repos)},
            business_time=at,
        ),
    )


# --- 客户端（integrations.llm.embeddings） ---------------------------------


def test_make_embedding_client_disables_semantic_without_base_url() -> None:
    assert make_embedding_client(None) is None
    assert make_embedding_client("") is None
    client = make_embedding_client("http://llm.local/v1", api_key="k", model="m")
    assert isinstance(client, OpenAICompatibleEmbeddings)


async def test_embed_posts_openai_shape_and_preserves_input_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0]},
                    {"index": 0, "embedding": [1.0, 0.0]},
                ]
            },
        )

    transport = httpx.MockTransport(_handler)

    class _AsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(embeddings_module.httpx, "AsyncClient", _AsyncClient)
    client = OpenAICompatibleEmbeddings(
        EmbeddingConfig(base_url="http://llm.local/v1", api_key="secret", model="m")
    )

    vectors = await client.embed(["a", "b"])
    assert vectors == [[1.0, 0.0], [0.0, 1.0]], "按 data[].index 排序回填输入顺序"
    assert captured["url"] == "http://llm.local/v1/embeddings"
    assert captured["body"] == {"model": "m", "input": ["a", "b"]}
    assert captured["auth"] == "Bearer secret"


async def test_embed_raises_on_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(500, text="provider boom")
    )

    class _AsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(embeddings_module.httpx, "AsyncClient", _AsyncClient)
    client = OpenAICompatibleEmbeddings(
        EmbeddingConfig(base_url="http://llm.local/v1", api_key=None)
    )
    with pytest.raises(httpx.HTTPStatusError):
        await client.embed(["a"])


# --- 纯函数：_text_for / _cosine -------------------------------------------


def test_text_for_is_deterministic_and_carries_step_status_and_repos() -> None:
    node = _node_view(
        repos=["ts-notify", "ts-order"],
        payload={
            "required": ["ts-notify"],
            "maybe": ["ts-order"],
            "excluded": [],
        },
    )
    text = _text_for(node)
    assert text == _text_for(node), "同一决策单重复嵌入必须产生同一文本"
    assert "classification" in text
    assert "proposed" in text
    assert "ts-notify" in text and "ts-order" in text
    assert "required" in text and "maybe" in text


def test_text_for_covers_all_five_steps() -> None:
    payloads = {
        DecisionStep.CLASSIFICATION: {"required": ["r"], "maybe": [], "excluded": []},
        DecisionStep.CONFIRMATION: {"approval": {"state": "approved"}, "adjustments": []},
        DecisionStep.INTEGRATION: {"contracts": ["c1"]},
        DecisionStep.TASK: {"title": "改通知模板"},
        DecisionStep.PR: {
            "pull_request_number": 42,
            "pull_request_url": "https://example.test/pr/42",
        },
    }
    texts = {
        step: _text_for(_node_view(step=step, payload=payload))
        for step, payload in payloads.items()
    }
    assert len(set(texts.values())) == 5, "五个 step 的检索文本必须可区分"
    assert "approved" in texts[DecisionStep.CONFIRMATION]
    assert "c1" in texts[DecisionStep.INTEGRATION]
    assert "改通知模板" in texts[DecisionStep.TASK]
    assert "42" in texts[DecisionStep.PR]


def test_cosine_guards_empty_and_mismatched_vectors() -> None:
    assert _cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert _cosine([], [1.0, 0.0]) == 0.0
    assert _cosine([1.0, 0.0], []) == 0.0
    assert _cosine([1.0], [1.0, 0.0]) == 0.0


# --- DecisionEmbeddingService（B8 批量回填） --------------------------------


async def test_refresh_embeds_pending_and_is_idempotent() -> None:
    org, leader = uuid4(), uuid4()
    project_a = uuid4()
    projection, chain_store = _store_services(_chain_events(org, project_a, leader))
    await projection.drain()
    embed_store = InMemoryDecisionEmbeddingStore(chain_store)
    fake = _FakeEmbeddings()
    service = DecisionEmbeddingService(embed_store, fake)

    assert await service.refresh() == 5
    assert [len(batch) for batch in fake.calls] == [5], "默认 batch_size=16 → 单批"
    assert await service.refresh() == 0, "再次刷新无待嵌入节点 → 幂等 0"

    embedded = await embed_store.embedded_nodes(organization_id=org)
    assert len(embedded) == 5
    assert all(len(entry.embedding) == 4 for entry in embedded)


async def test_refresh_batches_calls_when_batch_size_is_small() -> None:
    org, leader = uuid4(), uuid4()
    project_a, project_b = uuid4(), uuid4()
    base = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)
    projection, chain_store = _store_services(
        _chain_events(org, project_a, leader)
        + [_classified(org, project_b, leader, repos=["ts-notify"], at=base)]
    )
    await projection.drain()
    embed_store = InMemoryDecisionEmbeddingStore(chain_store)
    fake = _FakeEmbeddings()
    service = DecisionEmbeddingService(embed_store, fake, batch_size=2)

    assert await service.refresh() == 6
    assert [len(batch) for batch in fake.calls] == [2, 2, 2]


# --- DecisionChainSemanticSearchService -------------------------------------


async def test_semantic_ranks_by_cosine_collapses_projects_and_excludes_self() -> None:
    org, leader = uuid4(), uuid4()
    project_a, peer_b, peer_c = uuid4(), uuid4(), uuid4()
    base = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)
    projection, chain_store = _store_services(
        _chain_events(org, project_a, leader)
        + [
            _classified(org, peer_b, leader, repos=["ts-notify"], at=base),
            _classified(org, peer_c, leader, repos=["ts-notify"], at=base),
        ]
    )
    await projection.drain()

    embed_store = InMemoryDecisionEmbeddingStore(chain_store)
    nodes = list(chain_store._nodes.values())  # noqa: SLF001 (test twin)
    by_project: dict[UUID, DecisionNodeView] = {
        node.project_id: node for node in nodes
    }
    query = [1.0, 0.0, 0.0, 0.0]
    # B 与查询同向（score 1.0），C 正交；项目 A 自身也嵌入但必须被排除。
    await embed_store.upsert(by_project[peer_b].decision_id, [0.99, 0.0, 0.0, 0.0])
    await embed_store.upsert(by_project[peer_c].decision_id, [0.0, 1.0, 0.0, 0.0])
    await embed_store.upsert(by_project[project_a].decision_id, query)

    service = DecisionChainSemanticSearchService(embed_store)
    hits = await service.find_similar(
        organization_id=org, project_id=project_a, query_embedding=query
    )
    assert [hit.decision.project_id for hit in hits] == [peer_b, peer_c]
    assert hits[0].score == pytest.approx(1.0)
    assert hits[0].decision.step == DecisionStep.CLASSIFICATION
    assert all(hit.decision.project_id != project_a for hit in hits)


async def test_semantic_collapses_each_project_to_its_latest_sheet() -> None:
    org, leader = uuid4(), uuid4()
    project_a, peer = uuid4(), uuid4()
    base = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)
    task_event, _ = _task_event(org, peer, leader, at=base + timedelta(minutes=10))
    projection, chain_store = _store_services(
        _chain_events(org, project_a, leader)
        + [_classification_event(org, peer, leader, at=base), task_event]
    )
    await projection.drain()

    embed_store = InMemoryDecisionEmbeddingStore(chain_store)
    for node in chain_store._nodes.values():  # noqa: SLF001 (test twin)
        await embed_store.upsert(node.decision_id, [1.0, 0.0, 0.0])

    service = DecisionChainSemanticSearchService(embed_store)
    hits = await service.find_similar(
        organization_id=org, project_id=project_a, query_embedding=[1.0, 0.0, 0.0]
    )
    assert len(hits) == 1, "每个其他项目只贡献一条决策单"
    assert hits[0].decision.project_id == peer
    assert hits[0].decision.step == DecisionStep.TASK, "折叠取业务时间最新的节点"


async def test_semantic_hard_filters_candidates_by_repository_scope() -> None:
    org, leader = uuid4(), uuid4()
    project_a, peer_b, peer_c = uuid4(), uuid4(), uuid4()
    base = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)
    projection, chain_store = _store_services(
        _chain_events(org, project_a, leader)
        + [
            _classified(org, peer_b, leader, repos=["ts-notify"], at=base),
            _classified(org, peer_c, leader, repos=["payments-core"], at=base),
        ]
    )
    await projection.drain()

    embed_store = InMemoryDecisionEmbeddingStore(chain_store)
    for node in chain_store._nodes.values():  # noqa: SLF001 (test twin)
        await embed_store.upsert(node.decision_id, [1.0, 0.0, 0.0])

    service = DecisionChainSemanticSearchService(embed_store)
    hits = await service.find_similar(
        organization_id=org,
        project_id=project_a,
        query_embedding=[1.0, 0.0, 0.0],
        same_repository_ids=("ts-notify",),
    )
    assert [hit.decision.project_id for hit in hits] == [peer_b], "未触碰的仓库不得进入候选"


async def test_semantic_is_scoped_to_organization_and_bounded_by_top_k() -> None:
    org_a, org_b = uuid4(), uuid4()
    leader = uuid4()
    project_a, peer_b, peer_other_org = uuid4(), uuid4(), uuid4()
    base = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)
    projection, chain_store = _store_services(
        _chain_events(org_a, project_a, leader)
        + [
            _classified(org_a, peer_b, leader, repos=["ts-notify"], at=base),
            _classified(org_b, peer_other_org, leader, repos=["ts-notify"], at=base),
        ]
    )
    await projection.drain()

    embed_store = InMemoryDecisionEmbeddingStore(chain_store)
    for node in chain_store._nodes.values():  # noqa: SLF001 (test twin)
        await embed_store.upsert(node.decision_id, [1.0, 0.0, 0.0])

    service = DecisionChainSemanticSearchService(embed_store)
    hits = await service.find_similar(
        organization_id=org_a, project_id=project_a, query_embedding=[1.0, 0.0, 0.0]
    )
    assert [hit.decision.project_id for hit in hits] == [peer_b], "另一个组织的项目不得进入结果"
    bounded = await service.find_similar(
        organization_id=org_a,
        project_id=project_a,
        query_embedding=[1.0, 0.0, 0.0],
        top_k=0,
    )
    assert bounded == []


# --- DecisionHistoryVectorStore（混合适配器） -------------------------------


async def test_vector_store_falls_back_to_structural_without_query_text() -> None:
    semantic = _RecordingSemantic([])
    structural = _RecordingStructural()
    store = DecisionHistoryVectorStore(
        semantic, _FailingEmbeddings(), structural=structural
    )

    sheets = await store.find_similar(
        organization_id=uuid4(),
        project_id=uuid4(),
        repository_ids=("ts-notify",),
        query_text=None,
    )
    assert len(structural.calls) == 1
    assert semantic.calls == [], "无查询文本 → 不触碰语义路径"
    assert len(sheets) == 1
    assert sheets[0].affected_repository_ids == ("ts-notify",)


async def test_vector_store_falls_back_when_embedding_fails() -> None:
    semantic = _RecordingSemantic([])
    structural = _RecordingStructural()
    store = DecisionHistoryVectorStore(
        semantic, _FailingEmbeddings(), structural=structural
    )

    sheets = await store.find_similar(
        organization_id=uuid4(),
        project_id=uuid4(),
        repository_ids=("ts-notify",),
        query_text="fix email",
    )
    assert len(structural.calls) == 1
    assert semantic.calls == [], "embedding 失败 → 回退，不进入语义检索"
    assert len(sheets) == 1


async def test_vector_store_falls_back_when_semantic_raises() -> None:
    semantic = _RecordingSemantic([])
    semantic.raise_on_call = True
    structural = _RecordingStructural()
    store = DecisionHistoryVectorStore(
        semantic, _EchoEmbeddings([1.0]), structural=structural
    )

    sheets = await store.find_similar(
        organization_id=uuid4(),
        project_id=uuid4(),
        repository_ids=("ts-notify",),
        query_text="fix email",
    )
    assert len(structural.calls) == 1, "语义检索异常 → 回退，绝不阻塞分类"
    assert len(semantic.calls) == 1
    assert len(sheets) == 1


async def test_vector_store_falls_back_when_semantic_has_no_hits() -> None:
    semantic = _RecordingSemantic([])
    structural = _RecordingStructural()
    store = DecisionHistoryVectorStore(
        semantic, _EchoEmbeddings([1.0]), structural=structural
    )

    sheets = await store.find_similar(
        organization_id=uuid4(),
        project_id=uuid4(),
        repository_ids=("ts-notify",),
        query_text="fix email",
    )
    assert len(structural.calls) == 1, "语义空命中仍给结构重叠答案"
    assert len(semantic.calls) == 1
    assert len(sheets) == 1


async def test_vector_store_returns_semantic_sheets_on_hit() -> None:
    org, project = uuid4(), uuid4()
    at = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)
    hit = _semantic_hit(project=uuid4(), repos=["ts-notify"], at=at, score=0.91)
    semantic = _RecordingSemantic([hit])
    structural = _RecordingStructural()
    store = DecisionHistoryVectorStore(
        semantic, _EchoEmbeddings([1.0]), structural=structural
    )

    sheets = await store.find_similar(
        organization_id=org,
        project_id=project,
        repository_ids=("ts-notify",),
        query_text="fix email",
    )
    assert len(sheets) == 1
    assert sheets[0].decision_id == hit.decision.decision_id
    assert structural.calls == [], "语义命中时不调用结构回退"
    assert semantic.calls[0]["same_repository_ids"] == ("ts-notify",)
    assert semantic.calls[0]["query_embedding"] == [1.0]


async def test_vector_store_without_structural_returns_empty_on_fallback() -> None:
    store = DecisionHistoryVectorStore(_RecordingSemantic([]), _FailingEmbeddings())
    sheets = await store.find_similar(
        organization_id=uuid4(),
        project_id=uuid4(),
        repository_ids=("ts-notify",),
        query_text=None,
    )
    assert sheets == [], "无结构适配器可回退 → 诚实返回无历史"


# --- Postgres 孪生核对 -------------------------------------------------------


def _node(org: UUID, project: UUID, *, repos: list[str], at: datetime) -> DecisionNodeInput:
    return DecisionNodeInput(
        event_id=uuid4(),
        project_id=project,
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


def test_embedding_store_on_postgres_matches_memory_semantics(
    application_container,
) -> None:
    from repomesh.modules.decision_chain import (
        PostgresDecisionChainStore,
        PostgresDecisionEmbeddingStore,
    )

    chain_store = PostgresDecisionChainStore(application_container.database)
    embed_store = PostgresDecisionEmbeddingStore(application_container.database)
    org = uuid4()
    project = uuid4()
    base = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)
    first = asyncio.run(
        chain_store.append(_node(org, project, repos=["ts-notify"], at=base))
    )
    second = asyncio.run(
        chain_store.append(_node(org, project, repos=["ts-notify"], at=base))
    )

    pending = asyncio.run(embed_store.pending_nodes())
    assert {node.decision_id for node in pending} == {
        first.decision_id,
        second.decision_id,
    }

    asyncio.run(embed_store.upsert(first.decision_id, [1.0, 0.0]))
    pending_after = asyncio.run(embed_store.pending_nodes())
    assert [node.decision_id for node in pending_after] == [second.decision_id]

    embedded = asyncio.run(embed_store.embedded_nodes(organization_id=org))
    assert [entry.node.decision_id for entry in embedded] == [first.decision_id]
    assert embedded[0].embedding == [1.0, 0.0]

    # 幂等 upsert：同 decision_id 覆盖而非新增。
    asyncio.run(embed_store.upsert(first.decision_id, [0.0, 1.0]))
    embedded = asyncio.run(embed_store.embedded_nodes(organization_id=org))
    assert [entry.node.decision_id for entry in embedded] == [first.decision_id]
    assert embedded[0].embedding == [0.0, 1.0]
