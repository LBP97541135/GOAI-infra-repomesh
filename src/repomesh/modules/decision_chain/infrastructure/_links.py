"""Shared chain-link resolution used by both store twins.

Contract v0.1 §4.2: the first event of a ``(project_id, step)`` gets
``version=1`` and later same-step events increment; ``upstream_ref`` points at
the parent decision (the chain root's parent is NULL). Both rules are decided
against the rows the project already has, so the Postgres store and the
in-memory twin cannot drift.
"""

from __future__ import annotations

from uuid import UUID

from repomesh.modules.decision_chain.contracts import (
    DecisionChainSummaryView,
    DecisionNodeInput,
    DecisionNodeView,
    DecisionStep,
)

CHAIN_STEPS = (
    DecisionStep.CLASSIFICATION,
    DecisionStep.CONFIRMATION,
    DecisionStep.INTEGRATION,
    DecisionStep.TASK,
    DecisionStep.PR,
)


def legacy_gaps(nodes: list[DecisionNodeView]) -> list[str]:
    """§7: steps with no node that are followed by a later step's node.

    A hole in the middle of a chain can only mean the earlier step was never
    recorded — the five events are emitted strictly in chain order (Phase 1),
    so a project with nodes past step *N* must have had steps before *N*
    happen in the real world. A missing tail (e.g. no ``pr`` yet) is not a
    gap: the chain may simply not have reached it.
    """

    if not nodes:
        return []
    present = {node.step for node in nodes}
    return [
        step.value
        for step in CHAIN_STEPS
        if step not in present
        and any(node.step.chain_order > step.chain_order for node in nodes)
    ]


def summary(node: DecisionNodeView) -> DecisionChainSummaryView:
    """One decision sheet collapsed to the Phase-4 similarity shape."""

    return DecisionChainSummaryView(
        decision_id=node.decision_id,
        project_id=node.project_id,
        organization_id=node.organization_id,
        step=node.step,
        version=node.version,
        status=node.status,
        affected_repository_ids=node.affected_repository_ids,
        payload_summary=node.payload_summary,
        business_time=node.business_time,
    )


def resolve_chain_links(
    existing: list[DecisionNodeView],
    node: DecisionNodeInput,
) -> tuple[int, UUID | None]:
    """Version within ``(project_id, step)`` and pick ``upstream_ref``.

    ``upstream_ref`` resolution, in order:
    1. the hinted node — by ``decision_id``, or by the entity id carried in
       ``payload_summary.task_id`` (a ``pr`` node hints at its task this way) —
       when that node already exists in the project's chain;
    2. otherwise the newest node of the previous step;
    3. otherwise ``None``: the classification root, and out-of-order arrivals
       (Q5 — the node lands anyway; the trace assembles by version, so a
       missing link never hides a node).
    """

    same_step = [n for n in existing if n.step == node.step]
    version = max((n.version for n in same_step), default=0) + 1

    upstream_ref: UUID | None = None
    if node.step.chain_order > 0:
        if node.upstream_ref_hint is not None:
            hint = node.upstream_ref_hint
            for n in existing:
                if n.step.chain_order >= node.step.chain_order:
                    continue
                if n.decision_id == hint:
                    upstream_ref = n.decision_id
                    break
                if str(n.payload_summary.get("task_id", "")) == str(hint):
                    upstream_ref = n.decision_id
                    break
        if upstream_ref is None:
            previous = [
                n
                for n in existing
                if n.step.chain_order == node.step.chain_order - 1
            ]
            if previous:
                upstream_ref = max(previous, key=lambda n: n.version).decision_id
    return version, upstream_ref
