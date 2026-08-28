"""Decision-chain contract views (contract decision-chain-v0.1 §2, §4.1, §6).

The chain is a read-side projection: one ``DecisionChainNodes`` per project,
each node being a "decision sheet" with chain fields + a lightweight
``payload_summary`` + ``evidence_refs`` pointers. Full payloads stay in the
producer modules; this module never double-writes them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class DecisionStep(StrEnum):
    """§2.1 five chain steps (the requirement root is ``project_id`` itself)."""

    CLASSIFICATION = "classification"
    CONFIRMATION = "confirmation"
    INTEGRATION = "integration"
    TASK = "task"
    PR = "pr"

    @property
    def chain_order(self) -> int:
        return {
            DecisionStep.CLASSIFICATION: 0,
            DecisionStep.CONFIRMATION: 1,
            DecisionStep.INTEGRATION: 2,
            DecisionStep.TASK: 3,
            DecisionStep.PR: 4,
        }[self]


class DecisionStatus(StrEnum):
    """§2.2 allowed statuses across the five steps (union)."""

    PROPOSED = "proposed"
    ADJUSTED = "adjusted"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"
    BLOCKED = "blocked"
    SUPERSEDED = "superseded"
    MERGED = "merged"
    CLOSED = "closed"


class NodeSource(StrEnum):
    """§4.1 ``source``: where a node came from."""

    EVENT = "event"
    BACKFILL = "backfill"
    LEGACY = "legacy"


# §3.1 event-type table: the five chain events this module subscribes to.
CHAIN_EVENT_TYPES: tuple[str, ...] = (
    "ClassificationDecided",
    "ConfirmationDecided",
    "IntegrationDecided",
    "TasksPlanned",
    "PullRequestObserved",
)


@dataclass(frozen=True, slots=True)
class NodeActor:
    """§4.1 ``actor``: who made the decision.

    ``type`` is ``llm`` | ``human`` | ``service`` (the last covers the PR
    observation node, whose actor is a SERVICE envelope); ``agent_id`` is the
    acting agent for llm/human decisions.
    """

    type: str
    agent_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class DecisionNodeInput:
    """The projected decision sheet handed to ``DecisionChainStore.append``.

    The application projector maps an ``EventEnvelope`` into this shape
    (status/actor/payload_summary/evidence_refs); the store is responsible for
    idempotency (``event_id``), versioning and ``upstream_ref`` linking.
    ``upstream_ref_hint`` carries step-specific linking hints — for ``pr`` the
    first ``task_ids`` entry, so the chain can point at the concrete task node
    when it exists.
    """

    event_id: UUID
    project_id: UUID
    organization_id: UUID
    step: DecisionStep
    status: DecisionStatus
    actor: NodeActor
    business_time: datetime
    event_type: str
    evidence_refs: dict[str, list[str]]
    payload_summary: dict[str, Any]
    affected_repository_ids: list[str]
    upstream_ref_hint: UUID | None = None


@dataclass(frozen=True, slots=True)
class DecisionNodeView:
    """§4.1 one projected decision sheet."""

    decision_id: UUID
    event_id: UUID
    project_id: UUID
    organization_id: UUID
    step: DecisionStep
    version: int
    status: DecisionStatus
    actor: NodeActor
    upstream_ref: UUID | None
    evidence_refs: dict[str, list[str]]
    payload_summary: dict[str, Any]
    affected_repository_ids: list[str]
    business_time: datetime
    recorded_at: datetime
    source: NodeSource
    event_type: str


@dataclass(frozen=True, slots=True)
class DecisionChainNodes:
    """§6.1 node list + legacy gaps (the store's slice of a trace)."""

    project_id: UUID
    organization_id: UUID
    nodes: list[DecisionNodeView]
    legacy_gaps: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RequirementView:
    """§6.1 the chain root, read through the ``RequirementReader`` port."""

    text: str
    plan_version: int
    snapshot_id: UUID


@dataclass(frozen=True, slots=True)
class DecisionChainView:
    """§6.1 complete trace output consumed by the audit surface."""

    project_id: UUID
    organization_id: UUID
    requirement: RequirementView | None
    nodes: list[DecisionNodeView]
    legacy_gaps: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DecisionChainSummaryView:
    """§4/§5 similarity result: one decision per summary (Phase 4 consumer)."""

    decision_id: UUID
    project_id: UUID
    organization_id: UUID
    step: DecisionStep
    version: int
    status: DecisionStatus
    affected_repository_ids: list[str]
    payload_summary: dict[str, Any]
    business_time: datetime


@dataclass(frozen=True, slots=True)
class EmbeddedDecision:
    """L3 candidate: one decision sheet plus its stored vector.

    Produced by the embedding store's read path; the semantic search service
    owns the project-collapse and cosine ranking (B8: query vectors are
    produced on read, embeddings are refreshed off the write path).
    """

    node: DecisionNodeView
    embedding: list[float]


@dataclass(frozen=True, slots=True)
class SemanticDecisionHit:
    """L3 result: one similar decision, scored by cosine similarity."""

    score: float
    decision: DecisionChainSummaryView
