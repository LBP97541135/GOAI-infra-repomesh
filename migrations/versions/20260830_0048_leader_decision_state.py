"""Give a parked leader assignment the memory its state machine needs (PR 7).

0038 created the row a batch parks in. It holds the facts that must not drift —
the safety envelope and the worker roster — and the phase, and that was enough
while ``planning`` was the only reachable phase. This revision adds what the
rest of the machine remembers as it moves.

Four columns, and each one exists because a specific frozen guarantee cannot be
kept without it (``contracts/leader-actions/v1``):

``accepted_plan`` — the leader's submitted decision, verbatim, plus the receipt
it was answered with and a fingerprint of the document. The fingerprint is the
whole of frozen invariant 2 on the plan side: an identical resubmission has to
return *the same* receipt with 200, and a different plan under the same leader
task id has to be refused rather than silently applied, and neither is decidable
without having kept what was accepted the first time.

``accepted_reviews`` — the same guarantee for verdicts, as a list rather than a
slot because ``request_rework`` opens a second review round and round 1's
receipt must stay replayable after round 2 exists. Keyed inside the document by
``review_revision``, which is the second half of the review idempotency key.

``review_revision`` — which round the next verdict judges. A column and not a
count of the list above: the revision advances when rework is accepted, and
deriving it from the list would make "no verdict yet" and "verdict 1 accepted"
the same number.

``review_evidence`` — the immutable evidence snapshot taken when the round's
worker tasks all finished (adjudication A-6). Stored rather than re-read on
every request because a verdict must be attributable to the facts it was given:
a leader that approved two green runs must not be recorded as having approved
whatever those tasks look like after a later redispatch rewrote their evidence.

Documents rather than tables for the reason 0038 gave for the envelope and the
roster: nothing queries inside them. They are read back whole, by the primary
key, to decide one assignment's next transition.

**Nothing is backfilled.** Every row written before this revision is a parked
assignment in ``planning`` — the only phase the previous slice could reach — and
that is a row with no plan, no evidence and no verdicts. The defaults say
exactly that, so there is nothing to compute per row.

Revision ID: 20260830_0048
Revises: 20260830_0047
Create Date: 2026-08-28

The chain runs 0036 → 0038 → 0037 → 0040: revision order is merge order, and
the numbers were reserved per work order in the wave-0 baseline ledger rather
than assigned in sequence. Alembic orders by ``down_revision``; renumbering to
make the filenames sort would rewrite revisions already applied to running
databases.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260830_0048"
down_revision: str | None = "20260830_0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column(
        "leader_assignments",
        sa.Column(
            "review_revision",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        schema="task_orchestration",
    )
    # Nullable, and null means "none yet" rather than "empty one": an
    # assignment with no accepted plan is a different thing from one whose plan
    # created nothing, and only the first is representable.
    for column in ("accepted_plan", "review_evidence"):
        op.add_column(
            "leader_assignments",
            sa.Column(column, _JSON_DOCUMENT, nullable=True),
            schema="task_orchestration",
        )
    # NOT NULL with an empty-array default: "no verdicts yet" and "an empty
    # list of verdicts" are the same statement, so there is no null to want.
    op.add_column(
        "leader_assignments",
        sa.Column(
            "accepted_reviews",
            _JSON_DOCUMENT,
            nullable=False,
            server_default="[]",
        ),
        schema="task_orchestration",
    )


def downgrade() -> None:
    """Drop the four columns.

    THIS DESTROYS THE LEADER'S PRODUCTS. The Engineering Spec and DAG a leader
    authored live only in ``accepted_plan``; the evidence a verdict was based on
    lives only in ``review_evidence``. After a downgrade the worker tasks the
    plan created still exist and still carry their own instructions, so the
    round is not lost — but the plan that justified them, and every receipt that
    made a resubmission idempotent, is. A leader resubmitting after that is
    treated as submitting for the first time.

    It exists so the revision is reversible for the round-trip test and for
    rolling a bad deploy back on a database with no accepted plans worth
    keeping, which is the same bargain 0038 struck one revision down.
    """

    for column in ("accepted_reviews", "review_evidence", "accepted_plan", "review_revision"):
        op.drop_column("leader_assignments", column, schema="task_orchestration")
