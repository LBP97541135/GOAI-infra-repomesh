"""pgvector-backed embedding store: SQL-side ANN on ``decision_embeddings``.

Since migration ``20260914_0056`` the Postgres column is ``vector(1024)``
(cosine HNSW). This store subclasses the JSONB store and overrides every path
that touches the embedding column:

* ``nearest`` is the new capability — ``ORDER BY embedding <=> :query`` over
  the HNSW index (the documented upgrade path, now executed);
* ``upsert``/``embedded_nodes`` speak explicit ``CAST(... AS vector)`` SQL —
  the ORM column stays declared ``JSON_DOCUMENT`` (the SQLite twin's type), so
  no ORM statement may ever bind or decode the physical vector column.

Every method probes ``pg_extension`` once; without the extension the class
degrades to the inherited JSONB behaviour (pre-migration environments) and
``nearest`` reports ``None`` — the application layer then ranks in Python, so
``vector`` absence is a degrade, never a failure.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text

from repomesh.modules.decision_chain.contracts import (
    DecisionNodeView,
    DecisionStatus,
    DecisionStep,
    EmbeddedDecision,
    NodeActor,
    NodeSource,
)
from repomesh.modules.decision_chain.infrastructure.embedding_store import (
    PostgresDecisionEmbeddingStore,
)
from repomesh.persistence import Database

EMBEDDING_DIMENSION = 1024  # BAAI/bge-m3; keep in sync with migration 0056

_NODE_COLUMNS = """
    n.decision_id, n.event_id, n.project_id, n.organization_id,
    n.step, n.version, n.status, n.actor, n.upstream_ref,
    n.evidence_refs, n.payload_summary, n.affected_repository_ids,
    n.business_time, n.recorded_at, n.source, n.event_type
"""

_PROBE_SQL = text(
    "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')"
)

_UPSERT_SQL = text(
    """
    INSERT INTO decision_chain.decision_embeddings (decision_id, embedding)
    VALUES (CAST(:decision_id AS uuid), CAST(:embedding AS vector))
    ON CONFLICT (decision_id)
    DO UPDATE SET embedding = CAST(:embedding AS vector)
    """
)

_EMBEDDED_SQL = text(
    f"""
    SELECT {_NODE_COLUMNS}, e.embedding::text AS embedding_text
    FROM decision_chain.decision_embeddings e
    JOIN decision_chain.decision_chain_nodes n ON n.decision_id = e.decision_id
    WHERE CAST(:organization_id AS uuid) IS NULL
       OR n.organization_id = CAST(:organization_id AS uuid)
    """
)

_NEAREST_SQL = text(
    f"""
    SELECT {_NODE_COLUMNS},
           e.embedding <=> CAST(:query AS vector) AS distance
    FROM decision_chain.decision_embeddings e
    JOIN decision_chain.decision_chain_nodes n ON n.decision_id = e.decision_id
    WHERE CAST(:organization_id AS uuid) IS NULL
       OR n.organization_id = CAST(:organization_id AS uuid)
    ORDER BY distance
    LIMIT :limit
    """
)


def _to_db(vector: list[float]) -> str:
    """pgvector text form (``[0.1,0.2,...]``) for ``CAST(:v AS vector)``."""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _row_to_node(row: object) -> DecisionNodeView:
    actor = row.actor or {}
    return DecisionNodeView(
        decision_id=row.decision_id,
        event_id=row.event_id,
        project_id=row.project_id,
        organization_id=row.organization_id,
        step=DecisionStep(row.step),
        version=row.version,
        status=DecisionStatus(row.status),
        actor=NodeActor(
            type=str(actor.get("type") or "llm"),
            agent_id=UUID(str(actor["agent_id"])) if actor.get("agent_id") else None,
        ),
        upstream_ref=row.upstream_ref,
        evidence_refs=row.evidence_refs or {},
        payload_summary=row.payload_summary or {},
        affected_repository_ids=list(row.affected_repository_ids or []),
        business_time=_aware(row.business_time),
        recorded_at=_aware(row.recorded_at),
        source=NodeSource(row.source),
        event_type=row.event_type,
    )


class PgVectorDecisionEmbeddingStore(PostgresDecisionEmbeddingStore):
    """``DecisionEmbeddingStore`` port with the SQL-side ANN capability.

    Same JSONB fallback semantics as the base class whenever the ``vector``
    extension is absent, so one composition works across schema states.
    """

    def __init__(self, database: Database) -> None:
        super().__init__(database)
        self._vector_ready: bool | None = None

    async def _probe(self) -> bool:
        if self._vector_ready is None:
            async with self._database.transaction() as session:
                # Dialect guard first: the SQLite twin has no pg_extension and
                # must never see the vector path (its JSON column IS the
                # contract there — probe result is false without querying).
                is_pg = session.get_bind().dialect.name == "postgresql"
                self._vector_ready = is_pg and bool(
                    await session.scalar(_PROBE_SQL)
                )
        return self._vector_ready

    async def upsert(self, decision_id: UUID, embedding: list[float]) -> None:
        if not (await self._probe()):
            await super().upsert(decision_id, embedding)
            return
        async with self._database.transaction() as session:
            await session.execute(
                _UPSERT_SQL,
                {"decision_id": str(decision_id), "embedding": _to_db(embedding)},
            )

    async def embedded_nodes(
        self, *, organization_id: UUID | None
    ) -> list[EmbeddedDecision]:
        if not (await self._probe()):
            return await super().embedded_nodes(organization_id=organization_id)
        async with self._database.transaction() as session:
            rows = (
                await session.execute(
                    _EMBEDDED_SQL,
                    {
                        "organization_id": str(organization_id)
                        if organization_id
                        else None
                    },
                )
            ).all()
        return [
            EmbeddedDecision(
                node=_row_to_node(row),
                embedding=list(json.loads(row.embedding_text)),
            )
            for row in rows
        ]

    async def nearest(
        self,
        query_embedding: list[float],
        *,
        organization_id: UUID | None,
        limit: int = 500,
    ) -> list[tuple[DecisionNodeView, float]] | None:
        """ANN-ranked sheets, closest first; ``None`` = rank in Python.

        ``None`` is the honest degrade when the vector extension is absent —
        the caller falls back to loading the slice and ranking in Python
        rather than failing the search.
        """
        if not (await self._probe()):
            return None
        async with self._database.transaction() as session:
            rows = (
                await session.execute(
                    _NEAREST_SQL,
                    {
                        "query": _to_db(query_embedding),
                        "organization_id": str(organization_id)
                        if organization_id
                        else None,
                        "limit": limit,
                    },
                )
            ).all()
        return [(_row_to_node(row), float(row.distance)) for row in rows]
