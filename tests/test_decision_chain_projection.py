"""decision-chain v0.1 —— Phase 2 投影测试：幂等 / 版本化 / 串链 / trace。

合同 §4.2 / §5 / §6.1 / §7。前半部分是内存存储上的确定性单元测试
（幂等、版本递增、乱序容忍、actor 映射、effective_tiers 重建、legacy
gaps、trace 装配）；后半部分是 Postgres 全链路集成测试——复用 Phase 1
的 ``_discovery_session``/``_plan_task``/``_observe_pr`` 产生五个节点事件，
经 ``platform.audit_events`` 投影到 ``decision_chain_nodes`` 后 trace。
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from api.test_issue_discovery import (
    ANALYSIS_OK,
    CANDIDATES,
    INTEGRATION,
    ScriptedLLM,
    _configure,
    _confirmation,
    _seed,
)
from sqlalchemy import select
from test_decision_chain_events import (
    _discovery_session,
    _observe_pr,
    _plan_task,
)

from repomesh.bootstrap.container import ApplicationContainer
from repomesh.modules.decision_chain import (
    DecisionChainProjectionService,
    DecisionChainTraceService,
    InMemoryDecisionChainStore,
    InMemoryDecisionEventSource,
    PostgresDecisionChainStore,
)
from repomesh.modules.decision_chain.contracts import (
    DecisionNodeInput,
    DecisionStatus,
    DecisionStep,
    NodeActor,
    RequirementView,
)
from repomesh.modules.decision_chain.infrastructure.models import DecisionNodeRecord
from repomesh.modules.delivery import PostgresDeliveryAuditLog
from repomesh.settings import get_settings
from repomesh.shared.events import ActorType, EventEnvelope


def _event(
    event_type: str,
    *,
    organization_id: UUID,
    project_id: UUID,
    actor_id: str,
    payload: dict,
    occurred_at: datetime | None = None,
    actor_type: ActorType = ActorType.AGENT,
    task_id: UUID | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        aggregate_type="Project",
        aggregate_id=project_id,
        aggregate_version=1,
        payload=payload,
        correlation_id=uuid4(),
        event_id=uuid4(),
        occurred_at=occurred_at or datetime.now(UTC),
        organization_id=organization_id,
        project_id=project_id,
        task_id=task_id,
    )


def _classification_event(org: UUID, project: UUID, leader: UUID, *, at: datetime) -> EventEnvelope:
    return _event(
        "ClassificationDecided",
        organization_id=org,
        project_id=project,
        actor_id=str(leader),
        occurred_at=at,
        payload={
            "classification": {
                "required": ["ts-notify"],
                "maybe": ["ts-order"],
                "excluded": [],
                "effective_tiers": {"ts-notify": "REQUIRED", "ts-order": "MAYBE"},
                "evidence_version": "sha256:abc",
                "supplemented_repository_ids": [],
            },
            "affected_repository_ids": ["ts-notify", "ts-order"],
        },
    )


def _confirmation_event(
    org: UUID,
    project: UUID,
    leader: UUID,
    *,
    at: datetime,
    adjustments: list[dict] | None = None,
) -> EventEnvelope:
    return _event(
        "ConfirmationDecided",
        organization_id=org,
        project_id=project,
        actor_id=str(leader),
        occurred_at=at,
        payload={
            "approval": {
                "state": "approved",
                "decided_by_agent_id": str(leader),
                "reason": "范围合理",
            },
            "evidence_version": "sha256:abc",
            "adjustments": adjustments or [],
            "affected_repository_ids": ["ts-notify", "ts-order"],
        },
    )


def _integration_event(org: UUID, project: UUID, leader: UUID, *, at: datetime) -> EventEnvelope:
    return _event(
        "IntegrationDecided",
        organization_id=org,
        project_id=project,
        actor_id=str(leader),
        occurred_at=at,
        payload={
            "execution_batches": [
                {"index": 0, "repository_ids": ["ts-notify"]},
                {"index": 1, "repository_ids": ["ts-order"]},
            ],
            "contracts": ["ts-notify->ts-order:POST /api/v1/notify"],
            "affected_repository_ids": ["ts-notify", "ts-order"],
        },
    )


def _task_event(
    org: UUID, project: UUID, leader: UUID, *, at: datetime
) -> tuple[EventEnvelope, UUID]:
    task_id = uuid4()
    return (
        _event(
            "TasksPlanned",
            organization_id=org,
            project_id=project,
            actor_id=str(leader),
            occurred_at=at,
            task_id=task_id,
            payload={
                "schema_version": 1,
                "upstream_step": "integration",
                "task": {
                    "task_id": str(task_id),
                    "repository_id": str(uuid4()),
                    "title": "改通知模板",
                    "parent_task_id": None,
                },
            },
        ),
        task_id,
    )


def _pr_event(
    org: UUID,
    project: UUID,
    leader: UUID,
    task_id: UUID,
    *,
    at: datetime,
    number: int = 42,
) -> EventEnvelope:
    return _event(
        "PullRequestObserved",
        organization_id=org,
        project_id=project,
        actor_id=str(leader),
        occurred_at=at,
        actor_type=ActorType.SERVICE,
        payload={
            "schema_version": 1,
            "change_set_id": str(uuid4()),
            "repository_id": str(uuid4()),
            "pull_request_number": number,
            "pull_request_url": f"https://github.com/acme/ts-notify/pull/{number}",
            "task_ids": [str(task_id)],
        },
    )


def _chain_events(org: UUID, project: UUID, leader: UUID) -> list[EventEnvelope]:
    """Five sequential chain events, oldest first (§2.1 order)."""
    base = datetime(2026, 8, 28, 10, 0, tzinfo=UTC)
    task_event, task_id = _task_event(
        org, project, leader, at=base + timedelta(minutes=3)
    )
    return [
        _classification_event(org, project, leader, at=base),
        _confirmation_event(org, project, leader, at=base + timedelta(minutes=1)),
        _integration_event(org, project, leader, at=base + timedelta(minutes=2)),
        task_event,
        _pr_event(org, project, leader, task_id, at=base + timedelta(minutes=4)),
    ]


def _store_services(
    events: list[EventEnvelope],
) -> tuple[DecisionChainProjectionService, InMemoryDecisionChainStore]:
    store = InMemoryDecisionChainStore()
    source = InMemoryDecisionEventSource(events, store=store)
    return DecisionChainProjectionService(store, source), store


async def test_drain_is_idempotent_and_source_skips_projected() -> None:
    org, project, leader = uuid4(), uuid4(), uuid4()
    projection, store = _store_services(_chain_events(org, project, leader))

    first = await projection.drain()
    assert first == 5
    second = await projection.drain()
    assert second == 0, "already-projected ids must not be re-drained"
    assert len(store.event_ids) == 5


async def test_append_replay_returns_the_existing_row() -> None:
    store = InMemoryDecisionChainStore()
    node = DecisionNodeInput(
        event_id=uuid4(),
        project_id=uuid4(),
        organization_id=uuid4(),
        step=DecisionStep.CLASSIFICATION,
        status=DecisionStatus.PROPOSED,
        actor=NodeActor(type="llm"),
        business_time=datetime.now(UTC),
        event_type="ClassificationDecided",
        evidence_refs={"result": ["sha256:abc"], "process": []},
        payload_summary={"required": ["ts-notify"]},
        affected_repository_ids=["ts-notify"],
    )
    first = await store.append(node)
    second = await store.append(node)
    assert second.decision_id == first.decision_id
    assert second.version == first.version == 1


async def test_same_step_events_version_increment_without_overwrite() -> None:
    org, project, leader = uuid4(), uuid4(), uuid4()
    events = [
        _classification_event(org, project, leader, at=datetime(2026, 8, 28, 9, 0, tzinfo=UTC)),
        _classification_event(org, project, leader, at=datetime(2026, 8, 28, 9, 5, tzinfo=UTC)),
    ]
    projection, store = _store_services(events)
    await projection.drain()
    nodes = await store.trace(organization_id=org, project_id=project)
    versions = sorted(node.version for node in nodes.nodes)
    assert versions == [1, 2]
    assert len({node.decision_id for node in nodes.nodes}) == 2


async def test_chain_links_upstream_ref_along_the_steps() -> None:
    org, project, leader = uuid4(), uuid4(), uuid4()
    projection, store = _store_services(_chain_events(org, project, leader))
    await projection.drain()
    chain = await store.trace(organization_id=org, project_id=project)
    by_step: dict[DecisionStep, list] = {}
    for node in chain.nodes:
        by_step.setdefault(node.step, []).append(node)

    classification = by_step[DecisionStep.CLASSIFICATION][0]
    assert classification.upstream_ref is None, "chain root has no parent"

    assert by_step[DecisionStep.CONFIRMATION][0].upstream_ref == classification.decision_id
    assert (
        by_step[DecisionStep.INTEGRATION][0].upstream_ref
        == by_step[DecisionStep.CONFIRMATION][0].decision_id
    )
    assert (
        by_step[DecisionStep.TASK][0].upstream_ref
        == by_step[DecisionStep.INTEGRATION][0].decision_id
    )
    # the pr node hints at its task via payload task_ids → points at the task node.
    for pr in by_step[DecisionStep.PR]:
        assert pr.upstream_ref == by_step[DecisionStep.TASK][0].decision_id
    assert chain.legacy_gaps == []


async def test_actor_mapping_per_event_type() -> None:
    org, project, leader = uuid4(), uuid4(), uuid4()
    projection, store = _store_services(_chain_events(org, project, leader))
    await projection.drain()
    chain = await store.trace(organization_id=org, project_id=project)
    by_step = {node.step: node for node in chain.nodes}

    classification = by_step[DecisionStep.CLASSIFICATION]
    assert classification.actor.type == "llm"
    assert classification.actor.agent_id == leader

    confirmation = by_step[DecisionStep.CONFIRMATION]
    assert confirmation.actor.type == "human", "the approval names the human decider"
    assert confirmation.actor.agent_id == leader

    task = by_step[DecisionStep.TASK]
    assert task.actor.type == "llm"

    pr = by_step[DecisionStep.PR]
    assert pr.actor.type == "service", "PR observation is a SERVICE envelope"
    assert pr.actor.agent_id is None


async def test_effective_tiers_land_on_confirmation_not_classification() -> None:
    org, project, leader = uuid4(), uuid4(), uuid4()
    base = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)
    events = [
        _classification_event(org, project, leader, at=base),
        _confirmation_event(
            org,
            project,
            leader,
            at=base + timedelta(minutes=1),
            adjustments=[
                {
                    "repository": "ts-order",
                    "from": "MAYBE",
                    "to": "REQUIRED",
                }
            ],
        ),
    ]
    projection, store = _store_services(events)
    await projection.drain()
    chain = await store.trace(organization_id=org, project_id=project)
    by_step = {node.step: node for node in chain.nodes}

    classification = by_step[DecisionStep.CLASSIFICATION]
    # §4.2: the LLM verdict stays untouched on the classification node.
    assert classification.payload_summary["effective_tiers"]["ts-order"] == "MAYBE"
    assert classification.status == DecisionStatus.PROPOSED

    confirmation = by_step[DecisionStep.CONFIRMATION]
    assert confirmation.status == DecisionStatus.CONFIRMED
    assert confirmation.payload_summary["effective_tiers"]["ts-notify"] == "REQUIRED"
    assert (
        confirmation.payload_summary["effective_tiers"]["ts-order"] == "REQUIRED"
    ), "the adjustment becomes the effective tier on the confirmation node"


async def test_confirmation_rejection_and_changes_requested_map_to_status() -> None:
    org, project, leader = uuid4(), uuid4(), uuid4()
    base = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)
    rejected = _confirmation_event(org, project, leader, at=base)
    rejected.payload["approval"]["state"] = "rejected"
    changes = _confirmation_event(org, project, leader, at=base + timedelta(minutes=1))
    changes.payload["approval"]["state"] = "changes_requested"

    projection, store = _store_services([rejected, changes])
    await projection.drain()
    chain = await store.trace(organization_id=org, project_id=project)
    statuses = sorted(node.status for node in chain.nodes)
    assert statuses == [DecisionStatus.CHANGES_REQUESTED, DecisionStatus.REJECTED]


async def test_events_without_org_or_project_identity_are_skipped() -> None:
    project = uuid4()
    orphan = EventEnvelope(
        event_type="ClassificationDecided",
        actor_type=ActorType.AGENT,
        actor_id=str(uuid4()),
        aggregate_type="Project",
        aggregate_id=project,
        aggregate_version=1,
        payload={},
        correlation_id=uuid4(),
        organization_id=None,  # cannot prove ownership → red line 7
        project_id=project,
    )
    projection, store = _store_services([orphan])
    projected = await projection.drain()
    assert projected == 0
    chain = await store.trace(organization_id=uuid4(), project_id=project)
    assert chain.nodes == []


async def test_out_of_order_events_land_and_trace_by_version() -> None:
    org, project, leader = uuid4(), uuid4(), uuid4()
    base = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)
    task_event, task_id = _task_event(org, project, leader, at=base + timedelta(minutes=2))
    # Q5: PR 的业务时间早于 task（真实乱序）。source 按 occurred_at 排序，
    # 于是 PR 先被投影，此时 task 节点尚不存在——hint 无法解析，不构造链接。
    events = [
        _pr_event(org, project, leader, task_id, at=base + timedelta(minutes=1)),
        task_event,
    ]
    projection, store = _store_services(events)
    await projection.drain()
    chain = await store.trace(organization_id=org, project_id=project)
    pr = next(node for node in chain.nodes if node.step == DecisionStep.PR)
    task = next(node for node in chain.nodes if node.step == DecisionStep.TASK)
    assert pr.upstream_ref is None, "an unresolved hint must not fabricate a link"
    assert task.upstream_ref is None
    assert [node.business_time for node in chain.nodes] == sorted(
        node.business_time for node in chain.nodes
    )
    assert chain.legacy_gaps == ["classification", "confirmation", "integration"]


async def test_source_normalizes_arrival_order_by_business_time() -> None:
    org, project, leader = uuid4(), uuid4(), uuid4()
    base = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)
    task_event, task_id = _task_event(org, project, leader, at=base + timedelta(minutes=2))
    # 到达顺序倒挂（task 的 envelope 在 list 尾部），但业务时间有序：
    # source 按 occurred_at 归一化后 task 先投影，PR 的 hint 正常解析。
    events = [
        _pr_event(org, project, leader, task_id, at=base + timedelta(minutes=3)),
        task_event,
    ]
    projection, store = _store_services(events)
    await projection.drain()
    chain = await store.trace(organization_id=org, project_id=project)
    pr = next(node for node in chain.nodes if node.step == DecisionStep.PR)
    task = next(node for node in chain.nodes if node.step == DecisionStep.TASK)
    assert pr.upstream_ref == task.decision_id
    assert chain.legacy_gaps == ["classification", "confirmation", "integration"]


async def test_legacy_gaps_report_middle_holes_only() -> None:
    org, project, leader = uuid4(), uuid4(), uuid4()
    base = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)
    # classification → integration (confirmation skipped) → task → pr
    task_event, task_id = _task_event(org, project, leader, at=base + timedelta(minutes=3))
    events = [
        _classification_event(org, project, leader, at=base),
        _integration_event(org, project, leader, at=base + timedelta(minutes=2)),
        task_event,
        _pr_event(org, project, leader, task_id, at=base + timedelta(minutes=4)),
    ]
    projection, store = _store_services(events)
    await projection.drain()
    chain = await store.trace(organization_id=org, project_id=project)
    assert chain.legacy_gaps == ["confirmation"]

    # a fresh chain that only reached classification has no tail gaps.
    projection2, store2 = _store_services([events[0]])
    await projection2.drain()
    partial = await store2.trace(organization_id=org, project_id=project)
    assert partial.legacy_gaps == []


async def test_trace_service_assembles_the_requirement_root() -> None:
    org, project, leader = uuid4(), uuid4(), uuid4()
    projection, store = _store_services(_chain_events(org, project, leader))
    await projection.drain()

    class _Reader:
        async def get_requirement(self, project_id):
            return RequirementView(
                text="订单通知模板改造",
                plan_version=1,
                snapshot_id=uuid4(),
            )

    trace_service = DecisionChainTraceService(store, _Reader())
    trace = await trace_service.trace(organization_id=org, project_id=project)
    assert trace.requirement is not None
    assert trace.requirement.text == "订单通知模板改造"
    assert trace.requirement.plan_version == 1
    assert len(trace.nodes) == 5
    assert trace.project_id == project
    assert trace.organization_id == org


# --- Postgres 全链路 ---------------------------------------------------------


def test_full_chain_projects_into_postgres_and_traces(
    application_container: ApplicationContainer, monkeypatch
) -> None:
    """五节点事件落 audit_events → drain 投影 → trace 完整链（含根与证据）。"""

    _configure(monkeypatch)
    organization_id, leader_id, _, _ = _seed(application_container)
    container = replace(
        application_container,
        llm_client=ScriptedLLM(
            ANALYSIS_OK,
            CANDIDATES,
            _confirmation("REQUIRED"),
            _confirmation("MAYBE"),
            INTEGRATION,
        ),
    )
    try:
        with _discovery_session(container, leader_id) as (project_id, evidence):
            tasks_planned, task_id = _plan_task(
                organization_id, project_id, leader_id
            )
            (observed_first, observed_second), _, _ = _observe_pr(
                organization_id, project_id, leader_id, task_id
            )
            # 后两个节点的信封进 Postgres audit（与 discovery 三事件同一张表）。
            audit = PostgresDeliveryAuditLog(container.database)
            for event in (tasks_planned, observed_first, observed_second):
                asyncio.run(audit.append(event))

            projection = container.decision_chain_projection_service()
            # Postgres audit 共 6 个链事件：discovery 三件 + task 一件 + pr 两件。
            assert asyncio.run(projection.drain()) == 6
            assert asyncio.run(projection.drain()) == 0, "drain 幂等且增量"

            trace = asyncio.run(
                container.decision_chain_trace_service().trace(
                    organization_id=organization_id,
                    project_id=project_id,
                )
            )
            # 6 个节点：1 分类 + 1 确认 + 1 集成 + 1 task + 2 pr。
            assert len(trace.nodes) == 6
            assert [node.step for node in trace.nodes] == [
                DecisionStep.CLASSIFICATION,
                DecisionStep.CONFIRMATION,
                DecisionStep.INTEGRATION,
                DecisionStep.TASK,
                DecisionStep.PR,
                DecisionStep.PR,
            ]
            assert trace.legacy_gaps == []

            by_step: dict[DecisionStep, list] = {}
            for node in trace.nodes:
                by_step.setdefault(node.step, []).append(node)

            # §6.1 链根：plan_version=1 快照自带需求文本。
            assert trace.requirement is not None
            assert trace.requirement.plan_version == 1
            assert trace.requirement.text

            classification = by_step[DecisionStep.CLASSIFICATION][0]
            confirmation = by_step[DecisionStep.CONFIRMATION][0]
            task = by_step[DecisionStep.TASK][0]

            # §4.2：确认节点的 effective_tiers 由分类 + adjustments 重建。
            assert confirmation.payload_summary["effective_tiers"] == {
                "ts-notify": "REQUIRED",
                "ts-order": "MAYBE",
            }
            assert confirmation.status == DecisionStatus.CONFIRMED
            assert classification.payload_summary["effective_tiers"]["ts-order"] == "MAYBE"

            # 证据指针（§6.2 result）：分类/确认带 evidence_version 指纹。
            assert classification.evidence_refs["result"] == [evidence]
            assert confirmation.evidence_refs["result"] == [evidence]

            # actor 映射。
            assert classification.actor.type == "llm"
            assert confirmation.actor.type == "human"
            assert by_step[DecisionStep.PR][0].actor.type == "service"
            assert task.actor.type == "llm"

            # 串链：upstream_ref 逐级指向前一步最新节点。
            assert classification.upstream_ref is None
            assert confirmation.upstream_ref == classification.decision_id
            assert by_step[DecisionStep.INTEGRATION][0].upstream_ref == confirmation.decision_id
            assert task.upstream_ref == by_step[DecisionStep.INTEGRATION][0].decision_id
            for pr in by_step[DecisionStep.PR]:
                assert pr.upstream_ref == task.decision_id, "pr 节点指向其 task 节点"

            # 行级幂等：Postgres 存储上重放同 event 返回同一行。
            store = PostgresDecisionChainStore(container.database)
            replay_input = asyncio.run(
                _input_from_trace(container, project_id, classification)
            )
            replayed = asyncio.run(store.append(replay_input))
            assert replayed.decision_id == classification.decision_id
    finally:
        get_settings.cache_clear()


async def _input_from_trace(container, project_id, node) -> DecisionNodeInput:
    """Rebuild the append input for an already-projected node (replay test)."""
    record = await _decision_node_record(container, project_id, node.event_id)
    actor = record.actor or {}
    agent_id = UUID(actor["agent_id"]) if actor.get("agent_id") else None
    return DecisionNodeInput(
        event_id=record.event_id,
        project_id=record.project_id,
        organization_id=record.organization_id,
        step=DecisionStep(record.step),
        status=DecisionStatus(record.status),
        actor=NodeActor(type=str(actor.get("type") or "llm"), agent_id=agent_id),
        business_time=record.business_time,
        event_type=record.event_type,
        evidence_refs=record.evidence_refs,
        payload_summary=record.payload_summary,
        affected_repository_ids=list(record.affected_repository_ids),
    )


async def _decision_node_record(container, project_id, event_id) -> DecisionNodeRecord:
    async with container.database.transaction() as session:
        record = await session.scalar(
            select(DecisionNodeRecord).where(
                DecisionNodeRecord.project_id == project_id,
                DecisionNodeRecord.event_id == event_id,
            )
        )
    assert record is not None
    return record
