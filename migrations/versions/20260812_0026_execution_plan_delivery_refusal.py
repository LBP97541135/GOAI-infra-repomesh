"""Give a refused delivery somewhere to be recorded (defect A-19's silent twin).

``PlanDeliveryFinalizer._candidates_for_batch`` refuses to publish a candidate
whose Runner evidence carries no test results. The refusal is correct — delivery
declining unverified work is the one honest thing in that whole chain — but it
was raised as a bare ``ValueError`` from inside ``_advance_if_ready``, which
runs under the Runner event ingest's best-effort handler. That handler logs and
returns, because a scheduling failure must never fail ingestion. So the refusal
was made, and dropped, several times a minute: no state written, nothing
projected, and a console showing every task green beside a change set that
would stay empty forever.

This column is where the refusal now lands. Beside the status rather than
inside it, on purpose: the plan is still IN_PROGRESS and its batch really did
succeed, so ``FAILED`` would be a lie, and un-failing it later would be a
second lie. A refusal is cleared when the delivering side stops making it —
which is what makes the round converge on its own once a re-dispatched Worker
reports evidence that has test results.

NULL is the ordinary value and means delivery has raised nothing about the
current batch. No row is backfilled: this migration cannot know which of the
plans already stuck in the crash-loop are still stuck, and asserting a refusal
that the running system has not restated would be inventing state. The next
terminal event or delivery observation on such a plan records the real one.

Revision ID: 20260812_0026
Revises: 20260812_0025
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260812_0026"
down_revision: str | None = "20260812_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "execution_plans",
        sa.Column(
            "delivery_refusal",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        schema="task_orchestration",
    )


def downgrade() -> None:
    op.drop_column("execution_plans", "delivery_refusal", schema="task_orchestration")
