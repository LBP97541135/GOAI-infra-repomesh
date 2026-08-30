"""Create the table a room's own messages are recorded in (PR 9).

Until now the console's room stream could only show what RepoMesh *sent*:
``collaboration.messages`` holds outbound rows, and every other item on the
stream is a projection of a fact from somewhere else. A human typing in the
team room produced nothing at all — there was no table for it to land in — so
the Room page was, for anything a person said, honestly empty.

``room_timeline_messages`` is that table, and it is deliberately *not* a
second use of ``processed_matrix_events`` one revision up the chain. That one
is a consumption cursor answering "has this event already moved a task?", and
this one is a transcript answering "what was said in this room?". The two
consume the same Matrix events and must not share a cursor: an event can be
recorded here and deliberately refused there (adjudication D-7), and both
statements are true at the same time.

Column notes, each for a reason that outlives this revision:

``event_id`` is the primary key and the whole of the ingest's idempotency. A
Matrix sync batch is replayed in full whenever any message in it fails, so the
same event arrives repeatedly in normal operation; the homeserver's globally
unique id is the only key a replay can present, and making it the key is what
makes a replay free instead of duplicating the room.

``occurred_at`` is the homeserver's ``origin_server_ts``, not our receive time.
A message delayed in transit, or recovered after a crash, still has to sort
where it happened in the room — ordering by arrival would shuffle a
conversation into the order our poller happened to see it.

``sender_matrix_user_id`` is stored raw *and* ``sender_agent_id`` is nullable.
When a Matrix user maps onto no registered principal the row keeps the raw
handle and no agent id (adjudication D-4). AC-06 forbids showing a message
under the wrong name; it does not require inventing one, and an honest unknown
is the only truthful projection of a sender we cannot identify.

``project_id``/``repository_id`` are resolved once, at ingest, from the
topology that authorized the room. They are attribution, not a foreign key
(this schema declares none anywhere), and having them on the row is what lets
the console project a message without a second lookup per message.

The composite index mirrors the model's ``ix_room_timeline_messages_room_id``:
the only read is "one room, in ``(occurred_at, event_id)`` order", and it is
made on every console poll, so it must never sort the table.

Revision ID: 20260830_0049
Revises: 20260830_0048

The chain runs 0036 -> 0038 -> 0037 -> 0040 -> 0039: revision order is merge
order, and the numbers were reserved per work order in the wave-0 baseline
ledger rather than assigned in sequence. Alembic orders by ``down_revision``,
so this revision sits at the tip despite its number, and renumbering to make
the filenames sort would rewrite revisions already applied to running
databases.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260830_0049"
down_revision: str | None = "20260830_0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "room_timeline_messages",
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("room_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("sender_matrix_user_id", sa.Text(), nullable=False),
        # Nullable: an unresolved sender is recorded as unresolved (D-4).
        sa.Column("sender_agent_id", sa.Uuid(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # Named explicitly so it matches the metadata naming convention in
        # ``repomesh.persistence.base`` rather than PostgreSQL's default.
        sa.PrimaryKeyConstraint("event_id", name="pk_room_timeline_messages"),
        schema="collaboration",
    )
    op.create_index(
        "ix_room_timeline_messages_room_id",
        "room_timeline_messages",
        ["room_id", "occurred_at", "event_id"],
        schema="collaboration",
    )
    for column in ("project_id", "repository_id", "sender_agent_id"):
        op.create_index(
            f"ix_room_timeline_messages_{column}",
            "room_timeline_messages",
            [column],
            schema="collaboration",
        )


def downgrade() -> None:
    """Drop the table.

    THIS DESTROYS THE TRANSCRIPT. Room messages live only here — RepoMesh's own
    outbound rows are in ``collaboration.messages`` and survive, but everything
    a human or an external agent typed into a room exists in this table and
    nowhere else, and the homeserver's own history is not re-ingested (the
    poller resumes from its ``since`` token, not from the beginning of time).
    A downgrade therefore loses that conversation irrecoverably.

    It exists so the revision is reversible for the round-trip test and for
    rolling a bad deploy back on a database with no transcript worth keeping —
    the same bargain 0036 and 0038 struck further down the chain.
    """

    for column in ("sender_agent_id", "repository_id", "project_id"):
        op.drop_index(
            f"ix_room_timeline_messages_{column}",
            table_name="room_timeline_messages",
            schema="collaboration",
        )
    op.drop_index(
        "ix_room_timeline_messages_room_id",
        table_name="room_timeline_messages",
        schema="collaboration",
    )
    op.drop_table("room_timeline_messages", schema="collaboration")
