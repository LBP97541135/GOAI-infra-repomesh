"""Decision-chain application layer: projector + trace assembly.

The projector is the only writer of the read side. It maps the five chain
events (already persisted in ``platform.audit_events`` by the producers) into
decision sheets and hands them to ``DecisionChainStore.append``, which owns
idempotency, versioning and ``upstream_ref`` linking. Events whose
``organization_id`` or ``project_id`` cannot be proven are skipped — the chain
must never fabricate an ownership it cannot show (red line 7).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from uuid import UUID

from repomesh.modules.decision_chain.contracts import (
    CHAIN_EVENT_TYPES,
    DecisionChainNodes,
    DecisionChainSummaryView,
    DecisionChainView,
    DecisionNodeInput,
    DecisionNodeView,
    DecisionStatus,
    DecisionStep,
    EmbeddedDecision,
    NodeActor,
    SemanticDecisionHit,
)
from repomesh.modules.decision_chain.ports import (
    DecisionChainStore,
    DecisionEmbeddingStore,
    DecisionEventSource,
    EmbeddingLookup,
    RequirementReader,
)
from repomesh.shared.domain import DomainError
from repomesh.shared.events import EventEnvelope

_logger = logging.getLogger(__name__)


class DecisionChainOrganizationUnknown(DomainError):
    """§6.1: no organization pinned and the chain proves no ownership.

    ``organization_id`` is mandatory on every decision sheet (§3.1: the
    projection carries L1 itself, E2) and on the §6.1 trace output. When the
    caller does not pin one and neither the chain nodes nor the requirement
    origin can resolve it, the trace cannot prove ownership — it fails rather
    than fabricate an org it cannot show (red line 7).
    """

    def __init__(self, project_id: UUID) -> None:
        super().__init__(f"organization unresolved for project {project_id}")
        self.project_id = project_id


class DecisionChainProjectionService:
    """Drains chain events from the audit log into the chain store."""

    def __init__(
        self,
        store: DecisionChainStore,
        source: DecisionEventSource,
    ) -> None:
        self._store = store
        self._source = source

    async def drain(self, limit: int = 200) -> int:
        """Project every unprojected chain event; returns the count appended.

        Replayed events are skipped inside ``append`` (``event_id`` unique),
        so running ``drain`` twice is naturally idempotent.
        """
        events = await self._source.list_chain_events(limit)
        projected = 0
        for event in events:
            if event.organization_id is None or event.project_id is None:
                _logger.warning(
                    "decision chain: skipping %s event %s without org/project "
                    "identity (cannot prove ownership)",
                    event.event_type,
                    event.event_id,
                )
                continue
            node = await self._project_node(event)
            await self._store.append(node)
            projected += 1
        return projected

    async def _project_node(self, event: EventEnvelope) -> DecisionNodeInput:
        step = _step_for(event.event_type)
        status = _status_for(event)
        actor = await self._actor_for(event)
        evidence_refs = _evidence_refs_for(event)
        payload_summary = _payload_summary_for(event)
        upstream_ref_hint = _upstream_ref_hint_for(event)
        if step is DecisionStep.CONFIRMATION:
            payload_summary = await self._with_effective_tiers(
                event, payload_summary
            )
        return DecisionNodeInput(
            event_id=event.event_id,
            project_id=event.project_id,  # type: ignore[arg-type]
            organization_id=event.organization_id,  # type: ignore[arg-type]
            step=step,
            status=status,
            actor=actor,
            business_time=event.occurred_at,
            event_type=event.event_type,
            evidence_refs=evidence_refs,
            payload_summary=payload_summary,
            affected_repository_ids=_affected_repositories_for(event),
            upstream_ref_hint=upstream_ref_hint,
        )

    async def _with_effective_tiers(
        self,
        event: EventEnvelope,
        payload_summary: dict,
    ) -> dict:
        """§4.2: the effective tiering after an approval lives on the
        confirmation node, not the classification node (which keeps the LLM
        verdict). Rebuild it from the classification sheet + adjustments.
        """
        classification = await self._store.latest_node(
            event.project_id,  # type: ignore[arg-type]
            DecisionStep.CLASSIFICATION,
        )
        if classification is None:
            return payload_summary
        effective = dict(classification.payload_summary.get("effective_tiers", {}))
        for adjustment in event.payload.get("adjustments", []):
            repository = adjustment.get("repository")
            target = adjustment.get("to")
            if repository and target:
                effective[repository] = target
        return {**payload_summary, "effective_tiers": effective}

    async def _actor_for(self, event: EventEnvelope) -> NodeActor:
        if event.event_type == "PullRequestObserved":
            return NodeActor(type="service")
        agent_id = _as_uuid(event.actor_id)
        if event.event_type == "ConfirmationDecided":
            # The approval carries the deciding agent explicitly; that is the
            # human decision, even though the envelope actor is an AGENT.
            decided_by = event.payload.get("approval", {}).get("decided_by_agent_id")
            return NodeActor(type="human", agent_id=_as_uuid(decided_by) or agent_id)
        return NodeActor(type="llm", agent_id=agent_id)


class DecisionChainProjector:
    """Background subscription: drains the chain events on an interval.

    ``drain`` is idempotent (event_id unique) and incremental (the source
    skips already-projected ids), so the loop is safe to run continuously —
    exactly the same contract as the alerting evaluator's interval cycle.
    """

    def __init__(
        self,
        projection: DecisionChainProjectionService,
        *,
        interval_seconds: int = 30,
    ) -> None:
        self._projection = projection
        self._interval = interval_seconds
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="decision-chain-projector")

    async def close(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def drain_now(self) -> int:
        """One synchronous pass — the audit surface and tests."""
        return await self._projection.drain()

    async def _run(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._interval)
                try:
                    await self._projection.drain()
                except Exception:
                    _logger.exception("decision chain projection cycle failed")
        except asyncio.CancelledError:
            pass


class DecisionChainTraceService:
    """Assembles the complete §6.1 trace (nodes + requirement root)."""

    def __init__(
        self,
        store: DecisionChainStore,
        requirement_reader: RequirementReader,
        organization_resolver=None,
    ) -> None:
        """``organization_resolver`` resolves a project's org when the caller
        does not pin one (audit persona "input a requirement id" may not know
        which org owns it). It is consulted only after the chain nodes have
        already proven an org — node rows carry it themselves (E2).
        """
        self._store = store
        self._requirement_reader = requirement_reader
        self._organization_resolver = organization_resolver

    async def trace(
        self,
        *,
        organization_id: UUID | None,
        project_id: UUID,
    ) -> DecisionChainView:
        nodes: DecisionChainNodes = await self._store.trace(
            organization_id=organization_id,
            project_id=project_id,
        )
        requirement = await self._requirement_reader.get_requirement(project_id)
        resolved_org = organization_id
        if resolved_org is None:
            resolved_org = next(
                (node.organization_id for node in nodes.nodes if node.organization_id),
                None,
            )
            if resolved_org is None and self._organization_resolver is not None:
                resolved_org = await self._organization_resolver(project_id)
        if resolved_org is None:
            raise DecisionChainOrganizationUnknown(project_id)
        return DecisionChainView(
            project_id=project_id,
            organization_id=resolved_org,
            requirement=requirement,
            nodes=nodes.nodes,
            legacy_gaps=nodes.legacy_gaps,
        )


class DecisionChainSimilarityService:
    """§5/Q6 structural similarity: same repositories, newest first, Top-K.

    Phase 4's structured hit first (SQL overlap over ``affected_repository_ids``);
    the pgvector/embedding upgrade comes later. The repository scope defaults to
    the project's own chain nodes; a fresh requirement with no chain yet can pass
    ``same_repository_ids`` explicitly (the Phase-4 pipeline-injection caller,
    which knows the repositories at classification time).
    """

    def __init__(self, store: DecisionChainStore) -> None:
        self._store = store

    async def find_similar(
        self,
        *,
        organization_id: UUID,
        project_id: UUID,
        same_repository_ids: tuple[str, ...] = (),
        top_k: int = 5,
    ) -> list[DecisionChainSummaryView]:
        """Top-K latest decisions of other projects sharing a repository.

        Q6: 同仓库 + 最近 N 条起步. The store already orders newest-first and
        collapses each other project to its latest decision sheet; the bounded
        recency ("最近 N 条") is this ``top_k`` cut. A time window is a tuning
        knob deliberately left out of v0.1.
        """
        hits = await self._store.find_similar_structural(
            organization_id=organization_id,
            project_id=project_id,
            same_repository_ids=same_repository_ids,
        )
        return hits[: max(0, top_k)]


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity; 0.0 when either vector is empty (no signal)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _text_for(node: DecisionNodeView) -> str:
    """Deterministic search text for one decision sheet (L3 embedding input).

    Mirrors what the confirmation prompt already surfaces — step, status, the
    affected repositories and the step-specific payload summary — so a new
    requirement's vector lands close to the language it will be matched
    against. Pure and stable: a re-embed of the same sheet must produce the
    same text.
    """
    payload = node.payload_summary or {}
    repos = ", ".join(node.affected_repository_ids)
    if node.step == DecisionStep.CLASSIFICATION:
        detail = (
            f"required {payload.get('required')} maybe {payload.get('maybe')} "
            f"excluded {payload.get('excluded')}"
        )
    elif node.step == DecisionStep.CONFIRMATION:
        approval = (payload.get("approval") or {}).get("state", node.status.value)
        detail = f"approval {approval} adjustments {payload.get('adjustments')}"
    elif node.step == DecisionStep.INTEGRATION:
        detail = f"contracts {payload.get('contracts')}"
    elif node.step == DecisionStep.TASK:
        detail = f"title {payload.get('title')}"
    else:  # DecisionStep.PR
        detail = (
            f"pull request {payload.get('pull_request_number')} "
            f"{payload.get('pull_request_url')}"
        )
    return (
        f"{node.step.value} decision ({node.status.value}) on repositories: "
        f"{repos}; {detail}"
    )


class DecisionEmbeddingService:
    """L3 batch refresh: vectors off the write path (B8).

    ``refresh`` picks the oldest un-embedded sheets, embeds their search text
    in one batched call and upserts the vectors. It is the only caller of the
    embedding service on the write side of the projection; the write path
    itself never touches it.
    """

    def __init__(
        self,
        store: DecisionEmbeddingStore,
        embeddings: EmbeddingLookup,
        *,
        batch_size: int = 16,
    ) -> None:
        self._store = store
        self._embeddings = embeddings
        self._batch_size = batch_size

    async def refresh(self, limit: int = 200) -> int:
        """Embed pending sheets (oldest first); returns how many were stored."""
        pending = await self._store.pending_nodes(limit=limit)
        if not pending:
            return 0
        texts = [_text_for(node) for node in pending]
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            vectors.extend(
                await self._embeddings.embed(texts[start : start + self._batch_size])
            )
        for node, vector in zip(pending, vectors, strict=True):
            await self._store.upsert(node.decision_id, vector)
        return len(pending)


class DecisionChainSemanticSearchService:
    """L3 read path: cosine Top-K over the project-collapsed embeddings.

    The unit of retrieval is the requirement (``project_id``), not the sheet:
    every project contributes the sheet whose embedding is closest to the
    query — the match evidence the audit surface headlines — with ties broken
    to the newest sheet. Ranking a project by its newest sheet alone would let
    a semantically empty PR sheet bury a requirement whose classification
    sheet matches the probe. An explicit repository scope (the classification
    pipeline's candidate slugs) acts as a hard filter first — the hybrid mode
    — so a requirement is not offered a semantically close decision on
    repositories it never touched. The corpus is small, so ranking runs in
    Python over the store's organization slice — the same portable,
    dialect-free pattern ``find_similar_structural`` uses.
    """

    def __init__(self, store: DecisionEmbeddingStore) -> None:
        self._store = store

    async def find_similar(
        self,
        *,
        organization_id: UUID | None = None,
        project_id: UUID | None = None,
        query_embedding: list[float],
        top_k: int = 5,
        same_repository_ids: tuple[str, ...] = (),
    ) -> list[SemanticDecisionHit]:
        candidates = await self._store.embedded_nodes(
            organization_id=organization_id
        )
        scope = set(same_repository_ids)
        # Per project keep the best-matching sheet (cosine), ties to the
        # newest — the requirement is the retrieval unit, the sheet is only
        # the evidence of why it matched.
        best: dict[UUID, tuple[float, EmbeddedDecision]] = {}
        for hit in candidates:
            if project_id is not None and hit.node.project_id == project_id:
                continue
            if scope and not (scope & set(hit.node.affected_repository_ids)):
                continue
            score = _cosine(query_embedding, hit.embedding)
            previous = best.get(hit.node.project_id)
            if previous is None or (
                score,
                hit.node.business_time,
                hit.node.version,
            ) > (
                previous[0],
                previous[1].node.business_time,
                previous[1].node.version,
            ):
                best[hit.node.project_id] = (score, hit)
        scored = sorted(
            (
                SemanticDecisionHit(
                    score=score,
                    decision=DecisionChainSummaryView(
                        decision_id=hit.node.decision_id,
                        project_id=hit.node.project_id,
                        organization_id=hit.node.organization_id,
                        step=hit.node.step,
                        version=hit.node.version,
                        status=hit.node.status,
                        affected_repository_ids=list(
                            hit.node.affected_repository_ids
                        ),
                        payload_summary=dict(hit.node.payload_summary),
                        business_time=hit.node.business_time,
                    ),
                )
                for score, hit in best.values()
            ),
            key=lambda result: result.score,
            reverse=True,
        )
        return scored[: max(0, top_k)]


class DecisionEmbeddingRefresher:
    """Background interval loop: keeps ``decision_embeddings`` current.

    Same shape as ``DecisionChainProjector`` — an idempotent, incremental
    ``refresh`` pass on a timer, so the loop is safe to run continuously and
    the audit surface (and tests) call ``refresh_now`` directly.
    """

    def __init__(
        self,
        service: DecisionEmbeddingService,
        *,
        interval_seconds: int = 60,
    ) -> None:
        self._service = service
        self._interval = interval_seconds
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="decision-embedding-refresher")

    async def close(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def refresh_now(self) -> int:
        """One synchronous pass — the audit surface and tests."""
        return await self._service.refresh()

    async def _run(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._interval)
                try:
                    await self._service.refresh()
                except Exception:
                    _logger.exception("decision embedding refresh cycle failed")
        except asyncio.CancelledError:
            pass


# --- event → decision-sheet mapping helpers (pure) ---------------------------


def _step_for(event_type: str) -> DecisionStep:
    return {
        "ClassificationDecided": DecisionStep.CLASSIFICATION,
        "ConfirmationDecided": DecisionStep.CONFIRMATION,
        "IntegrationDecided": DecisionStep.INTEGRATION,
        "TasksPlanned": DecisionStep.TASK,
        "PullRequestObserved": DecisionStep.PR,
    }[event_type]


def _status_for(event: EventEnvelope) -> DecisionStatus:
    if event.event_type == "ConfirmationDecided":
        state = event.payload.get("approval", {}).get("state")
        return {
            "approved": DecisionStatus.CONFIRMED,
            "rejected": DecisionStatus.REJECTED,
            "changes_requested": DecisionStatus.CHANGES_REQUESTED,
        }.get(state, DecisionStatus.PROPOSED)
    # classification / integration / task / pr land as their first verdict.
    return DecisionStatus.PROPOSED


def _evidence_refs_for(event: EventEnvelope) -> dict[str, list[str]]:
    """§6.2 result/process split. Phase 2 fills ``result``; ``process``
    (Room-message pointers) arrives with the collaboration backfill.
    The classification fingerprint nests under ``classification`` (§3.2);
    the confirmation one sits at the top level.
    """
    if event.event_type == "ClassificationDecided":
        evidence = event.payload.get("classification", {}).get("evidence_version")
        return {"result": [evidence] if evidence else [], "process": []}
    if event.event_type == "ConfirmationDecided":
        evidence = event.payload.get("evidence_version")
        return {"result": [evidence] if evidence else [], "process": []}
    return {"result": [], "process": []}


def _payload_summary_for(event: EventEnvelope) -> dict:
    if event.event_type == "ClassificationDecided":
        classification = event.payload.get("classification", {})
        return {
            "required": list(classification.get("required", [])),
            "maybe": list(classification.get("maybe", [])),
            "excluded": list(classification.get("excluded", [])),
            "effective_tiers": dict(classification.get("effective_tiers", {})),
            "supplemented_repository_ids": list(
                classification.get("supplemented_repository_ids", [])
            ),
        }
    if event.event_type == "ConfirmationDecided":
        return {
            "approval": dict(event.payload.get("approval", {})),
            "adjustments": list(event.payload.get("adjustments", [])),
        }
    if event.event_type == "IntegrationDecided":
        return {
            "execution_batches": list(event.payload.get("execution_batches", [])),
            "contracts": list(event.payload.get("contracts", [])),
        }
    if event.event_type == "TasksPlanned":
        task = event.payload.get("task", {})
        return {
            "task_id": task.get("task_id"),
            "repository_id": task.get("repository_id"),
            "title": task.get("title"),
            "parent_task_id": task.get("parent_task_id"),
        }
    # PullRequestObserved
    return {
        "change_set_id": event.payload.get("change_set_id"),
        "repository_id": event.payload.get("repository_id"),
        "pull_request_number": event.payload.get("pull_request_number"),
        "pull_request_url": event.payload.get("pull_request_url"),
        "task_ids": list(event.payload.get("task_ids", [])),
    }


def _affected_repositories_for(event: EventEnvelope) -> list[str]:
    return list(event.payload.get("affected_repository_ids", []))


def _upstream_ref_hint_for(event: EventEnvelope) -> UUID | None:
    """Step-specific chain hint. PRs point at their task when possible."""
    if event.event_type == "PullRequestObserved":
        task_ids = event.payload.get("task_ids", [])
        if task_ids:
            return _as_uuid(task_ids[0])
    return None


def _as_uuid(value: object) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


__all__ = [
    "CHAIN_EVENT_TYPES",
    "DecisionChainProjectionService",
    "DecisionChainProjector",
    "DecisionChainSemanticSearchService",
    "DecisionChainSimilarityService",
    "DecisionChainTraceService",
    "DecisionEmbeddingRefresher",
    "DecisionEmbeddingService",
]
