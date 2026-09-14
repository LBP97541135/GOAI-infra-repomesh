"""彻底清除一个**已归档** issue 的全部业务事实（2026-09-08 用户裁决）。

归档（``issue_archive``）是「墓碑 + 全部保留」：隐藏但不删。清除是它旁边那个
**独立的、不可逆的**第二动作：硬删除计划快照、决策链节点（含向量）与
checkpoint 决策等审计事件——只保留一条 ``IssuePurged`` 审计（谁、何时、删了
多少）。审计删光而不留痕是被明确否决的语义：删除动作本身必须可追溯。

跨模块红线（AGENTS.md：不得直写其他模块的 schema）：决策链表归
``decision_chain`` 模块、审计表归 ``delivery`` 模块，本服务**不摸别人的表**——
两个删除各自由所属模块的实现执行，这里只定义端口（Protocol），由组合根装配。
"""

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from repomesh.modules.repository_intelligence.contracts import IssueArchiveView
from repomesh.shared.domain import new_id
from repomesh.shared.events import ActorType, EventEnvelope

__all__ = [
    "DecisionChainPurge",
    "DeliveryAuditPurge",
    "IssuePurgeArchives",
    "IssuePurgeNotArchived",
    "IssuePurgeService",
    "IssuePurgeSnapshots",
]


class IssuePurgeNotArchived(Exception):
    """清除只对**已归档**的 issue 开放：先归档再清除，两步确认各自成立。"""


class IssuePurgeArchives(Protocol):
    """The archive tombstone probe: purge requires the tombstone to exist."""

    async def get(self, issue_id: UUID) -> IssueArchiveView | None: ...


class IssuePurgeSnapshots(Protocol):
    """repository_intelligence 自己的快照存储（模块内删除，无跨模块问题）。"""

    async def delete_for_project(self, project_id: UUID) -> int: ...


class DecisionChainPurge(Protocol):
    """decision_chain 模块的清除实现：删该项目的全部决策链节点与向量。"""

    async def purge_for_project(self, project_id: UUID) -> int: ...


class DeliveryAuditPurge(Protocol):
    """delivery 模块的审计清除实现：删该项目全部审计事件后**同一事务**写入
    ``keep``（IssuePurged）。「删光了却没留下清除记录」不许发生。"""

    async def purge_for_project(self, project_id: UUID, keep: EventEnvelope) -> int: ...


class IssuePurgeService:
    """彻底清除一个已归档 issue 的业务事实；审计只留一条 IssuePurged。

    清除**不是**归档的重放语义：重复清除只是反复零删除（计数为 0，不是错误）。
    顺序刻意为先删快照与决策链、最后清审计并写入保留事件——若审计清除前的
    任何一步崩溃，审计流里仍能看到之前的全部历史；最坏情况是「没删干净」，
    绝不会是「删了却没有记录」。"""

    def __init__(
        self,
        archives: IssuePurgeArchives,
        snapshots: IssuePurgeSnapshots,
        decision_chain: DecisionChainPurge,
        audit: DeliveryAuditPurge,
    ) -> None:
        self._archives = archives
        self._snapshots = snapshots
        self._decision_chain = decision_chain
        self._audit = audit

    async def purge(self, issue_id: UUID) -> dict[str, int]:
        archive = await self._archives.get(issue_id)
        if archive is None:
            raise IssuePurgeNotArchived(
                "only an archived issue can be purged; archive it first "
                "(purge is the irreversible second step, not a bigger archive)"
            )

        snapshots = await self._snapshots.delete_for_project(issue_id)
        chain_nodes = await self._decision_chain.purge_for_project(issue_id)

        # Actor follows the archive event's: the API service action, not a
        # guessed caller identity (ACTION_TOKEN already gated the route).
        keep = EventEnvelope(
            event_type="IssuePurged",
            actor_type=ActorType.SERVICE,
            actor_id="repomesh-api",
            aggregate_type="Project",
            aggregate_id=issue_id,
            aggregate_version=1,
            correlation_id=new_id(),
            project_id=issue_id,
            payload={
                "purgedAt": datetime.now(UTC).isoformat(),
                "snapshotCount": snapshots,
                "decisionChainNodeCount": chain_nodes,
            },
        )
        audit_events = await self._audit.purge_for_project(issue_id, keep)

        return {
            "snapshots": snapshots,
            "decision_chain_nodes": chain_nodes,
            "audit_events": audit_events,
        }
