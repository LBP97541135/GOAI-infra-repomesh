"""decision_chain_nodes: the decision-chain read-side projection table.

Contract decision-chain-v0.1 §4.1. One row per projected chain event
(decision sheet): chain fields (decision_id / event_id / project_id /
organization_id / step / version / status / actor / upstream_ref / times) plus
a ``payload_summary`` and ``evidence_refs`` pointers. Full payloads stay in
``platform.audit_events`` — this table never double-writes them.

Idempotency: ``event_id`` UNIQUE (a replay returns the existing row).
Versioning: ``(project_id, step, version)`` UNIQUE, version incremented by the
projector for same-step events (contract §4.2).

One column beyond the §4.1 list: ``event_type``, stamped by the projector so a
§6.1 trace carries the event type on every node without joining back into the
write side's retention.

Revision ID: 20260828_0033
Revises: 20260815_0032
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260828_0033"
down_revision: str | None = "20260828_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "decision_chain_nodes",
        sa.Column("decision_id", sa.Uuid(), primary_key=True),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("step", sa.String(length=30), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("actor", JSON_DOCUMENT, nullable=False),
        sa.Column("upstream_ref", sa.Uuid(), nullable=True),
        sa.Column("evidence_refs", JSON_DOCUMENT, nullable=False),
        sa.Column("payload_summary", JSON_DOCUMENT, nullable=False),
        sa.Column("affected_repository_ids", JSON_DOCUMENT, nullable=False),
        sa.Column("business_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("event_type", sa.String(length=200), nullable=False),
        sa.UniqueConstraint(
            "event_id", name="uq_decision_chain_nodes_event_id"
        ),
        sa.UniqueConstraint(
            "project_id",
            "step",
            "version",
            name="uq_decision_chain_nodes_project_step_version",
        ),
        schema="decision_chain",
    )
    op.create_index(
        "ix_decision_chain_nodes_project_id",
        "decision_chain_nodes",
        ["project_id"],
        schema="decision_chain",
    )
    op.create_index(
        "ix_decision_chain_nodes_organization_id",
        "decision_chain_nodes",
        ["organization_id"],
        schema="decision_chain",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_decision_chain_nodes_organization_id",
        table_name="decision_chain_nodes",
        schema="decision_chain",
    )
    op.drop_index(
        "ix_decision_chain_nodes_project_id",
        table_name="decision_chain_nodes",
        schema="decision_chain",
    )
    op.drop_table("decision_chain_nodes", schema="decision_chain")
