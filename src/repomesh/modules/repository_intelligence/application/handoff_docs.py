"""Repository handoff documents (仓库对接文档).

A *handoff document* turns one repository's adjustment inside an
:class:`IntegratedPlan` into a human-reviewable proposal.  For every plan
version, each repository in the plan gets a document that states:

- what will change in *this* repository (the task instruction, plus the
  per-repository adjustment plan produced by the Team Manager when the
  caller supplies it),
- which interfaces *this* repository produces for other repositories to
  consume, and
- which interfaces *this* repository consumes from others.

The repository owner reviews the document and manually approves or rejects
the proposed adjustment (``PENDING → APPROVED | REJECTED``).  When a replan
produces a new plan version, the affected repositories' documents are
superseded and regenerated from the new plan, so an old approval can never
be mistaken for a decision on the new version.

The service is store-agnostic: it talks to a :class:`HandoffDocStore` port,
which the composition root backs with PostgreSQL.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID, uuid4

from opentelemetry import trace

from repomesh.shared.domain import DomainError
from repomesh.telemetry import traced

from .plan_integration import ContractSpec, IntegratedPlan

_logger = logging.getLogger(__name__)


class HandoffDocStatus(StrEnum):
    """Lifecycle of a repository handoff document."""

    PENDING = "PENDING"  # awaiting the repository owner's decision
    APPROVED = "APPROVED"  # owner allows the proposed adjustment
    REJECTED = "REJECTED"  # owner vetoes the proposed adjustment
    SUPERSEDED = "SUPERSEDED"  # replaced by a newer plan version


class HandoffDocError(DomainError):
    """A handoff document operation was rejected."""


@dataclass(frozen=True, slots=True)
class HandoffDoc:
    """One repository's adjustment proposal for a specific plan version."""

    id: UUID
    project_id: UUID
    plan_version: int
    repository: str
    status: HandoffDocStatus
    content: dict[str, Any]
    created_at: datetime
    created_by_agent_id: UUID | None = None
    decided_by_agent_id: UUID | None = None
    decision_reason: str = ""
    superseded_by_version: int | None = None

    @property
    def decision(self) -> str | None:
        """Human-readable decision: ``"approved"`` / ``"rejected"`` / None."""
        if self.status is HandoffDocStatus.APPROVED:
            return "approved"
        if self.status is HandoffDocStatus.REJECTED:
            return "rejected"
        return None


# ---------------------------------------------------------------------------
# Port
# ---------------------------------------------------------------------------


class HandoffDocStore(Protocol):
    """Persistence port for :class:`HandoffDoc` objects."""

    async def save(self, doc: HandoffDoc) -> HandoffDoc: ...

    async def get(self, doc_id: UUID) -> HandoffDoc | None: ...

    async def list_docs(
        self,
        *,
        project_id: UUID,
        plan_version: int | None = None,
        repository: str | None = None,
        status: HandoffDocStatus | None = None,
    ) -> list[HandoffDoc]: ...

    async def supersede_for_repos(
        self,
        *,
        project_id: UUID,
        repositories: Sequence[str],
        superseded_by_version: int,
    ) -> int:
        """Mark every non-superseded doc of *repositories* as SUPERSEDED.

        Documents already carrying ``superseded_by_version`` (same-version
        regenerations) are left untouched.  Returns the number of documents
        that transitioned.
        """


# ---------------------------------------------------------------------------
# Content building
# ---------------------------------------------------------------------------


def _interfaces_for_repo(
    contracts: Sequence[ContractSpec], repository: str
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Split contracts into what *repository* produces vs consumes."""
    produced: list[dict[str, str]] = []
    consumed: list[dict[str, str]] = []
    for contract in contracts:
        if contract.producer == repository:
            produced.append(
                {
                    "interface": contract.interface,
                    "consumer": contract.consumer,
                    "agreement": contract.agreement,
                }
            )
        if contract.consumer == repository:
            consumed.append(
                {
                    "interface": contract.interface,
                    "producer": contract.producer,
                    "agreement": contract.agreement,
                }
            )
    return produced, consumed


def _batch_index_for(plan: IntegratedPlan, repository: str) -> int | None:
    """Return the 1-based execution batch a repository belongs to."""
    for index, batch in enumerate(plan.execution_batches):
        if repository in batch:
            return index + 1
    return None


def build_doc_content(
    *,
    repository: str,
    plan_version: int,
    requirement: str,
    plan: IntegratedPlan,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the content payload of one repository's handoff document.

    Args:
        repository: The repository this document is addressed to.
        plan_version: The plan version the document belongs to.
        requirement: The original requirement text.
        plan: The integrated plan (task instructions + contracts + batches).
        details: Optional per-repository adjustment plan from the
            confirmation phase (``summary`` / ``changed_apis`` /
            ``changed_modules`` / ``risk``) merged into the adjustment block.
    """

    task_node = next((t for t in plan.task_dag if t.repository == repository), None)
    produced, consumed = _interfaces_for_repo(plan.contracts, repository)
    related = sorted(
        {
            contract.producer for contract in plan.contracts
            if contract.consumer == repository
        }
        | {
            contract.consumer for contract in plan.contracts
            if contract.producer == repository
        }
    )
    related = [name for name in related if name != repository]

    adjustment: dict[str, Any] = {
        "instruction": task_node.instruction if task_node else "",
        "depends_on": list(task_node.depends_on) if task_node else [],
        "parallelizable_with": list(task_node.parallelizable_with) if task_node else [],
        "execution_batch": _batch_index_for(plan, repository),
    }
    if details:
        adjustment["summary"] = str(details.get("summary", ""))
        adjustment["changed_apis"] = list(details.get("changed_apis", ()))
        adjustment["changed_modules"] = list(details.get("changed_modules", ()))
        adjustment["risk"] = str(details.get("risk", ""))

    return {
        "repository": repository,
        "plan_version": plan_version,
        "requirement": requirement,
        "engineering_spec": plan.engineering_spec,
        "adjustment": adjustment,
        "interfaces": {"produced": produced, "consumed": consumed},
        "related_repositories": related,
    }


def render_markdown(doc: HandoffDoc) -> str:
    """Render a handoff document as human-readable Markdown.

    This is the form a repository owner reads before approving or rejecting
    the proposed adjustment.
    """

    content = doc.content
    adjustment = content.get("adjustment", {})
    interfaces = content.get("interfaces", {})

    lines: list[str] = [
        f"# 对接文档：{doc.repository}",
        "",
        f"- 计划版本：v{doc.plan_version}",
        f"- 文档状态：{doc.status.value}",
        f"- 生成时间：{doc.created_at.isoformat()}",
        "",
        "## 需求",
        "",
        content.get("requirement", "") or "（无）",
        "",
        "## 项目方案摘要",
        "",
        content.get("engineering_spec", "") or "（无）",
        "",
        "## 本仓库调整方案",
        "",
    ]
    if adjustment.get("instruction"):
        lines.append(f"- 调整内容：{adjustment['instruction']}")
    if adjustment.get("summary"):
        lines.append(f"- 方案摘要：{adjustment['summary']}")
    if adjustment.get("changed_apis"):
        lines.append(f"- 变更接口：{', '.join(adjustment['changed_apis'])}")
    if adjustment.get("changed_modules"):
        lines.append(f"- 变更模块：{', '.join(adjustment['changed_modules'])}")
    if adjustment.get("depends_on"):
        lines.append(f"- 依赖前置：{', '.join(adjustment['depends_on'])}")
    if adjustment.get("parallelizable_with"):
        lines.append(f"- 可并行：{', '.join(adjustment['parallelizable_with'])}")
    if adjustment.get("execution_batch") is not None:
        lines.append(f"- 执行批次：Batch {adjustment['execution_batch']}")
    if adjustment.get("risk"):
        lines.append(f"- 风险等级：{adjustment['risk']}")

    produced = interfaces.get("produced", [])
    consumed = interfaces.get("consumed", [])
    lines += ["", "## 对外契约（本仓库作为 Producer）", ""]
    if produced:
        for item in produced:
            lines.append(
                f"- 接口 `{item['interface']}` → {item['consumer']}：{item['agreement']}"
            )
    else:
        lines.append("（无——本仓库不对外提供变更接口）")

    lines += ["", "## 消费契约（本仓库作为 Consumer）", ""]
    if consumed:
        for item in consumed:
            lines.append(
                f"- {item['producer']} → 接口 `{item['interface']}`：{item['agreement']}"
            )
    else:
        lines.append("（无——本仓库不消费其他仓库的变更接口）")

    related = content.get("related_repositories", [])
    if related:
        lines += ["", "## 相关仓库", "", "、".join(related)]

    decision = doc.decision
    decision_text = (
        "已批准"
        if decision == "approved"
        else "已拒绝"
        if decision == "rejected"
        else "待确认"
    )
    lines += [
        "",
        "## 人工确认",
        "",
        f"- 状态：{doc.status.value}",
        f"- 是否允许此修改：{decision_text}",
    ]
    if doc.decided_by_agent_id is not None:
        lines.append(f"- 确认人：{doc.decided_by_agent_id}")
    if doc.decision_reason:
        lines.append(f"- 确认理由：{doc.decision_reason}")
    if doc.superseded_by_version is not None:
        lines.append(f"- 被 v{doc.superseded_by_version} 方案替代")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class HandoffDocService:
    """Generate, list and decide repository handoff documents.

    Usage::

        service = HandoffDocService(store)
        docs = await service.generate_for_plan(
            project_id=project_id,
            plan_version=1,
            plan=plan,
            requirement="fix notification email bug",
        )
        await service.decide(
            doc_id=docs[0].id,
            approved=True,
            decided_by_agent_id=owner_agent_id,
            reason="interface change looks fine",
        )
    """

    def __init__(self, store: HandoffDocStore) -> None:
        self._store = store

    @traced("planning.handoff_docs.generate")
    async def generate_for_plan(
        self,
        *,
        project_id: UUID,
        plan_version: int,
        plan: IntegratedPlan,
        requirement: str,
        created_by_agent_id: UUID | None = None,
        repositories: Sequence[str] | None = None,
        details: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> list[HandoffDoc]:
        """Generate PENDING handoff documents for *plan*.

        Args:
            repositories: When provided (the replan path), only these
                repositories get documents and their previous documents are
                superseded first.  When omitted (the initial plan), every
                repository in the plan gets a document.
            details: Optional per-repository adjustment plan from the
                confirmation phase, keyed by repository name.

        Returns:
            The newly created PENDING documents.
        """

        target_repos = list(repositories) if repositories else [
            task.repository for task in plan.task_dag
        ]
        # De-duplicate while preserving order.
        target_repos = list(dict.fromkeys(target_repos))

        if repositories:
            superseded = await self._store.supersede_for_repos(
                project_id=project_id,
                repositories=target_repos,
                superseded_by_version=plan_version,
            )
            if superseded:
                _logger.info(
                    "handoff docs: superseded %d previous document(s) for %s",
                    superseded,
                    target_repos,
                )

        docs: list[HandoffDoc] = []
        for repository in target_repos:
            content = build_doc_content(
                repository=repository,
                plan_version=plan_version,
                requirement=requirement,
                plan=plan,
                details=details.get(repository) if details else None,
            )
            doc = HandoffDoc(
                id=uuid4(),
                project_id=project_id,
                plan_version=plan_version,
                repository=repository,
                status=HandoffDocStatus.PENDING,
                content=content,
                created_at=datetime.now(UTC),
                created_by_agent_id=created_by_agent_id,
            )
            await self._store.save(doc)
            docs.append(doc)

        span = trace.get_current_span()
        span.set_attribute("repomesh.handoff_docs.generated", len(docs))
        span.set_attribute("repomesh.handoff_docs.plan_version", plan_version)
        return docs

    @traced("planning.handoff_docs.decide")
    async def decide(
        self,
        *,
        doc_id: UUID,
        approved: bool,
        decided_by_agent_id: UUID,
        reason: str = "",
    ) -> HandoffDoc:
        """Record a repository owner's manual decision on a PENDING document.

        Raises:
            HandoffDocError: when the document does not exist or is no longer
                PENDING (already decided or superseded).
        """

        doc = await self._store.get(doc_id)
        if doc is None:
            raise HandoffDocError(f"handoff document not found: {doc_id}")
        if doc.status is not HandoffDocStatus.PENDING:
            raise HandoffDocError(
                f"document {doc_id} is {doc.status.value}, only PENDING "
                "documents can be decided"
            )

        decided = HandoffDoc(
            id=doc.id,
            project_id=doc.project_id,
            plan_version=doc.plan_version,
            repository=doc.repository,
            status=HandoffDocStatus.APPROVED
            if approved
            else HandoffDocStatus.REJECTED,
            content=doc.content,
            created_at=doc.created_at,
            created_by_agent_id=doc.created_by_agent_id,
            decided_by_agent_id=decided_by_agent_id,
            decision_reason=reason,
            superseded_by_version=doc.superseded_by_version,
        )
        await self._store.save(decided)
        _logger.info(
            "handoff doc %s for %s decided: %s (by %s)",
            doc_id,
            doc.repository,
            decided.status.value,
            decided_by_agent_id,
        )
        return decided

    async def list_docs(
        self,
        *,
        project_id: UUID,
        plan_version: int | None = None,
        repository: str | None = None,
        status: HandoffDocStatus | None = None,
    ) -> list[HandoffDoc]:
        """List documents, optionally filtered."""
        return await self._store.list_docs(
            project_id=project_id,
            plan_version=plan_version,
            repository=repository,
            status=status,
        )

    async def get_doc(self, doc_id: UUID) -> HandoffDoc | None:
        """Fetch one document by id."""
        return await self._store.get(doc_id)

    async def supersede_for_repos(
        self,
        *,
        project_id: UUID,
        repositories: Sequence[str],
        superseded_by_version: int,
    ) -> int:
        """Explicitly supersede the open documents of *repositories*."""
        return await self._store.supersede_for_repos(
            project_id=project_id,
            repositories=repositories,
            superseded_by_version=superseded_by_version,
        )
