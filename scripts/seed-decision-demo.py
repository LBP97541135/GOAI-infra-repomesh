"""Seed the live audit-walkthrough corpus: 6 demo decision chains + L1 roots.

Purpose: the decision-chain console (frontend/) must be demonstrable in live
mode, not only under ``?source=replay``. This seeds, for the six issues that
the issue list already promises (they are the demo scenario's requirements),
a first plan snapshot (the §6.1 requirement root, E1) and the decision chain
nodes with the same shape as the replay fixture — so title keywords / #short
ids resolve through the live APIs, traces open, and L3 semantic search has a
corpus once ``POST /api/v1/decision-chains/embeddings-refresh`` has embedded.

Runs inside the api container against its own database:

    docker compose --profile platform up -d api
    docker compose --profile platform exec -T api \
        python /scripts/seed-decision-demo.py [--database-url URL]

Idempotent, stated honestly:

- Every ``event_id`` and ``decision_id`` derives from a fixed UUIDv5
  namespace, so a rerun replays the same rows instead of duplicating them.
- Plan snapshots are insert-if-absent on ``(project_id, plan_version)``.
- Chain nodes dedupe on ``event_id`` (the store's own idempotency).
- The seed never writes another module's tables directly — it goes through
  ``PlanSnapshotStore`` (repository_intelligence) and
  ``PostgresDecisionChainStore.append`` (decision_chain), the same ports the
  runtime uses.
"""

import argparse
import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

from repomesh.modules.decision_chain import PostgresDecisionChainStore
from repomesh.modules.decision_chain.contracts import (
    DecisionNodeInput,
    DecisionStatus,
    DecisionStep,
    NodeActor,
)
from repomesh.modules.repository_intelligence.infrastructure import (
    PlanSnapshotStore,
)
from repomesh.persistence import Database

DEFAULT_DATABASE_URL = os.environ.get(
    "REPOMESH_DATABASE_URL", "postgresql+psycopg://repomesh:repomesh@127.0.0.1:5533/repomesh"
)

# Same organization as the train-ticket fixture already present in the DB, so
# the demo chains share its L1 namespace instead of inventing a new one.
DEMO_ORG = UUID("c095f4b7-4e1a-48c8-a9d8-83e1c1d2c0f0")
DEMO_NS = UUID("d13e8a10-0000-0000-0000-0000000000dc")  # seed namespace (uuid5)

_EVENT_TYPES = {
    DecisionStep.CLASSIFICATION: "ClassificationDecided",
    DecisionStep.CONFIRMATION: "ConfirmationDecided",
    DecisionStep.INTEGRATION: "IntegrationDecided",
    DecisionStep.TASK: "TasksPlanned",
    DecisionStep.PR: "PullRequestObserved",
}


def _event_id(project_id: UUID, step: DecisionStep, version: int) -> UUID:
    return uuid.uuid5(DEMO_NS, f"{project_id}:{step.value}:{version}")


def _decision_id(project_id: UUID, step: DecisionStep) -> UUID:
    return uuid.uuid5(DEMO_NS, f"{project_id}:{step.value}:decision")


# The six demo requirements — ids and titles are the same the issue list shows.
PROJECTS = [
    {
        "id": UUID("9d1e4c56-1b39-4f72-a4e5-88fd30de0037"),
        "title": "退货流程支持部分退款",
        "requirement": "当前退货流程只支持整单退款，用户因部分商品问题退货时必须整单退回；"
        "需支持按商品行部分退款，并保留退款金额审计记录。",
        "repos": ["saleor-core", "saleor-dashboard"],
        "nodes": [
            (DecisionStep.CLASSIFICATION, DecisionStatus.MERGED, {
                "required": ["退货流程支持部分退款", "按商品行退款", "退款金额审计"],
                "maybe": ["部分退款审批", "退款单拆分"],
                "excluded": ["物流拦截", "库存回补"],
            }, 30),
            (DecisionStep.CONFIRMATION, DecisionStatus.CONFIRMED, {
                "approval": {"state": "approved", "decided_by": "product-owner"},
                "adjustments": ["仅支持发货后 30 天内部分退款"],
            }, 29),
            (DecisionStep.TASK, DecisionStatus.MERGED, {
                "title": "退货部分退款：订单行级退款计算与审计记录",
                "task_ids": ["dc-return-partial-01"],
            }, 27),
            # Confirmation v2（次日补录批准理由）：append-only 审计模型里「后续
            # 同步事件递增版本」是合法玩法（README §Versioned），也让控制台能
            # 演示「为什么批准」与多版本演进。days_ago=28 落在 v1(29) 与 task(27)
            # 之间，trace 按业务时间排序自然就位。
            (DecisionStep.CONFIRMATION, DecisionStatus.CONFIRMED, {
                "approval": {
                    "state": "approved",
                    "decided_by": "product-owner",
                    "reason": "按商品行退款是本次需求的核心，退款金额审计直接服务合规，两项必须做；"
                              "审批流与退款单拆分先挂待定，等首版上线后按反馈再定；"
                              "物流拦截要动履约系统、库存回补牵扯财务对账，都超出本次边界。",
                },
                "adjustments": ["仅支持发货后 30 天内部分退款"],
            }, 28),
        ],
    },
    {
        "id": UUID("7f3d2a10-93d0-4c8e-9b21-5aa1c0de0042"),
        "title": "结账价格修改原因：记录、暴露并在后台展示",
        "requirement": "结账阶段价格修改（优惠、调价、后台干预）必须记录修改原因，"
        "并在后台对订单展示修改历史，保证价格修改可审计。",
        "repos": ["saleor-core", "saleor-dashboard"],
        "nodes": [
            (DecisionStep.CLASSIFICATION, DecisionStatus.MERGED, {
                "required": ["结账价格修改原因记录", "后台展示价格修改历史", "价格修改审计"],
                "maybe": ["价格修改原因表单", "结账备注"],
                "excluded": ["运费计算", "优惠券核销"],
            }, 21),
            (DecisionStep.CONFIRMATION, DecisionStatus.CONFIRMED, {
                "approval": {"state": "approved", "decided_by": "product-owner"},
                "adjustments": ["原因必填，覆盖后台与 API 两种修改入口"],
            }, 20),
            (DecisionStep.INTEGRATION, DecisionStatus.MERGED, {
                "contracts": [
                    {"interface": "IPriceChangeReason", "provider": "saleor-core",
                     "consumers": ["saleor-dashboard"]},
                ],
            }, 18),
            (DecisionStep.TASK, DecisionStatus.MERGED, {
                "title": "结账价格修改原因：记录字段、后台展示与 API 暴露",
                "task_ids": ["dc-price-reason-01", "dc-price-reason-02"],
            }, 16),
            (DecisionStep.PR, DecisionStatus.MERGED, {
                "pull_request_number": 4821,
                "pull_request_url": "https://github.com/demo/saleor/pull/4821",
            }, 14),
            # Confirmation v2（补录批准理由，见退货链同款说明）。
            (DecisionStep.CONFIRMATION, DecisionStatus.CONFIRMED, {
                "approval": {
                    "state": "approved",
                    "decided_by": "product-owner",
                    "reason": "价格修改全程可审计是合规底线，记录、展示、审计三项必改缺一不可；"
                              "原因表单与结账备注先看运营反馈再定；"
                              "运费计算与优惠券核销属于定价域，不在本需求边界内。",
                },
                "adjustments": ["原因必填，覆盖后台与 API 两种修改入口"],
            }, 19),
        ],
    },
    {
        "id": UUID("b41d0c77-5e2a-4f18-9c30-77aa10de0041"),
        "title": "账单金额四舍五入错误修复",
        "requirement": "账单合计存在四舍五入错误：按行四舍五入后加总与总额不一致，"
        "需统一为按分累计后一次性舍入，并修复历史账单展示。",
        "repos": ["saleor-core"],
        "nodes": [
            (DecisionStep.CLASSIFICATION, DecisionStatus.MERGED, {
                "required": ["账单金额四舍五入错误修复", "按分累计后舍入", "历史账单一致性"],
                "maybe": ["舍入精度配置"],
                "excluded": ["税务计算", "多币种"],
            }, 12),
            (DecisionStep.CONFIRMATION, DecisionStatus.CONFIRMED, {
                "approval": {"state": "approved", "decided_by": "tech-lead"},
                "adjustments": ["舍入规则跟随门店货币精度"],
            }, 11),
            (DecisionStep.INTEGRATION, DecisionStatus.MERGED, {
                "contracts": [
                    {"interface": "IInvoiceRounding", "provider": "saleor-core",
                     "consumers": ["saleor-dashboard"]},
                ],
            }, 9),
            (DecisionStep.TASK, DecisionStatus.MERGED, {
                "title": "账单四舍五入修复：统一舍入口径与历史账单重算",
                "task_ids": ["dc-rounding-01"],
            }, 7),
            (DecisionStep.PR, DecisionStatus.MERGED, {
                "pull_request_number": 4812,
                "pull_request_url": "https://github.com/demo/saleor/pull/4812",
            }, 5),
            # Confirmation v2（补录批准理由，见退货链同款说明）。
            (DecisionStep.CONFIRMATION, DecisionStatus.CONFIRMED, {
                "approval": {
                    "state": "approved",
                    "decided_by": "tech-lead",
                    "reason": "账单金额一致性是用户信任底线，舍入口径必须全局统一、历史账单要能对上；"
                              "舍入精度配置属运营策略，先不做；"
                              "税务计算与多币种由独立需求承接，不混入本次修复。",
                },
                "adjustments": ["舍入规则跟随门店货币精度"],
            }, 10),
        ],
    },
    {
        "id": UUID("2a9f5e31-8c74-4b60-a1d2-33bc90de0040"),
        "title": "通知摘要：邮件与站内信合并为每日一封",
        "requirement": "通知发送过频导致用户关闭通知：将订单、账单、促销类通知合并为每日一封摘要，"
        "支持邮件与站内信两种渠道。",
        "repos": ["saleor-core", "saleor-dashboard"],
        "nodes": [
            (DecisionStep.CLASSIFICATION, DecisionStatus.MERGED, {
                "required": ["通知摘要", "邮件与站内信合并", "每日一封"],
                "maybe": ["摘要时间配置", "通知优先级"],
                "excluded": ["短信渠道", "实时风控通知"],
            }, 8),
            (DecisionStep.INTEGRATION, DecisionStatus.MERGED, {
                "contracts": [
                    {"interface": "INotifyDigestSubscribe", "provider": "saleor-dashboard",
                     "consumers": ["saleor-core"]},
                ],
            }, 6),
        ],
    },
    {
        "id": UUID("c8e07b12-4a91-4d55-b7e6-19df20de0039"),
        "title": "购物车库存提示优化",
        "requirement": "购物车页在商品库存不足或售罄时提示不清晰："
        "需在购物车行内展示可用数量、低库存警示与售罄置灰。",
        "repos": ["saleor-dashboard"],
        "nodes": [
            (DecisionStep.CLASSIFICATION, DecisionStatus.MERGED, {
                "required": ["购物车库存提示", "可用数量展示", "低库存警示", "售罄置灰"],
                "maybe": ["库存数字徽标"],
                "excluded": ["预售", "门店自提"],
            }, 6),
            (DecisionStep.CONFIRMATION, DecisionStatus.CONFIRMED, {
                "approval": {"state": "approved", "decided_by": "product-owner"},
                "adjustments": ["低库存阈值 5 件，可在门店后台配置"],
            }, 5),
            # Confirmation v2（补录批准理由，见退货链同款说明）。
            (DecisionStep.CONFIRMATION, DecisionStatus.CONFIRMED, {
                "approval": {
                    "state": "approved",
                    "decided_by": "product-owner",
                    "reason": "库存提示直接影响下单转化，四项必改覆盖从库存不足到售罄的全部场景；"
                              "库存数字徽标属视觉增强，可以后置；"
                              "预售与门店自提是新能力，不在本需求边界内。",
                },
                "adjustments": ["低库存阈值 5 件，可在门店后台配置"],
            }, 4),
        ],
    },
    {
        "id": UUID("5d3a91f4-6b28-4e03-8f41-64ca70de0038"),
        "title": "API 定价结果增加 discount_amount",
        "requirement": "定价 API 返回结果缺少折扣金额字段，客户端无法展示优惠明细；"
        "需在定价结果中增加 discount_amount 并同步计算逻辑。",
        "repos": ["saleor-core"],
        "nodes": [
            (DecisionStep.CLASSIFICATION, DecisionStatus.MERGED, {
                "required": ["API 定价结果增加 discount_amount", "折扣金额字段", "定价计算同步"],
                "maybe": ["折扣明细行"],
                "excluded": ["优惠券校验", "价格覆盖权限"],
            }, 3),
        ],
    },
]


async def seed(database_url: str) -> dict:
    database = Database(database_url)
    snapshots = PlanSnapshotStore(database)
    chains = PostgresDecisionChainStore(database)

    seeded_snapshots = 0
    seeded_nodes = 0
    for project in PROJECTS:
        project_id = project["id"]
        existing = await snapshots.get_by_version(project_id, plan_version=1)
        if existing is None:
            await snapshots.save(
                project_id=project_id,
                plan_version=1,
                engineering_spec=(
                    f"Demo requirement: {project['title']}. "
                    "Seeded by seed-decision-demo.py for the live audit walkthrough."
                ),
                contracts=[],
                task_dag=[],
                execution_batches=[[]],
                graph_edges=[],
                requirement_text=project["requirement"],
                document_filename=None,
                integration_method=None,
                discovery=None,
            )
            seeded_snapshots += 1

        base_time = datetime(2026, 7, 1, tzinfo=UTC)
        for version, (step, status, payload, days_ago) in enumerate(
            project["nodes"], start=1
        ):
            node = DecisionNodeInput(
                event_id=_event_id(project_id, step, version),
                project_id=project_id,
                organization_id=DEMO_ORG,
                step=step,
                status=status,
                actor=NodeActor(type="llm", agent_id=None),
                business_time=base_time + timedelta(days=30 - days_ago),
                event_type=_EVENT_TYPES[step],
                evidence_refs={
                    f"ev:{step.value}-{str(project_id)[:6]}-a{version}": [
                        f"seed://demo/{str(project_id)[:6]}/{step.value}/{version}"
                    ]
                },
                payload_summary=payload,
                affected_repository_ids=project["repos"],
            )
            view = await chains.append(node)
            if view.event_id == node.event_id:
                seeded_nodes += 1

    return {
        "projects": len(PROJECTS),
        "snapshots_seeded": seeded_snapshots,
        "nodes_seeded": seeded_nodes,
        "organization_id": str(DEMO_ORG),
        "next": "POST /api/v1/decision-chains/embeddings-refresh to vectorize",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--database-url",
        default=os.environ.get("REPOMESH_DATABASE_URL", DEFAULT_DATABASE_URL),
        help="SQLAlchemy async DSN (default: REPOMESH_DATABASE_URL or the 5533 convention)",
    )
    arguments = parser.parse_args()
    result = asyncio.run(seed(arguments.database_url))
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
