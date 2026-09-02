"""L3 ``decision_embeddings`` store on the ``decision_chain`` schema.

The store is a dumb projection holder: ``upsert`` is idempotent per decision
sheet, ``pending_nodes`` is the batch-refresh cue (B8 — off the write path,
oldest first), and ``embedded_nodes`` feeds the read path. Ranking and the
project collapse live in ``DecisionChainSemanticSearchService``, not here.
The join to ``decision_chain_nodes`` reuses the module's own row models, so
no dialect divergence between Postgres and the SQLite test twin.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from repomesh.modules.decision_chain.contracts import (
    DecisionNodeView,
    EmbeddedDecision,
)
from repomesh.modules.decision_chain.infrastructure.models import (
    DecisionEmbeddingRecord,
    DecisionNodeRecord,
)
from repomesh.modules.decision_chain.infrastructure.postgres_store import _hydrate
from repomesh.persistence import Database


class PostgresDecisionEmbeddingStore:
    """``DecisionEmbeddingStore`` port on ``decision_chain`` schema."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def upsert(self, decision_id: UUID, embedding: list[float]) -> None:
        """Insert or replace the vector for one decision sheet (idempotent)."""
        async with self._database.transaction() as session:
            existing = await session.scalar(
                select(DecisionEmbeddingRecord).where(
                    DecisionEmbeddingRecord.decision_id == decision_id
                )
            )
            if existing is None:
                session.add(
                    DecisionEmbeddingRecord(
                        decision_id=decision_id, embedding=embedding
                    )
                )
            else:
                existing.embedding = embedding

    async def pending_nodes(self, limit: int = 200) -> list[DecisionNodeView]:
        """Decision sheets without a stored vector, oldest first (batch cue)."""
        async with self._database.transaction() as session:
            records = (
                await session.scalars(
                    select(DecisionNodeRecord)
                    .outerjoin(
                        DecisionEmbeddingRecord,
                        DecisionEmbeddingRecord.decision_id
                        == DecisionNodeRecord.decision_id,
                    )
                    .where(DecisionEmbeddingRecord.decision_id.is_(None))
                    .order_by(DecisionNodeRecord.recorded_at)
                    .limit(limit)
                )
            ).all()
        return [_hydrate(record) for record in records]

    async def embedded_nodes(
        self, *, organization_id: UUID | None
    ) -> list[EmbeddedDecision]:
        """Every vectorized decision sheet (``organization_id`` None = all)."""
        async with self._database.transaction() as session:
            query = (
                select(DecisionNodeRecord, DecisionEmbeddingRecord.embedding)
                .join(
                    DecisionEmbeddingRecord,
                    DecisionEmbeddingRecord.decision_id
                    == DecisionNodeRecord.decision_id,
                )
            )
            if organization_id is not None:
                query = query.where(
                    DecisionNodeRecord.organization_id == organization_id
                )
            rows = (await session.execute(query)).all()
        return [
            EmbeddedDecision(
                node=_hydrate(record),
                embedding=list(embedding or []),
            )
            for record, embedding in rows
        ]
