"""``DecisionHistoryPort`` adapter: hybrid semantic retrieval (L3).

repository_intelligence depends on its own port; this adapter is the only
place that names decision_chain contract types. Retrieval is an enhancement,
never a blocker — the same Phase-4b rule: an unconfigured embedding service,
an embedding failure or an empty semantic hit all fall back to the
structural adapter (same-repository overlap) when one is wired, and to "no
history" otherwise.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from repomesh.modules.decision_chain.contracts import SemanticDecisionHit
from repomesh.modules.repository_intelligence.infrastructure.decision_history_from_chain import (
    DecisionHistoryFromChainStore,
)
from repomesh.modules.repository_intelligence.ports import SimilarDecisionSheet


class SemanticLookup(Protocol):
    """The slice of ``DecisionChainSemanticSearchService`` this adapter needs."""

    async def find_similar(
        self,
        *,
        organization_id: UUID,
        project_id: UUID,
        query_embedding: list[float],
        top_k: int = 5,
        same_repository_ids: tuple[str, ...] = (),
    ) -> list[SemanticDecisionHit]: ...


class EmbeddingLookupLike(Protocol):
    """Structurally satisfied by ``integrations.llm.embeddings``."""

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


def _sheet_from_semantic(hit: SemanticDecisionHit) -> SimilarDecisionSheet:
    return SimilarDecisionSheet(
        decision_id=hit.decision.decision_id,
        project_id=hit.decision.project_id,
        step=str(hit.decision.step),
        status=str(hit.decision.status),
        affected_repository_ids=tuple(hit.decision.affected_repository_ids),
        payload_summary=dict(hit.decision.payload_summary),
        business_time=hit.decision.business_time,
    )


class DecisionHistoryVectorStore:
    """Hybrid ``DecisionHistoryPort``: semantic ranking over repository scope.

    The query vector is produced on read from the requirement's own wording
    (B8 — never on the write path); the semantic service hard-filters the
    candidate pool by the same repository slugs the structural search uses,
    then orders by cosine closeness. Any failure degrades to the structural
    adapter instead of blocking classification.
    """

    def __init__(
        self,
        semantic: SemanticLookup,
        embeddings: EmbeddingLookupLike,
        *,
        structural: DecisionHistoryFromChainStore | None = None,
    ) -> None:
        self._semantic = semantic
        self._embeddings = embeddings
        self._structural = structural

    async def find_similar(
        self,
        *,
        organization_id: UUID,
        project_id: UUID,
        repository_ids: tuple[str, ...],
        top_k: int = 5,
        query_text: str | None = None,
    ) -> list[SimilarDecisionSheet]:
        if not query_text or not query_text.strip():
            return await self._fallback(
                organization_id=organization_id,
                project_id=project_id,
                repository_ids=repository_ids,
                top_k=top_k,
            )
        try:
            vectors = await self._embeddings.embed([query_text])
            query_embedding = vectors[0]
        except Exception:  # noqa: BLE001 - history is an enhancement, not a gate
            return await self._fallback(
                organization_id=organization_id,
                project_id=project_id,
                repository_ids=repository_ids,
                top_k=top_k,
            )
        try:
            hits = await self._semantic.find_similar(
                organization_id=organization_id,
                project_id=project_id,
                query_embedding=query_embedding,
                top_k=top_k,
                same_repository_ids=repository_ids,
            )
        except Exception:  # noqa: BLE001 - history is an enhancement, not a gate
            return await self._fallback(
                organization_id=organization_id,
                project_id=project_id,
                repository_ids=repository_ids,
                top_k=top_k,
            )
        if hits:
            return [_sheet_from_semantic(hit) for hit in hits]
        # A semantic miss still deserves the structural overlap answer.
        return await self._fallback(
            organization_id=organization_id,
            project_id=project_id,
            repository_ids=repository_ids,
            top_k=top_k,
        )

    async def _fallback(
        self,
        *,
        organization_id: UUID,
        project_id: UUID,
        repository_ids: tuple[str, ...],
        top_k: int,
    ) -> list[SimilarDecisionSheet]:
        if self._structural is None:
            return []
        return await self._structural.find_similar(
            organization_id=organization_id,
            project_id=project_id,
            repository_ids=repository_ids,
            top_k=top_k,
        )
