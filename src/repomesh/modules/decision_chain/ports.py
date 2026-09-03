"""Ports for the decision-chain read side (contract decision-chain-v0.1 §5).

The store is the only writer of ``decision_chain_nodes``; the projector
(application layer) is the only caller of ``append``. ``trace`` and
``find_similar_structural`` stay read-only. The event source and requirement
reader are internal seams so the module never touches another module's schema:
events come from the shared ``platform.audit_events`` table and the requirement
text comes through a composition-root adapter over ``PlanSnapshotStore``.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from repomesh.modules.decision_chain.contracts import (
    DecisionChainNodes,
    DecisionChainSummaryView,
    DecisionNodeInput,
    DecisionNodeView,
    DecisionStep,
    EmbeddedDecision,
    RequirementView,
)
from repomesh.shared.events import EventEnvelope


class DecisionChainStore(Protocol):
    """§5 port, implemented by the Postgres store (and an in-memory twin)."""

    async def append(self, node: DecisionNodeInput) -> DecisionNodeView:
        """Upsert one decision sheet.

        Idempotent on ``event_id`` (a replayed event returns the existing
        row); otherwise versions within ``(project_id, step)`` and links
        ``upstream_ref`` to the newest node of the previous step.
        """
        ...

    async def latest_node(
        self, project_id: UUID, step: DecisionStep
    ) -> DecisionNodeView | None:
        """The highest-version node for one project/step (projector helper)."""
        ...

    async def trace(
        self, *, organization_id: UUID | None, project_id: UUID
    ) -> DecisionChainNodes:
        """§6.1 node list ordered by business_time + legacy gaps.

        ``organization_id`` None = across every organization (the audit
        "search by requirement id" entry has no org to pin).
        """
        ...

    async def find_similar_structural(
        self,
        *,
        organization_id: UUID,
        project_id: UUID,
        same_repository_ids: tuple[str, ...] = (),
    ) -> list[DecisionChainSummaryView]:
        """§5/Q6 structural similarity: same repositories, newest first.

        ``same_repository_ids`` carries repository *names* (the slug stored in
        ``affected_repository_ids`` at decision time), not internal UUIDs.
        """
        ...


class DecisionEventSource(Protocol):
    """Reads the five chain events out of ``platform.audit_events``."""

    async def list_chain_events(self, limit: int = 200) -> list[EventEnvelope]:
        """Unprojected chain events, oldest first (``event_id``-idempotent)."""
        ...


class RequirementReader(Protocol):
    """§6.1 chain root text, adapted in the composition root."""

    async def get_requirement(self, project_id: UUID) -> RequirementView | None:
        ...


class DecisionEmbeddingStore(Protocol):
    """L3 port over ``decision_embeddings`` (Postgres store + in-memory twin).

    The store stays a dumb projection holder: refresh and ranking semantics
    live in the application layer (B8 — the write path never calls the
    embedding service, and query vectors are produced on read).
    """

    async def upsert(self, decision_id: UUID, embedding: list[float]) -> None:
        """Insert or replace the vector for one decision sheet (idempotent)."""
        ...

    async def pending_nodes(self, limit: int = 200) -> list[DecisionNodeView]:
        """Decision sheets without a stored vector, oldest first (batch cue)."""
        ...

    async def embedded_nodes(
        self, *, organization_id: UUID | None
    ) -> list[EmbeddedDecision]:
        """Every vectorized decision sheet (``organization_id`` None = all)."""
        ...


class EmbeddingLookup(Protocol):
    """The slice of an embedding service the decision_chain module needs.

    Structurally satisfied by the OpenAI-compatible client in
    ``integrations.llm.embeddings``; the composition root wires it when
    semantic retrieval is configured (``REPOMESH_EMBEDDING_BASE_URL``).
    """

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """One embedding per input text, input order preserved."""
        ...
