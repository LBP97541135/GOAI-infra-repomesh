"""Let a repository say where its tests live, not only how to run them (A-21).

0025 gave a repository a verification command and the console started supplying
it. The command then ran — and the run died anyway, because nothing had told
the Worker it was allowed to write the file the command reads.

A Worker's allowed paths come from its responsibility paths, which for the
checkout fixture are ``src/checkout/**``. ``scripts/run_tests.py`` discovers
from the repository root's ``tests/``. Those two describe different
repositories, and every agent that met the contradiction lost: the compliant
one wrote ``tests/test_discount.py`` where the command looks and the path guard
voided the whole run (``changed_path_denied``, commitSha null); the evading one
hid its test under ``src/`` where the command never finds it. The round before
had predicted it in as many words — "Allowed paths are src/checkout/**, which
excludes tests/" — and was right.

So the command and the path are one fact stored in two columns, and this is the
second. It is *added* to a task's allowed paths and never substituted for them:
a repository declaring where its tests live must not become a way to widen what
a Worker may touch anywhere else.

**Nothing is backfilled**, for the same reason as 0025: this file knows no
repository's layout, and a guessed ``tests/**`` would hand a Worker write
permission to a directory on nothing but a convention. Setting the real fixture
repositories is the operator's own auditable statement; the ``UPDATE`` is in
the delivery note, not here.

Empty stays legal and stays honest. A repository that has not said where its
tests live gives its tasks exactly the paths they had before, and an agent that
needs to write outside them is refused — loudly, by the guard, which this change
deliberately does not touch.

Revision ID: 20260812_0027
Revises: 20260812_0026
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260812_0027"
down_revision: str | None = "20260812_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "repositories",
        sa.Column(
            "test_paths",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        schema="repository_intelligence",
    )


def downgrade() -> None:
    op.drop_column("repositories", "test_paths", schema="repository_intelligence")
