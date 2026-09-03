"""HTTP response shapes for the decision-chain trace API.

Mirrors the §6.1 ``DecisionChainView`` from the module contracts — the audit
surface's wire contract. A frontend mirror must stay in sync with any field
change here (same rule as ``observability/api/models.py``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
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

    The collapsed ``DecisionChainSummaryView`` (§4/§5): for structural hits
    the latest decision of another project sharing a repository; for semantic
    hits the sheet whose embedding is closest to the probe (the match
    evidence). ``score`` is the L3 cosine similarity, present only on semantic
    hits. ``requirement_text`` is the project's requirement root sentence —
    hit cards are requirement-level ("similar requirements"), the sheet stays
    the evidence line; ``None`` is an honest gap, the card falls back to the
    sheet header.
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
    requirement_text: str | None = None


class SemanticSearchView(BaseModel):
    """Corpus-wide semantic probe (§6.5 extension, audit "search by text").

    Unlike ``SimilarDecisionsView`` (anchored to one project), this is the
    unanchored entry: embed a probe phrase and rank every other project by
    its best-matching decision sheet, across organizations unless one is
    pinned. There is no structural fallback — semantic retrieval without an
    embedding endpoint is a 503, honest configuration failure.
    """

    model_config = ConfigDict(from_attributes=True)

    organization_id: UUID | None = None
    query_text: str
    mode: Literal["semantic"] = "semantic"
    hits: list[SimilarDecisionView]


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
