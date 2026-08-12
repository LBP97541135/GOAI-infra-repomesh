"""Let a repository say how it is verified (defect A-19).

``TaskNode.tests`` has always carried the commands a Worker must run before it
may report a task done, and its own docstring says the integration LLM does not
emit them, so "the caller supplies them when materialising a plan". The script
era's caller did. The console's does not, and there was nowhere for it to read
them from — so every console round dispatched ``testCommands: []``, the Runner
verified nothing, and the completion evidence came back with ``testResults: []``
under a green tick.

The commands belong to the repository, not to the requirement: how ``checkout``
is checked does not change because a different issue touched it. So they live
here, one row per repository, read on the materialize path of every round.

**Nothing is backfilled.** Every existing row keeps ``[]``, which is the honest
value: this migration knows of no repository's test command, and inventing
``pytest`` for forty rows would put a command that does not exist into a
dispatch and turn a missing verification into a failing one. Setting the real
fixture repositories is a separate, auditable statement the operator makes —
the ``UPDATE`` for them is in the delivery note, not in this file, because a
migration that hard-codes three product rows would re-assert them on every
environment that has never heard of them.

An empty list stays legal after this. A repository nobody has told us how to
test still produces tasks, still dispatches, and is still refused by delivery
downstream for carrying no test results — visibly, now (see the delivery
refusal recorded on the execution plan).

Revision ID: 20260812_0025
Revises: 20260812_0024
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260812_0025"
down_revision: str | None = "20260812_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "repositories",
        sa.Column(
            "test_commands",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        schema="repository_intelligence",
    )


def downgrade() -> None:
    op.drop_column("repositories", "test_commands", schema="repository_intelligence")
