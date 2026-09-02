"""decision-chain v0.1 —— Phase 4 相似决策检索测试（存储 + 服务层）。

Q6 裁决：同仓库 + 最近 N 条起步。``find_similar_structural`` 的存储实现
随 Phase 2 落盘但无测试覆盖，本文件补齐其行为：同仓库命中、排除自身、
每个项目折叠为最新决策单、按业务时间倒序、无法证明共享返回空（红线 7
的诚实延伸）、显式仓库作用域（全新需求场景）、服务层 top_k 截断、
组织隔离；末尾用 Postgres 存储核对与内存孪生语义一致。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from test_decision_chain_projection import (
    _chain_events,
    _classification_event,
    _store_services,
    _task_event,
)

from repomesh.bootstrap.container import ApplicationContainer
from repomesh.modules.decision_chain import (
    DecisionChainSimilarityService,
    PostgresDecisionChainStore,
)
from repomesh.modules.decision_chain.contracts import (
    DecisionNodeInput,
    DecisionStatus,
    DecisionStep,
    NodeActor,
)


def _classified(org: UUID, project: UUID, leader: UUID, *, repos: list[str], at: datetime):
    """A classification event whose affected repositories differ from the
    shared ``_classification_event`` fixture (hard-codes ts-notify/ts-order).
    """
    event = _classification_event(org, project, leader, at=at)
    event.payload["classification"]["required"] = list(repos)
    event.payload["affected_repository_ids"] = list(repos)
    return event


async def test_similar_returns_other_projects_sharing_a_repository() -> None:
    org, leader = uuid4(), uuid4()
    project_a, project_b, project_c = uuid4(), uuid4(), uuid4()
    base = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)
    projection, store = _store_services(
        _chain_events(org, project_a, leader)  # A: ts-notify, ts-order
        + [
            _classified(org, project_b, leader, repos=["ts-notify"], at=base),
            _classified(org, project_c, leader, repos=["payments-core"], at=base),
        ]
    )
    await projection.drain()

    hits = await store.find_similar_structural(
        organization_id=org, project_id=project_a
    )
    assert [hit.project_id for hit in hits] == [project_b]


async def test_similar_excludes_the_project_itself() -> None:
    org, leader = uuid4(), uuid4()
    project_a, project_b = uuid4(), uuid4()
    base = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)
    projection, store = _store_services(
        _chain_events(org, project_a, leader)
        + [
            _classified(
                org,
                project_b,
                leader,
                repos=["ts-notify", "ts-order"],
                at=base,
            )
        ]
    )
    await projection.drain()

    hits = await store.find_similar_structural(
        organization_id=org, project_id=project_a
    )
    assert [hit.project_id for hit in hits] == [project_b]
    assert all(hit.project_id != project_a for hit in hits)


async def test_similar_collapses_each_project_to_its_latest_decision() -> None:
    org, leader = uuid4(), uuid4()
    project_a, project_b = uuid4(), uuid4()
    base = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)
    task_event, _ = _task_event(org, project_b, leader, at=base + timedelta(minutes=10))
    projection, store = _store_services(
        _chain_events(org, project_a, leader)
        + [
            _classified(org, project_b, leader, repos=["ts-notify"], at=base),
            task_event,
        ]
    )
    await projection.drain()

    hits = await store.find_similar_structural(
        organization_id=org, project_id=project_a
    )
    assert len(hits) == 1, "每个其他项目只贡献一条最新决策单"
    assert hits[0].project_id == project_b
    assert hits[0].step == DecisionStep.TASK, "折叠取业务时间最新的节点，而非链头"


async def test_similar_orders_hits_newest_first() -> None:
    org, leader = uuid4(), uuid4()
    project_a, project_b, project_c = uuid4(), uuid4(), uuid4()
    base = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)
    projection, store = _store_services(
        _chain_events(org, project_a, leader)
        + [
            _classified(org, project_b, leader, repos=["ts-notify"], at=base - timedelta(hours=1)),
            _classified(org, project_c, leader, repos=["ts-notify"], at=base + timedelta(hours=1)),
        ]
    )
    await projection.drain()

    hits = await store.find_similar_structural(
        organization_id=org, project_id=project_a
    )
    assert [hit.project_id for hit in hits] == [project_c, project_b]
    assert hits[0].business_time > hits[1].business_time


async def test_similar_is_empty_when_overlap_cannot_be_proven() -> None:
    org, leader = uuid4(), uuid4()
    project_a = uuid4()  # 全新需求：链上还没有节点，也无显式仓库作用域
    project_b = uuid4()
    projection, store = _store_services(
        [
            _classified(
                org,
                project_b,
                leader,
                repos=["payments-core"],
                at=datetime(2026, 8, 28, 9, 0, tzinfo=UTC),
            )
        ]
    )
    await projection.drain()

    hits = await store.find_similar_structural(
        organization_id=org, project_id=project_a
    )
    assert hits == [], "无法证明共享时不得声称相似（诚实数据）"


async def test_similar_is_empty_when_the_target_chain_has_no_repositories() -> None:
    org, leader = uuid4(), uuid4()
    project_a, project_b = uuid4(), uuid4()
    base = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)
    task_event, _ = _task_event(org, project_a, leader, at=base)
    projection, store = _store_services(
        [
            task_event,  # A 只有 task 节点：affected_repository_ids 为空
            _classified(org, project_b, leader, repos=["ts-notify"], at=base),
        ]
    )
    await projection.drain()

    hits = await store.find_similar_structural(
        organization_id=org, project_id=project_a
    )
    assert hits == [], "目标链无仓库信息 → 无法证明共享 → 空"


async def test_similar_scope_can_be_passed_explicitly_for_a_fresh_requirement() -> None:
    org, leader = uuid4(), uuid4()
    project_a = uuid4()  # 全新需求：链上还没有节点
    project_b = uuid4()
    repo = "notification-service"  # 仓库 name/slug（affected_repository_ids 的形态）
    projection, store = _store_services(
        [
            _classified(
                org,
                project_b,
                leader,
                repos=[repo],
                at=datetime(2026, 8, 28, 9, 0, tzinfo=UTC),
            )
        ]
    )
    await projection.drain()

    hits = await store.find_similar_structural(
        organization_id=org,
        project_id=project_a,
        same_repository_ids=(repo,),
    )
    assert [hit.project_id for hit in hits] == [project_b]


async def test_similarity_service_bounds_results_to_top_k() -> None:
    org, leader = uuid4(), uuid4()
    project_a = uuid4()
    peers = [uuid4(), uuid4(), uuid4()]
    base = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)
    projection, store = _store_services(
        _chain_events(org, project_a, leader)
        + [
            _classified(
                org,
                peers[i],
                leader,
                repos=["ts-notify"],
                at=base + timedelta(minutes=i + 1),
            )
            for i in range(3)
        ]
    )
    await projection.drain()

    service = DecisionChainSimilarityService(store)
    bounded = await service.find_similar(
        organization_id=org, project_id=project_a, top_k=2
    )
    assert len(bounded) == 2
    assert bounded[0].business_time > bounded[1].business_time, "取最近 N 条"

    full = await service.find_similar(organization_id=org, project_id=project_a)
    assert len(full) == 3
    assert full[0].business_time >= full[-1].business_time, "默认返回全部命中（newest first）"


async def test_similar_is_scoped_to_the_organization() -> None:
    org_a, org_b = uuid4(), uuid4()
    leader = uuid4()
    project_a, project_b = uuid4(), uuid4()
    base = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)
    projection, store = _store_services(
        _chain_events(org_a, project_a, leader)
        + [_classified(org_b, project_b, leader, repos=["ts-notify"], at=base)]
    )
    await projection.drain()

    hits = await store.find_similar_structural(
        organization_id=org_a, project_id=project_a
    )
    assert hits == [], "另一个组织的项目不得进入结果"


# --- Postgres 全链路 ---------------------------------------------------------


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


def test_similar_structural_on_postgres_matches_memory_semantics(
    application_container: ApplicationContainer,
) -> None:
    store = PostgresDecisionChainStore(application_container.database)
    org = uuid4()
    project_a, project_b, project_c = uuid4(), uuid4(), uuid4()
    base = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)
    asyncio.run(store.append(_node(org, project_a, repos=["ts-notify", "ts-order"], at=base)))
    asyncio.run(store.append(_node(org, project_b, repos=["ts-notify"], at=base)))
    asyncio.run(store.append(_node(org, project_c, repos=["payments-core"], at=base)))

    hits = asyncio.run(
        store.find_similar_structural(organization_id=org, project_id=project_a)
    )
    assert [hit.project_id for hit in hits] == [project_b]
    assert hits[0].step == DecisionStep.CLASSIFICATION
    assert hits[0].affected_repository_ids == ["ts-notify"]
    assert hits[0].status == DecisionStatus.PROPOSED

    # 无法证明共享（无节点也无显式作用域）→ 空，与内存孪生一致。
    unseen = asyncio.run(
        store.find_similar_structural(organization_id=org, project_id=uuid4())
    )
    assert unseen == []
