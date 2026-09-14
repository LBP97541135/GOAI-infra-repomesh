"""decision_embeddings onto the pgvector column (the documented upgrade path).

Revision ID: 20260914_0056
Revises: 20260904_0055

The JSONB storage from 20260901_0053 noted pgvector as the upgrade once the
corpus justifies SQL-side ANN — this executes it. The column type and the
embedding provider's output dimension are one contract: BAAI/bge-m3 emits
1024 floats, so the physical type is ``vector(1024)`` and a provider change
means a new migration plus a re-embed, never a silent mismatch. The cosine
HNSW index serves the ``<=>`` read path.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260914_0056"
down_revision: str | None = "20260904_0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EMBEDDING_DIMENSION = 1024  # BAAI/bge-m3; keep in sync with the provider config


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        f"""
        ALTER TABLE decision_chain.decision_embeddings
        ALTER COLUMN embedding TYPE vector({_EMBEDDING_DIMENSION})
        USING (embedding::text)::vector
        """
    )
    op.execute(
        """
        CREATE INDEX ix_decision_embeddings_embedding_hnsw
        ON decision_chain.decision_embeddings
        USING hnsw (embedding vector_cosine_ops)
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS decision_chain.ix_decision_embeddings_embedding_hnsw"
    )
    op.execute(
        """
        ALTER TABLE decision_chain.decision_embeddings
        ALTER COLUMN embedding TYPE JSONB
        USING embedding::text::jsonb
        """
    )
