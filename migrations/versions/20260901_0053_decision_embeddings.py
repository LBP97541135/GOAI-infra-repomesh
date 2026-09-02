"""decision_embeddings: L3 semantic-retrieval vector store.

Contract decision-chain-v0.1 §6.5 (semantic mode). One row per decision sheet:
``decision_id`` → the vector produced by the configured embedding endpoint
(REPOMESH_EMBEDDING_BASE_URL). The vector is stored as a JSON document rather
than a pgvector column — the corpus is small and the portable type keeps one
schema for Postgres and the SQLite test twin (the same JSON-column judgment
as decision_chain_nodes; pgvector stays the documented upgrade path).

Revision ID: 20260901_0053
Revises: 20260901_0052
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260901_0053"
down_revision: str | None = "20260901_0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "decision_embeddings",
        sa.Column("decision_id", sa.Uuid(), primary_key=True),
        sa.Column("embedding", JSON_DOCUMENT, nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema="decision_chain",
    )


def downgrade() -> None:
    op.drop_table("decision_embeddings", schema="decision_chain")
