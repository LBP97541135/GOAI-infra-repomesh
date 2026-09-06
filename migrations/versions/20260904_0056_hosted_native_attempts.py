"""Give hosted-native rounds their attempt table and event inbox (hosted-native M1).

0055 let a team say that its copaw workers build its code. This is where the
platform remembers what it asked one of them to build: one row of
``agent_runtime.hosted_native_attempts`` per copaw-native task directory under
the team's shared storage — the directory's name *is* the attempt id (spec
D-8) — carrying the worker-side phase, the budgets, what copaw wrote back and
the fencing data (assignment attempt, generation, execution reservation) the
attempt was opened under (D-9). The one rule the schema enforces, not the
code, is the partial unique index ``uq_hosted_native_attempts_open_task``: a
task has at most one attempt in a non-terminal phase, so two rounds racing to
open a generation cannot both publish a directory and notify the worker
(D-6). A terminal row — ``verified``, ``failed``, ``blocked``, ``fenced`` —
leaves the index and frees the task for its next generation.

``agent_runtime.hosted_native_events`` is the observer's inbox, shaped after
``delivery.scm_observations``: one row per observation of one attempt,
``UNIQUE (attempt_id, kind, marker)`` where the marker is the copaw timestamp,
the ``result.md`` etag or the Matrix event id of a Tool Guard request, so a
re-read of the same directory (or a re-delivered approval request, D-23)
inserts nothing and applies nothing twice.

**Nothing is backfilled.** No hosted-native attempt existed before this
revision: every task delivered so far went out as a runner dispatch, which has
its own tables. Both tables start empty everywhere this revision is applied.

Revision ID: 20260904_0056
Revises: 20260904_0055
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260904_0056"
down_revision: str | None = "20260904_0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The predicate of the partial unique index, spelled the same as
#: ``repomesh.integrations.hosted_native.contracts.OPEN_PHASES_SQL``. A copy
#: rather than an import for the reason 0047 and 0055 give: a migration
#: describes the schema as it was at this revision, and a phase added to the
#: enum later must not silently change what this file did. The unit tests pin
#: that the two literals agree.
_OPEN_PHASES_SQL = "phase IN ('notified', 'acknowledged', 'review_pending', 'verifying')"

_SCHEMA = "agent_runtime"


def upgrade() -> None:
    op.create_table(
        "hosted_native_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("worker_agent_id", sa.Uuid(), nullable=False),
        sa.Column("leader_agent_id", sa.Uuid(), nullable=False),
        sa.Column("team_name", sa.String(length=200), nullable=False),
        sa.Column("room_id", sa.String(length=255), nullable=False),
        sa.Column("assignment_attempt_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        # String(30) rather than a PostgreSQL enum, like every status column
        # beside it in this schema: a new phase is a code change, not a
        # migration that must run before any row can hold the value.
        sa.Column("phase", sa.String(length=30), nullable=False),
        sa.Column("package_dir", sa.String(length=500), nullable=False),
        sa.Column("base_sha", sa.String(length=64), nullable=False),
        sa.Column("review_dir", sa.String(length=500), nullable=True),
        sa.Column("budget_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("review_budget_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submit_status", sa.String(length=30), nullable=True),
        sa.Column("review_verdict", sa.String(length=20), nullable=True),
        sa.Column("verification_run_id", sa.Uuid(), nullable=True),
        sa.Column("fenced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fence_reason", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema=_SCHEMA,
    )
    # One non-terminal attempt per task. Partial so that the finished history
    # of a task can grow without bound while the live row stays unique.
    op.create_index(
        "uq_hosted_native_attempts_open_task",
        "hosted_native_attempts",
        ["task_id"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text(_OPEN_PHASES_SQL),
    )
    # Names are the ones the metadata naming convention produces for
    # ``index=True`` on the model, so autogenerate against a database at this
    # revision sees no drift.
    for column in ("task_id", "worker_agent_id", "leader_agent_id", "room_id", "phase"):
        op.create_index(
            f"ix_hosted_native_attempts_{column}",
            "hosted_native_attempts",
            [column],
            schema=_SCHEMA,
        )

    op.create_table(
        "hosted_native_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("marker", sa.String(length=200), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "attempt_id", "kind", "marker", name="uq_hosted_native_events_observation"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_hosted_native_events_attempt_id",
        "hosted_native_events",
        ["attempt_id"],
        schema=_SCHEMA,
    )


def downgrade() -> None:
    """Drop both tables, events first.

    THIS LOSES THE HOSTED-NATIVE ATTEMPT AUDIT TRAIL. Every attempt a copaw
    worker was asked to build, what it acknowledged and submitted, how the
    Leader ruled and which verifier run checked it is gone, and the observer
    no longer recognises any task directory as its own (D-6 claims by
    directory name against this table) — an attempt still in flight is
    orphaned, not fenced. Its task assignment and execution reservation
    survive in their own tables, so the recovery loop still expires the
    reservation, but nothing links the expiry back to a directory.

    Reversible for the round-trip test and for rolling back a deploy on a
    database where no hosted-native attempt has run — which is every database
    this revision is first applied to.
    """

    op.drop_index(
        "ix_hosted_native_events_attempt_id", table_name="hosted_native_events", schema=_SCHEMA
    )
    op.drop_table("hosted_native_events", schema=_SCHEMA)
    for column in ("phase", "room_id", "leader_agent_id", "worker_agent_id", "task_id"):
        op.drop_index(
            f"ix_hosted_native_attempts_{column}",
            table_name="hosted_native_attempts",
            schema=_SCHEMA,
        )
    op.drop_index(
        "uq_hosted_native_attempts_open_task",
        table_name="hosted_native_attempts",
        schema=_SCHEMA,
    )
    op.drop_table("hosted_native_attempts", schema=_SCHEMA)
