"""Decision-history port (Phase 4b).

The classification pipeline injects similar historical decision chains into
the confirmation prompt ("LLM 引用历史依据作答"). The decision chain is a
sibling module's projection — this module must not import it directly, so the
pipeline depends on this port and the composition root wires an adapter
(``repomesh.modules.decision_chain`` contracts → this port) after contract
tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class SimilarDecisionSheet:
    """One collapsed historical decision (newest sheet of a similar project).

    Deliberately the same light shape as the contract's
    ``DecisionChainSummaryView``: enough for the RM to reason from, with the
    source pointers (``decision_id`` / ``project_id``) for deep reads.
    """

    decision_id: UUID
    project_id: UUID
    step: str
    status: str
    affected_repository_ids: tuple[str, ...]
    payload_summary: dict[str, Any]
    business_time: datetime


class DecisionHistoryPort(Protocol):
    """Port for similar historical decision chains (§6.5).

    The implementation only reads a projection — never the decision chain's
    own schema. Retrieval is an *enhancement*, never a blocker: a caller that
    cannot reach history must proceed without it (the pipeline treats a port
    failure as "no history").

    ``query_text`` is the L3 semantic hook: when present, an adapter with
    access to an embedding service ranks by similarity to the requirement's
    own wording; adapters without one (the Phase-4b structural adapter)
    ignore it and keep matching on repositories.
    """

    async def find_similar(
        self,
        *,
        organization_id: UUID,
        project_id: UUID,
        repository_ids: tuple[str, ...],
        top_k: int = 5,
        query_text: str | None = None,
    ) -> list[SimilarDecisionSheet]:
        """Latest decisions of other projects sharing ``repository_ids``.

        Repository ids are names/slugs, matching ``affected_repository_ids``.
        Empty list when nothing provably overlaps — no history is honest data.
        """
        ...
