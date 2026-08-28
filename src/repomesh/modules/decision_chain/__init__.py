"""Decision-chain read side (contract decision-chain-v0.1).

The module projects the five chain events out of ``platform.audit_events``
into ``decision_chain_nodes`` (idempotent, versioned, linked) and assembles
§6.1 traces. It never writes to another module's schema and never stores full
event payloads — only ``payload_summary`` + ``evidence_refs`` pointers. L3
adds ``decision_embeddings`` (off the write path, B8) and the cosine read
path over the project-collapsed embeddings.
"""

from .application import (
    DecisionChainProjectionService,
    DecisionChainProjector,
    DecisionChainSemanticSearchService,
    DecisionChainSimilarityService,
    DecisionChainTraceService,
    DecisionEmbeddingRefresher,
    DecisionEmbeddingService,
)
from .contracts import (
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
    NodeSource,
    RequirementView,
    SemanticDecisionHit,
)
from .infrastructure import (
    InMemoryDecisionChainStore,
    InMemoryDecisionEmbeddingStore,
    InMemoryDecisionEventSource,
    PostgresDecisionChainStore,
    PostgresDecisionEmbeddingStore,
    PostgresDecisionEventSource,
)
from .ports import (
    DecisionChainStore,
    DecisionEmbeddingStore,
    DecisionEventSource,
    EmbeddingLookup,
    RequirementReader,
)

__all__ = [
    "CHAIN_EVENT_TYPES",
    "DecisionChainNodes",
    "DecisionChainProjectionService",
    "DecisionChainProjector",
    "DecisionChainSemanticSearchService",
    "DecisionChainSimilarityService",
    "DecisionChainStore",
    "DecisionChainSummaryView",
    "DecisionChainTraceService",
    "DecisionChainView",
    "DecisionEmbeddingRefresher",
    "DecisionEmbeddingService",
    "DecisionEmbeddingStore",
    "DecisionEventSource",
    "DecisionNodeInput",
    "DecisionNodeView",
    "DecisionStatus",
    "DecisionStep",
    "EmbeddedDecision",
    "EmbeddingLookup",
    "InMemoryDecisionChainStore",
    "InMemoryDecisionEmbeddingStore",
    "InMemoryDecisionEventSource",
    "NodeActor",
    "NodeSource",
    "PostgresDecisionChainStore",
    "PostgresDecisionEmbeddingStore",
    "PostgresDecisionEventSource",
    "RequirementReader",
    "RequirementView",
    "SemanticDecisionHit",
]
