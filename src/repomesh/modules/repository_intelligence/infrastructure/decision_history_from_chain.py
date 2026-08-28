"""``DecisionHistoryPort`` adapter over the decision-chain similarity service.

repository_intelligence depends on its own port; this adapter is the only
place that names decision_chain types. It reads only the producer's contract
views (cross-module import rule — never the chain's own schema) and maps one
collapsed ``DecisionChainSummaryView`` per similar project into the light
``SimilarDecisionSheet`` the confirmation prompt consumes.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from repomesh.modules.decision_chain.contracts import DecisionChainSummaryView
from repomesh.modules.repository_intelligence.ports import SimilarDecisionSheet


class SimilarityLookup(Protocol):
    """The slice of the decision-chain similarity service this adapter needs.

    Declared here so the adapter never imports decision_chain internals; the
    composition root passes the concrete service (structural typing).
    """

    async def find_similar(
        self,
        *,
        organization_id: UUID,
        project_id: UUID,
        same_repository_ids: tuple[str, ...] = (),
        top_k: int = 5,
    ) -> Sequence[DecisionChainSummaryView]: ...


class DecisionHistoryFromChainStore:
    """Adapter: decision-chain similarity service → ``DecisionHistoryPort``.

    Retrieval stays a read over the projection; a failure propagates to the
    caller, which is contractually required to treat it as "no history"
    (see ``DecisionHistoryPort``). ``query_text`` is the L3 semantic hook:
    this structural adapter accepts it for port conformance and ignores it —
    ranking stays on repository overlap.
    """

    def __init__(self, similar: SimilarityLookup) -> None:
        self._similar = similar

    async def find_similar(
        self,
        *,
        organization_id: UUID,
        project_id: UUID,
        repository_ids: tuple[str, ...],
        top_k: int = 5,
        query_text: str | None = None,
    ) -> list[SimilarDecisionSheet]:
        sheets = await self._similar.find_similar(
            organization_id=organization_id,
            project_id=project_id,
            same_repository_ids=repository_ids,
            top_k=top_k,
        )
        return [
            SimilarDecisionSheet(
                decision_id=sheet.decision_id,
                project_id=sheet.project_id,
                step=str(sheet.step),
                status=str(sheet.status),
                affected_repository_ids=tuple(sheet.affected_repository_ids),
                payload_summary=dict(sheet.payload_summary),
                business_time=sheet.business_time,
            )
            for sheet in sheets
        ]
