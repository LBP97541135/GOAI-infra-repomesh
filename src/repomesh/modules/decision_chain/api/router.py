"""Trace API for the decision chain (contract decision-chain-v0.1 §6).

Mounts as ``/api/v1/decision-chains/*`` (the ``api_router`` adds the version
prefix). Reads are protected with the same Bearer ``agent_action_token`` as
the observability console: the trace exposes internal decision provenance,
payload summaries and actor ids, so the audit walkthrough is a privileged
consumer — the contract's public reads (``/repositories``, ``/plans/*``) stay
open; this one closes by design.
"""

from __future__ import annotations

import logging
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from repomesh.modules.decision_chain import SemanticDecisionHit
from repomesh.settings import get_settings

from .models import (
    DecisionChainView,
    EmbeddingRefreshView,
    SimilarDecisionsView,
    SimilarDecisionView,
)

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/decision-chains", tags=["decision-chain"])


def _authorized_container(request: Request):
    """Bearer-token gate shared with the observability console.

    Missing configuration fails closed (503) — an unset token must not read
    as "no authentication required" on an audit surface.
    """

    expected = get_settings().agent_action_token
    if not expected:
        raise HTTPException(
            status_code=503, detail="agent action token is not configured"
        )
    if request.headers.get("Authorization") != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="invalid agent action token")
    return request.app.state.container


@router.get("/{project_id}", response_model=DecisionChainView)
async def trace_decision_chain(
    project_id: UUID,
    request: Request,
    organization_id: UUID,
) -> DecisionChainView:
    """Contract decision-chain-v0.1 §6.1: the complete trace for one project.

    The audit walkthrough's single entry point: given a requirement id
    (``project_id``) and its L1 namespace, return the ordered chain with
    evidence pointers and the requirement root. 404 only when the org-scoped
    trace names nothing at all (no nodes and no requirement); an empty chain
    on a project that exists is a valid 200 — the projector may not have
    drained yet, and the audit surface must be able to show "no evidence yet"
    rather than pretend the project does not exist.
    """

    container = _authorized_container(request)
    view = await container.decision_chain_trace_service().trace(
        organization_id=organization_id,
        project_id=project_id,
    )
    if not view.nodes and view.requirement is None:
        raise HTTPException(
            status_code=404,
            detail="no decision chain found for this project",
        )
    return DecisionChainView.model_validate(view)


@router.get("/{project_id}/similar", response_model=SimilarDecisionsView)
async def similar_decisions(
    project_id: UUID,
    request: Request,
    organization_id: UUID,
    top_k: int = 5,
    mode: Literal["structural", "semantic"] = "structural",
    query_text: str | None = None,
) -> SimilarDecisionsView:
    """Contract decision-chain-v0.1 §6.5: similar decisions for one project.

    Q6's "同仓库 + 最近 N 条" hit for one project: the latest decision sheet of
    every other project, newest first, bounded by ``top_k``. ``mode`` selects
    the ranking:

    * ``structural`` (default, Phase 4) — repository overlap first, then
      recency; no extra configuration required.
    * ``semantic`` (L3) — cosine closeness to ``query_text``'s embedding. The
      query text is the caller's to supply (the classification pipeline passes
      the fresh requirement; a walkthrough passes a probe phrase). Fail-safe:
      no embedding endpoint, no ``query_text``, an embedding error, or an
      empty semantic corpus all fall back to ``structural`` — the response's
      ``mode`` field reports what was actually served.

    Empty ``hits`` is a valid 200 — no similar history yet is honest data.
    """

    container = _authorized_container(request)
    if mode == "semantic":
        served = await _semantic_hits(
            container,
            organization_id=organization_id,
            project_id=project_id,
            top_k=top_k,
            query_text=query_text,
        )
        if served is not None:
            return served
    hits = await container.decision_chain_similarity_service().find_similar(
        organization_id=organization_id,
        project_id=project_id,
        top_k=top_k,
    )
    return SimilarDecisionsView(
        project_id=project_id,
        organization_id=organization_id,
        mode="structural",
        hits=hits,
    )


async def _semantic_hits(
    container: Any,
    *,
    organization_id: UUID,
    project_id: UUID,
    top_k: int,
    query_text: str | None,
) -> SimilarDecisionsView | None:
    """L3 semantic ranking; ``None`` means "fall back to structural"."""

    if not query_text or not query_text.strip():
        return None
    try:
        client = container.embedding_client()
        if client is None:
            return None
        vectors = await client.embed([query_text])
        service = container.decision_chain_semantic_search_service()
        hits = await service.find_similar(
            organization_id=organization_id,
            project_id=project_id,
            query_embedding=vectors[0],
            top_k=top_k,
        )
    except Exception:
        _logger.exception("semantic similarity failed; falling back to structural")
        return None
    if not hits:
        return None
    return SimilarDecisionsView(
        project_id=project_id,
        organization_id=organization_id,
        mode="semantic",
        hits=[_semantic_sheet(hit) for hit in hits],
    )


def _semantic_sheet(hit: SemanticDecisionHit) -> SimilarDecisionView:
    """Map an L3 cosine hit onto the §6.5 wire shape (``score`` populated)."""

    d = hit.decision
    return SimilarDecisionView(
        decision_id=d.decision_id,
        project_id=d.project_id,
        organization_id=d.organization_id,
        step=d.step,
        version=d.version,
        status=d.status,
        affected_repository_ids=d.affected_repository_ids,
        payload_summary=d.payload_summary,
        business_time=d.business_time,
        score=hit.score,
    )


@router.post("/embeddings/refresh", response_model=EmbeddingRefreshView)
async def refresh_embeddings(request: Request) -> EmbeddingRefreshView:
    """L3 management endpoint: one batch refresh of ``decision_embeddings``.

    The batch service is global — every project's un-embedded sheets, oldest
    first — so the endpoint lives on the router root, not under a project.
    Without a configured embedding endpoint the service is ``None`` and the
    endpoint honestly reports 0: a no-op, not an error (B8 keeps embeddings
    off the write path; refresh is an explicit operator action).
    """

    container = _authorized_container(request)
    service = container.decision_embedding_service()
    if service is None:
        return EmbeddingRefreshView(refreshed=0)
    refreshed = await service.refresh()
    return EmbeddingRefreshView(refreshed=refreshed)
