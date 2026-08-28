"""Create the table a batch parked for an external Repository Leader lives in.

``LeaderAssignmentRecord`` is written by ``AdvanceExecutionPlan._assign_batch``
whenever a team's decomposition mode is ``leader``, and read by every request
to ``/agent-actions/leader/assignments/{taskId}``. Without this revision a
production database built by ``alembic upgrade head`` would answer the whole
leader-actions surface with ``UndefinedTable`` while the SQLite suite stayed
green, because the test schema comes from ``metadata.create_all`` -- the exact
shape revision 0036 was written to repair for ``handoff_docs``.

Columns, index names and the primary key name mirror the model exactly, so
``alembic revision --autogenerate`` on a database at this revision sees no
drift.

The primary key is the leader task id rather than a surrogate: there is one
assignment per leader task, that id is the only path parameter the frozen
contract has, and it is the whole of the write's idempotency -- a re-run of a
batch INSERTs the same key and is refused, which is what keeps a leader's
safety envelope from moving underneath the plan it is writing.

``safety_envelope`` and ``worker_roster`` are documents because nothing queries
inside them: they are read back whole, by the key above, and handed to a leader
verbatim. Deliberately *no* foreign key to ``task_orchestration.tasks``: the
schema declares none anywhere (``execution_plan_tasks`` references leader task
ids the same way), and adding one here alone would make this table the only
place in the module where a task cannot be removed independently.

Scope note for whoever extends this: the leader phase state machine's later
columns -- accepted plan provenance, the submitted DAG, review revision and
findings -- are NOT created here, because nothing writes them yet. They belong
to the revision that adds the code that does.

Revision ID: 20260828_0038
Revises: 20260827_0036
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260828_0038"
down_revision: str | None = "20260827_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "leader_assignments",
        sa.Column("leader_task_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("leader_agent_id", sa.Uuid(), nullable=False),
        # planning | executing | review_due | closed, kept as a plain string
        # (the model maps LeaderAssignmentPhase by value, not as a PG enum, and
        # reaching a later phase must not need a migration to be writable).
        sa.Column("phase", sa.String(20), nullable=False),
        sa.Column("safety_envelope", _JSON_DOCUMENT, nullable=False),
        sa.Column("worker_roster", _JSON_DOCUMENT, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        # Named explicitly rather than left to the PostgreSQL default so it
        # matches the metadata naming convention in ``repomesh.persistence.base``.
        sa.PrimaryKeyConstraint("leader_task_id", name="pk_leader_assignments"),
        schema="task_orchestration",
    )
    # ``index=True`` on the four mapped id columns and on phase; the naming
    # convention omits the schema, so these are the names the model produces.
    for column in (
        "organization_id",
        "project_id",
        "repository_id",
        "leader_agent_id",
        "phase",
    ):
        op.create_index(
            f"ix_leader_assignments_{column}",
            "leader_assignments",
            [column],
            schema="task_orchestration",
        )


def downgrade() -> None:
    """Drop the table.

    THIS DESTROYS DATA. The safety envelope and worker roster a leader was
    given live only here; the leader task row survives but carries none of
    them, so downgrading past this revision leaves any parked batch
    unanswerable -- its GET becomes a 404 and the round can only be finished by
    re-materialising it. It exists so the revision is reversible for
    upgrade/downgrade round-trip tests and for rolling a bad deploy back on a
    database with no parked leader assignments worth keeping.
    """

    for column in (
        "phase",
        "leader_agent_id",
        "repository_id",
        "project_id",
        "organization_id",
    ):
        op.drop_index(
            f"ix_leader_assignments_{column}",
            table_name="leader_assignments",
            schema="task_orchestration",
        )
    op.drop_table("leader_assignments", schema="task_orchestration")
