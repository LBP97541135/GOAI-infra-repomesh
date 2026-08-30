"""``collaboration.room_timeline_messages`` against the real migration chain.

The SQLite suite next door builds its schema with ``metadata.create_all``, so
it has this table whether or not a migration creates it — the exact gap
revision ``20260827_0036`` was written to close for ``handoff_docs`` and
``20260828_0038`` for ``leader_assignments``. Without revision
``20260828_0039`` a production database would answer every room ingest with
``UndefinedTable`` while every unit test stayed green, and the failure would
surface as a poller that logs and retries forever with an empty Room page
behind it.

Doubles are refused on both sides: the production ``PostgresRoomTimelineStore``
over a PostgreSQL database whose schema came from the migration chain and
nothing else. Four facts are pinned:

1. at ``20260828_0040`` (everything but the new revision) the store fails with
   ``UndefinedTable``;
2. at ``head`` the write/read chain works, and a duplicate event id returns the
   row that is already there rather than raising;
3. ``(occurred_at, event_id)`` ordering and the resume cursor hold against real
   SQL, including the timestamp tie the in-memory adapter can only simulate;
4. ``head -> downgrade 0040 -> head`` round-trips.

Point it at a throwaway server::

    REPOMESH_TEST_POSTGRES_URL=postgresql+asyncpg://user:pw@127.0.0.1:15550/postgres

Each test creates its *own* database on that server, migrates it, and drops it
afterwards, so the URL's database is never migrated in place. With the variable
unset the module skips.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from repomesh.modules.collaboration.contracts import (
    RoomTimelineCursor,
    RoomTimelineEntryView,
)
from repomesh.modules.collaboration.infrastructure import PostgresRoomTimelineStore
from repomesh.persistence import Database

POSTGRES_URL = os.getenv("REPOMESH_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not POSTGRES_URL,
        reason=(
            "REPOMESH_TEST_POSTGRES_URL is not configured; the migration chain "
            "the room timeline table depends on is NOT covered by this run"
        ),
    ),
]

#: The revision that has every table *except* room_timeline_messages.
REVISION_BEFORE = "20260830_0048"
#: The revision under test.
REVISION_UNDER_TEST = "20260830_0049"
#: PostgreSQL's ``undefined_table`` SQLSTATE.
UNDEFINED_TABLE = "42P01"

_REPO_ROOT = Path(__file__).resolve().parents[2]

TEAM_ROOM = "!team-pricing:matrix.local"
LEADER_DM = "!leader-pricing:matrix.local"
PROJECT_ID = UUID("42424242-4242-4242-4242-424242424242")
REPOSITORY_ID = UUID("43434343-4343-4343-4343-434343434343")
T0 = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)


async def _admin_execute(admin_url: URL, statement: str) -> None:
    """Run one cluster-level statement outside a transaction block."""
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.execute(text(statement))
    finally:
        await engine.dispose()


@pytest.fixture
def scratch_database() -> Iterator[str]:
    """Yield the URL of a fresh, empty database that is dropped afterwards."""
    assert POSTGRES_URL is not None
    admin_url = make_url(POSTGRES_URL)
    name = f"repomesh_timeline_{uuid4().hex[:12]}"
    asyncio.run(_admin_execute(admin_url, f'CREATE DATABASE "{name}"'))
    try:
        yield admin_url.set(database=name).render_as_string(hide_password=False)
    finally:
        asyncio.run(_admin_execute(admin_url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def _alembic(url: str, *args: str) -> str:
    """Run alembic against *url* in a subprocess, returning its output.

    A subprocess rather than ``alembic.command``: ``migrations/env.py`` ends in
    ``asyncio.run(...)``, which cannot be called from inside a running event
    loop, and it overrides ``sqlalchemy.url`` from ``get_settings()``, so
    ``REPOMESH_DATABASE_URL`` in a child process is the only way to aim the
    chain at the scratch database.
    """
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(_REPO_ROOT / "alembic.ini"), *args],
        cwd=_REPO_ROOT,
        env={**os.environ, "REPOMESH_DATABASE_URL": url},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    if completed.returncode != 0:
        raise AssertionError(f"alembic {' '.join(args)} failed:\n{output}")
    return output


def _table_exists(url: str) -> bool:
    async def _check() -> bool:
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                result = await connection.execute(
                    text("SELECT to_regclass('collaboration.room_timeline_messages')")
                )
                return result.scalar() is not None
        finally:
            await engine.dispose()

    return asyncio.run(_check())


def _with_store[T](url: str, work: Callable[[PostgresRoomTimelineStore], Awaitable[T]]) -> T:
    """Run *work* against the production store."""

    async def _inner() -> T:
        database = Database(url)
        try:
            return await work(PostgresRoomTimelineStore(database))
        finally:
            await database.dispose()

    return asyncio.run(_inner())


def _entry(
    event_id: str,
    *,
    room_id: str = TEAM_ROOM,
    at: datetime = T0,
    sender_agent_id: UUID | None = None,
    sender: str = "@worker:matrix.local",
    body: str = "starting on the pricing change",
) -> RoomTimelineEntryView:
    return RoomTimelineEntryView(
        event_id=event_id,
        room_id=room_id,
        project_id=PROJECT_ID,
        repository_id=REPOSITORY_ID,
        sender_matrix_user_id=sender,
        sender_agent_id=sender_agent_id,
        body=body,
        occurred_at=at,
    )


def test_the_timeline_is_undefined_at_0040_and_whole_at_head(scratch_database: str) -> None:
    """The failure the missing migration would cause, then its absence at head."""
    url = scratch_database
    _alembic(url, "upgrade", REVISION_BEFORE)

    recorded = _entry("$evt-1", sender_agent_id=uuid4())

    # (a) Red: a database carrying every revision but the new one.
    with pytest.raises(ProgrammingError) as excinfo:
        _with_store(url, lambda store: store.add(recorded))
    assert getattr(excinfo.value.orig, "sqlstate", None) == UNDEFINED_TABLE

    # (b) Green: the same object, one revision later.
    _alembic(url, "upgrade", "head")

    assert _with_store(url, lambda store: store.add(recorded)) == recorded
    assert _with_store(url, lambda store: store.get("$evt-1")) == recorded

    # A duplicate event id — a replayed sync batch — returns the stored row
    # instead of raising, which is what makes the replay free rather than an
    # error the poller has to interpret.
    twin = _entry("$evt-1", body="a different body under the same event id")
    assert _with_store(url, lambda store: store.add(twin)) == recorded
    assert _with_store(url, lambda store: store.get("$evt-1")) == recorded

    # An unresolved sender survives the round trip as unresolved (D-4), rather
    # than coming back as a zero UUID or an empty string.
    human = _entry("$evt-2", sender="@bohan:matrix.local", at=T0.replace(minute=5))
    _with_store(url, lambda store: store.add(human))
    read_back = _with_store(url, lambda store: store.get("$evt-2"))
    assert read_back is not None
    assert read_back.sender_agent_id is None
    assert read_back.sender_matrix_user_id == "@bohan:matrix.local"

    assert _with_store(url, lambda store: store.get("$never-happened")) is None


def test_ordering_and_the_cursor_hold_in_sql(scratch_database: str) -> None:
    """The stable order is the database's, not a Python sort after the fact.

    Two messages sharing ``occurred_at`` is the case that matters: a homeserver
    stamps in milliseconds, and without the event id in both the ORDER BY and
    the resume predicate a page boundary landing on the tie would repeat one
    row or skip the other.
    """

    url = scratch_database
    _alembic(url, "upgrade", "head")

    async def _seed(store: PostgresRoomTimelineStore) -> None:
        # Inserted out of order on purpose: arrival order must not be read
        # back as message order.
        for event_id, minute in (
            ("$evt-late", 30),
            ("$evt-b", 0),
            ("$evt-a", 0),
            ("$evt-other-room", 10),
        ):
            room = LEADER_DM if event_id == "$evt-other-room" else TEAM_ROOM
            await store.add(_entry(event_id, room_id=room, at=T0.replace(minute=minute)))

    _with_store(url, _seed)

    ordered = _with_store(url, lambda store: store.list_room(TEAM_ROOM))
    assert [entry.event_id for entry in ordered] == ["$evt-a", "$evt-b", "$evt-late"]

    page = _with_store(url, lambda store: store.list_room(TEAM_ROOM, limit=2))
    assert [entry.event_id for entry in page] == ["$evt-a", "$evt-b"]

    cursor = RoomTimelineCursor(page[-1].occurred_at, page[-1].event_id)
    rest = _with_store(url, lambda store: store.list_room(TEAM_ROOM, after=cursor))
    assert [entry.event_id for entry in rest] == ["$evt-late"]

    # The other room is a different conversation, not a filter applied later.
    other = _with_store(url, lambda store: store.list_room(LEADER_DM))
    assert [entry.event_id for entry in other] == ["$evt-other-room"]


def test_head_downgrade_0040_upgrade_head_round_trips(scratch_database: str) -> None:
    """The new revision is reversible, re-appliable, and creates the table itself."""
    url = scratch_database

    _alembic(url, "upgrade", REVISION_UNDER_TEST)
    assert REVISION_UNDER_TEST in _alembic(url, "current")
    assert _table_exists(url) is True

    _alembic(url, "upgrade", "head")
    assert _table_exists(url) is True

    # Destructive on purpose: the downgrade drops the table and every recorded
    # room message with it, and the homeserver's history is not re-ingested.
    # Safe here only because the database is a scratch one this test created.
    _alembic(url, "downgrade", REVISION_BEFORE)
    assert _table_exists(url) is False

    _alembic(url, "upgrade", "head")
    assert _table_exists(url) is True

    # A second application is not just DDL-clean; the store works through it.
    recorded = _entry("$evt-after-round-trip")
    assert _with_store(url, lambda store: store.add(recorded)) == recorded
