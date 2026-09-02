"""In-memory twins of the chain store and event source.

Kept usable so orchestration and the projector can be tested without a
database (AGENTS.md: keep the mock adapter usable). Semantics mirror the
Postgres store exactly: ``event_id`` idempotency, per-step versioning, and the
same ``upstream_ref`` resolution.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from repomesh.modules.decision_chain.contracts import (
    CHAIN_EVENT_TYPES,
    DecisionChainNodes,
    DecisionChainSummaryView,
    DecisionNodeInput,
    DecisionNodeView,
    DecisionStep,
    EmbeddedDecision,
    NodeSource,
)
from repomesh.modules.decision_chain.infrastructure._links import (
    legacy_gaps,
    resolve_chain_links,
    summary,
)
from repomesh.shared.events import EventEnvelope


class InMemoryDecisionChainStore:
    """§5 port over an in-process dict (the Postgres store's test twin)."""

    def __init__(self) -> None:
        self._nodes: dict[UUID, DecisionNodeView] = {}
        self._by_event: dict[UUID, UUID] = {}

    @property
    def event_ids(self) -> set[UUID]:
        return set(self._by_event)

    async def append(self, node: DecisionNodeInput) -> DecisionNodeView:
        existing_id = self._by_event.get(node.event_id)
        if existing_id is not None:
            return self._nodes[existing_id]
        existing = [
            view for view in self._nodes.values() if view.project_id == node.project_id
        ]
        version, upstream_ref = resolve_chain_links(existing, node)
        view = DecisionNodeView(
            decision_id=uuid4(),
            event_id=node.event_id,
            project_id=node.project_id,
            organization_id=node.organization_id,
            step=node.step,
            version=version,
            status=node.status,
            actor=node.actor,
            upstream_ref=upstream_ref,
            evidence_refs=node.evidence_refs,
            payload_summary=node.payload_summary,
            affected_repository_ids=list(node.affected_repository_ids),
            business_time=node.business_time,
            recorded_at=datetime.now(UTC),
            source=NodeSource.EVENT,
            event_type=node.event_type,
        )
        self._nodes[view.decision_id] = view
        self._by_event[view.event_id] = view.decision_id
        return view

    async def latest_node(
        self, project_id: UUID, step: DecisionStep
    ) -> DecisionNodeView | None:
        same_step = [
            view
            for view in self._nodes.values()
            if view.project_id == project_id and view.step == step
        ]
        return max(same_step, key=lambda view: view.version, default=None)

    async def trace(
        self, *, organization_id: UUID, project_id: UUID
    ) -> DecisionChainNodes:
        nodes = sorted(
            (
                view
                for view in self._nodes.values()
                if view.organization_id == organization_id
                and view.project_id == project_id
            ),
            key=lambda view: (view.business_time, view.step.chain_order, view.version),
        )
        return DecisionChainNodes(
            project_id=project_id,
            organization_id=organization_id,
            nodes=nodes,
            legacy_gaps=legacy_gaps(nodes),
        )

    async def find_similar_structural(
        self,
        *,
        organization_id: UUID,
        project_id: UUID,
        same_repository_ids: tuple[str, ...] = (),
    ) -> list[DecisionChainSummaryView]:
        own_repos = set(same_repository_ids)
        if not own_repos:
            own_repos = {
                repo
                for view in self._nodes.values()
                if view.organization_id == organization_id
                and view.project_id == project_id
                for repo in view.affected_repository_ids
            }
        if not own_repos:
            return []
        by_project: dict[UUID, list[DecisionNodeView]] = {}
        for view in self._nodes.values():
            if view.organization_id != organization_id:
                continue
            if view.project_id == project_id:
                continue
            by_project.setdefault(view.project_id, []).append(view)
        results = []
        for nodes in by_project.values():
            project_repos = {
                repo for node in nodes for repo in node.affected_repository_ids
            }
            if own_repos & project_repos:
                results.append(
                    max(nodes, key=lambda node: (node.business_time, node.version))
                )
        results.sort(key=lambda node: node.business_time, reverse=True)
        return [summary(node) for node in results]


class InMemoryDecisionEventSource:
    """Feeds envelopes to the projector; skips already-projected event ids."""

    def __init__(
        self,
        events: list[EventEnvelope] | None = None,
        store: InMemoryDecisionChainStore | None = None,
    ) -> None:
        self._events = list(events or [])
        self._store = store

    async def list_chain_events(self, limit: int = 200) -> list[EventEnvelope]:
        events = [
            event
            for event in self._events
            if event.event_type in CHAIN_EVENT_TYPES
            and (
                self._store is None or event.event_id not in self._store.event_ids
            )
        ]
        events.sort(key=lambda event: (event.occurred_at, event.event_id))
        return events[:limit]


class InMemoryDecisionEmbeddingStore:
    """``DecisionEmbeddingStore`` twin over the chain store's in-memory nodes."""

    def __init__(self, chain: InMemoryDecisionChainStore) -> None:
        self._chain = chain
        self._embeddings: dict[UUID, list[float]] = {}

    async def upsert(self, decision_id: UUID, embedding: list[float]) -> None:
        self._embeddings[decision_id] = list(embedding)

    async def pending_nodes(self, limit: int = 200) -> list[DecisionNodeView]:
        pending = [
            view
            for view in self._chain._nodes.values()  # noqa: SLF001 (test twin)
            if view.decision_id not in self._embeddings
        ]
        pending.sort(key=lambda view: view.recorded_at)
        return pending[:limit]

    async def embedded_nodes(
        self, *, organization_id: UUID
    ) -> list[EmbeddedDecision]:
        return [
            EmbeddedDecision(node=view, embedding=list(self._embeddings[view.decision_id]))
            for view in self._chain._nodes.values()  # noqa: SLF001 (test twin)
            if view.organization_id == organization_id
            and view.decision_id in self._embeddings
        ]
