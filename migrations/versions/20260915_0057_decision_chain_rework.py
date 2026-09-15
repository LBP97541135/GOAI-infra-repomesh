"""Decision-chain rework: nodes requirement columns, pgvector vec column, feature flag.

Revision ID: 20260915_0057
Revises: 20260914_0056

Three coordinated changes over the decision-chain storage, all idempotent so
re-running against an environment where the shape already landed out-of-band
(ops-applied DDL) is a no-op:

1. ``decision_chain_nodes`` — the requirement threading columns the rework
   introduces (``requirement_text``, ``requirement_key``,
   ``affected_repositories``) plus their indexes. The event-id uniqueness the
   rework document calls for already exists as the 0033 unique constraint
   ``uq_decision_chain_nodes_event_id``; no duplicate index is created.
2. ``decision_embeddings`` — pgvector lands as an ADDITIVE column
   (``embedding_vec vector(1024)``), not an in-place type change. The JSONB
   ``embedding`` column stays the storage contract (SQLite twin keeps JSON,
   pre-migration Postgres keeps JSONB) and doubles as the vector's fallback
   copy: ``PgVectorDecisionEmbeddingStore`` dual-writes both on every upsert,
   so the ANN column and the portable JSONB copy never diverge. ``embedded_at``
   stamps when the vector was (re)written.
3. ``platform.feature_settings`` — the runtime feature-flag table plus its
   ``decision_chain`` seed row (flag on). Rows are tunable at runtime; the
   migration only guarantees the row exists.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260915_0057"
down_revision: str | None = "20260914_0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EMBEDDING_DIMENSION = 1024  # BAAI/bge-m3; keep in sync with the provider config


def upgrade() -> None:
    # --- 1. decision_chain_nodes: requirement threading columns + indexes ---
    op.execute(
        """
        ALTER TABLE decision_chain.decision_chain_nodes
        ADD COLUMN IF NOT EXISTS requirement_text text,
        ADD COLUMN IF NOT EXISTS requirement_key text,
        ADD COLUMN IF NOT EXISTS affected_repositories jsonb NOT NULL DEFAULT '[]'::jsonb
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_decision_nodes_version
        ON decision_chain.decision_chain_nodes (requirement_key, step, version)
        WHERE requirement_key IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_decision_nodes_repos
        ON decision_chain.decision_chain_nodes
        USING gin (affected_repositories)
        """
    )

    # --- 2. decision_embeddings: additive pgvector column + HNSW ---
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        f"""
        ALTER TABLE decision_chain.decision_embeddings
        ADD COLUMN IF NOT EXISTS embedding_vec vector({_EMBEDDING_DIMENSION}),
        ADD COLUMN IF NOT EXISTS embedded_at timestamptz
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_decision_embeddings_hnsw
        ON decision_chain.decision_embeddings
        USING hnsw (embedding_vec vector_cosine_ops)
        """
    )

    # --- 3. platform.feature_settings: flag table + decision_chain seed ---
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.feature_settings (
            feature text PRIMARY KEY,
            enabled boolean NOT NULL DEFAULT true,
            updated_by text NOT NULL DEFAULT '',
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        INSERT INTO platform.feature_settings (feature, enabled)
        VALUES ('decision_chain', true)
        ON CONFLICT (feature) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform.feature_settings")
    op.execute(
        "DROP INDEX IF EXISTS decision_chain.idx_decision_embeddings_hnsw"
    )
    op.execute(
        """
        ALTER TABLE decision_chain.decision_embeddings
        DROP COLUMN IF EXISTS embedded_at,
        DROP COLUMN IF EXISTS embedding_vec
        """
    )
    op.execute(
        "DROP INDEX IF EXISTS decision_chain.idx_decision_nodes_repos"
    )
    op.execute(
        "DROP INDEX IF EXISTS decision_chain.uq_decision_nodes_version"
    )
    op.execute(
        """
        ALTER TABLE decision_chain.decision_chain_nodes
        DROP COLUMN IF EXISTS affected_repositories,
        DROP COLUMN IF EXISTS requirement_key,
        DROP COLUMN IF EXISTS requirement_text
        """
    )
