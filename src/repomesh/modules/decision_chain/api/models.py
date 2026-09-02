"""HTTP response shapes for the decision-chain trace API.

Mirrors the §6.1 ``DecisionChainView`` from the module contracts — the audit
surface's wire contract. A frontend mirror must stay in sync with any field
change here (same rule as ``observability/api/models.py``).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from repomesh.modules.decision_chain import DecisionStatus, DecisionStep, NodeSource


class NodeActorView(BaseModel):
    """§4.1 ``actor``: llm | human | service; ``agent_id`` for llm/human."""

    model_config = ConfigDict(from_attributes=True)

    type: str
    agent_id: UUID | None = None


class RequirementView(BaseModel):
    """§6.1 chain root (requirement text + the snapshot that owns it)."""

    model_config = ConfigDict(from_attributes=True)

    text: str
    plan_version: int
    snapshot_id: UUID


class DecisionNodeView(BaseModel):
    """§4.1 one projected decision sheet."""

    model_config = ConfigDict(from_attributes=True)

    decision_id: UUID
    event_id: UUID
    project_id: UUID
    organization_id: UUID
    step: DecisionStep
    version: int
    status: DecisionStatus
    actor: NodeActorView
    upstream_ref: UUID | None
    evidence_refs: dict[str, list[str]]
    payload_summary: dict
    affected_repository_ids: list[str]
    business_time: datetime
    recorded_at: datetime
    source: NodeSource
    event_type: str


class DecisionChainView(BaseModel):
    """§6.1 complete trace output consumed by the audit surface."""

    model_config = ConfigDict(from_attributes=True)

    project_id: UUID
    organization_id: UUID
    requirement: RequirementView | None
    nodes: list[DecisionNodeView]
    legacy_gaps: list[str]


class SimilarDecisionView(BaseModel):
    """§6.5 one similar historical decision sheet (Phase 4 consumer shape).

    The collapsed ``DecisionChainSummaryView`` (§4/§5): the latest decision of
    another project sharing a repository, light enough to render in a list.
    ``score`` is the L3 cosine similarity, present only on semantic hits.
    """

    model_config = ConfigDict(from_attributes=True)

    decision_id: UUID
    project_id: UUID
    organization_id: UUID
    step: DecisionStep
    version: int
    status: DecisionStatus
    affected_repository_ids: list[str]
    payload_summary: dict
    business_time: datetime
    score: float | None = None


class SimilarDecisionsView(BaseModel):
    """§6.5 similarity result: hits newest-first, bounded by ``top_k``.

    Empty ``hits`` is a valid 200 — "no similar history yet" is honest data,
    not an error; the similarity search does not vouch for project existence
    (that is the trace endpoint's job). ``mode`` reports the mode actually
    served: semantic retrieval without an embedding endpoint falls back to
    ``structural`` (fail-safe, the Phase-4b rule).
    """

    model_config = ConfigDict(from_attributes=True)

    project_id: UUID
    organization_id: UUID
    mode: str = "structural"
    hits: list[SimilarDecisionView]


class EmbeddingRefreshView(BaseModel):
    """L3 batch-refresh result: how many decision sheets were embedded."""

    model_config = ConfigDict(from_attributes=True)

    refreshed: int
