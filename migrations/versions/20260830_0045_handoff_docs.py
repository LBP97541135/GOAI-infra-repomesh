"""Create the handoff document table the plan bridge already writes to.

``HandoffDocRecord`` has been mapped since the handoff document feature
landed and ``PostgresHandoffDocStore`` writes through it, but no migration
ever created the table: ``grep handoff_docs migrations/`` returned nothing.
A database built from this chain therefore serves every ``/handoff-docs``
request with ``UndefinedTable: relation
"repository_intelligence.handoff_docs" does not exist``, and plan
materialization silently degrades -- ``change_orchestration/application.py``
swallows the generation failure into a "Failed to generate handoff
documents" warning and returns a plan whose ``handoff_doc_ids`` is empty.
The feature only ever worked where the schema came from
``metadata.create_all`` (SQLite tests, dev boxes).

Columns, index names and the primary key name mirror ``HandoffDocRecord``
exactly, so ``alembic revision --autogenerate`` on a database at this
revision sees no drift. Deliberately *no* unique constraint on
``(project_id, plan_version, repository)``: the docstring calls that
combination "one document per (repository, plan_version)", but the store
upserts by primary key via ``session.merge`` and never relies on a conflict
target, and the regeneration path writes a *new* ``id`` for the same triple
before superseding the old rows. Declaring uniqueness the model does not
declare would turn a supported regeneration into an IntegrityError.

Revision ID: 20260830_0045
Revises: 20260816_0035
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260830_0045"
down_revision: str | None = "20260830_0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "handoff_docs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("repository", sa.String(200), nullable=False),
        # PENDING | APPROVED | REJECTED | SUPERSEDED, kept as a plain string
        # (the model maps HandoffDocStatus by value, not as a PG enum, and a
        # new lifecycle state must not need a migration to be writable).
        sa.Column("status", sa.String(20), nullable=False),
        # ``JSON`` with a JSONB variant, matching the model's JSON_DOCUMENT:
        # PostgreSQL gets JSONB, and the SQLite path stays reachable.
        sa.Column(
            "content",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # ``onupdate=func.now()`` on the model is client-side, so it has no
        # DDL counterpart here; the store writes ``updated_at`` explicitly.
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by_agent_id", sa.Uuid(), nullable=True),
        sa.Column("decided_by_agent_id", sa.Uuid(), nullable=True),
        # NOT NULL with no server default: the model defaults it to "" in
        # Python, and every write goes through the store.
        sa.Column("decision_reason", sa.Text(), nullable=False),
        sa.Column("superseded_by_version", sa.Integer(), nullable=True),
        # Named explicitly rather than left to the PostgreSQL default
        # (``handoff_docs_pkey``) so it matches the metadata naming
        # convention in ``repomesh.persistence.base``.
        sa.PrimaryKeyConstraint("id", name="pk_handoff_docs"),
        schema="repository_intelligence",
    )
    # ``index=True`` on the two mapped columns; the naming convention omits
    # the schema, so these are the names the model itself produces.
    op.create_index(
        "ix_handoff_docs_project_id",
        "handoff_docs",
        ["project_id"],
        schema="repository_intelligence",
    )
    op.create_index(
        "ix_handoff_docs_repository",
        "handoff_docs",
        ["repository"],
        schema="repository_intelligence",
    )


def downgrade() -> None:
    """Drop the table.

    THIS DESTROYS DATA. Every handoff document -- including the repository
    owners' APPROVED/REJECTED decisions and the reasons they gave -- lives
    only in this table; nothing else in the schema carries a copy, so
    downgrading past this revision loses the whole approval history
    irrecoverably. It exists so the revision is reversible for
    upgrade/downgrade round-trip tests and for rolling a bad deploy back on
    a database that has no handoff documents worth keeping.
    """
    op.drop_index(
        "ix_handoff_docs_repository",
        table_name="handoff_docs",
        schema="repository_intelligence",
    )
    op.drop_index(
        "ix_handoff_docs_project_id",
        table_name="handoff_docs",
        schema="repository_intelligence",
    )
    op.drop_table("handoff_docs", schema="repository_intelligence")
