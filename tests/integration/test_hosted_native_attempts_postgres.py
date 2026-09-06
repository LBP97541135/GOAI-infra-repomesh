"""``agent_runtime.hosted_native_attempts`` / ``hosted_native_events`` against the real chain.

The two records are mapped (``HostedNativeAttemptRecord``, ``HostedNativeEventRecord``)
and written by ``PostgresHostedNativeAttemptStore``, and the SQLite suite builds
its schema with ``metadata.create_all`` -- so it has both tables whether or not a
migration creates them. Revision ``20260904_0056`` is what creates them in
production, and this module pins that the way ``test_hosted_native_postgres.py``
pins 0055 for its column: without the revision a production database would
refuse the first ``HostedNativeRound.open()`` with ``UndefinedTable`` while every
unit test stayed green.

What the partial unique index ``uq_hosted_native_attempts_open_task`` buys is
the point of the second test: D-8 says one attempt is one copaw-native task
directory and D-9 says a task's live attempt is unique per generation, so a
second *open* row for the same task must be refused by the database itself --
two API workers racing to open a round cannot both publish a directory and
notify the worker. A terminal row (``fenced`` here) leaves the index, which is
how the next generation gets its attempt. The store carries no read-then-write
check of its own, so the ``HostedNativeConflict`` it raises at head is the
index speaking.

Three facts are pinned, over the production store and a PostgreSQL database
whose schema came from the migration chain and nothing else:

1. at ``20260904_0055`` (everything but the new revision) adding an attempt
   fails with ``UndefinedTable``;
2. at ``head`` both tables and the partial index exist, an attempt with every
   field round-trips, a second open attempt for its task is refused by the
   index (through the store and through a raw INSERT), a fenced attempt frees
   the task, and the event inbox deduplicates ``(attempt_id, kind, marker)``;
3. ``head -> downgrade 0055 -> head`` round-trips: the tables are gone and
   back, the audit trail written before the gap is gone with them (which the
   migration's docstring warns about), and a fresh attempt still works.

Point it at a throwaway server::

    REPOMESH_TEST_POSTGRES_URL=postgresql+asyncpg://user:pw@127.0.0.1:5432/postgres

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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from repomesh.integrations.hosted_native.contracts import (
    AttemptPhase,
    EventKind,
    HostedNativeAttempt,
    HostedNativeConflict,
    HostedNativeEvent,
    ReviewVerdict,
    SubmitStatus,
)
from repomesh.integrations.hosted_native.store import PostgresHostedNativeAttemptStore
from repomesh.persistence import Database

POSTGRES_URL = os.getenv("REPOMESH_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not POSTGRES_URL,
        reason=(
            "REPOMESH_TEST_POSTGRES_URL is not configured; the migration chain "
            "the hosted_native_attempts tables depend on is NOT covered by this run"
        ),
    ),
]

#: The revision that has every table *except* the two hosted-native ones.
REVISION_BEFORE = "20260904_0055"
#: The revision under test.
REVISION_UNDER_TEST = "20260904_0056"
#: PostgreSQL's ``undefined_table`` SQLSTATE.
UNDEFINED_TABLE = "42P01"
#: PostgreSQL's ``unique_violation`` SQLSTATE.
UNIQUE_VIOLATION = "23505"

SCHEMA = "agent_runtime"
ATTEMPTS_TABLE = "hosted_native_attempts"
EVENTS_TABLE = "hosted_native_events"
OPEN_TASK_INDEX = "uq_hosted_native_attempts_open_task"

_REPO_ROOT = Path(__file__).resolve().parents[2]

TASK_ID = UUID("61616161-6161-6161-6161-616161616161")
NOW = datetime.now(UTC)


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
    name = f"repomesh_hosted_native_attempts_{uuid4().hex[:12]}"
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


def _scalar(url: str, statement: str, **params: object) -> object:
    async def _query() -> object:
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                result = await connection.execute(text(statement), params)
                return result.scalar()
        finally:
            await engine.dispose()

    return asyncio.run(_query())


def _table_exists(url: str, table: str) -> bool:
    return (
        _scalar(
            url,
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = :schema AND table_name = :table",
            schema=SCHEMA,
            table=table,
        )
        is not None
    )


def _index_exists(url: str, index: str) -> bool:
    return (
        _scalar(
            url,
            "SELECT 1 FROM pg_indexes WHERE schemaname = :schema AND indexname = :index",
            schema=SCHEMA,
            index=index,
        )
        is not None
    )


def _index_definition(url: str, index: str) -> str:
    return str(
        _scalar(
            url,
            "SELECT indexdef FROM pg_indexes WHERE schemaname = :schema AND indexname = :index",
            schema=SCHEMA,
            index=index,
        )
    )


def _with_store[T](
    url: str, work: Callable[[PostgresHostedNativeAttemptStore], Awaitable[T]]
) -> T:
    """Run *work* against the production store."""

    async def _inner() -> T:
        database = Database(url)
        try:
            return await work(PostgresHostedNativeAttemptStore(database))
        finally:
            await database.dispose()

    return asyncio.run(_inner())


def _raw_insert_open_attempt(url: str, task_id: UUID) -> None:
    """Bypass the store: the plainest possible second open row for *task_id*."""

    async def _inner() -> None:
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        f"INSERT INTO {SCHEMA}.{ATTEMPTS_TABLE} ("
                        "id, task_id, worker_agent_id, leader_agent_id, team_name, room_id, "
                        "assignment_attempt_id, generation, execution_id, phase, package_dir, "
                        "base_sha, budget_until, notified_at, created_at, updated_at"
                        ") VALUES ("
                        ":id, :task_id, :worker, :leader, 'rm-raw', '!raw:matrix.local', "
                        ":assignment, 9, :execution, 'notified', 'teams/rm-raw/shared/tasks/x', "
                        ":sha, :now, :now, :now, :now)"
                    ),
                    {
                        "id": uuid4(),
                        "task_id": task_id,
                        "worker": uuid4(),
                        "leader": uuid4(),
                        "assignment": uuid4(),
                        "execution": uuid4(),
                        "sha": "b" * 40,
                        "now": NOW,
                    },
                )
        finally:
            await engine.dispose()

    asyncio.run(_inner())


def _attempt(
    *,
    task_id: UUID = TASK_ID,
    generation: int = 1,
    phase: AttemptPhase = AttemptPhase.NOTIFIED,
    **changes: object,
) -> HostedNativeAttempt:
    attempt_id = uuid4()
    fields: dict[str, object] = {
        "id": attempt_id,
        "task_id": task_id,
        "worker_agent_id": uuid4(),
        "leader_agent_id": uuid4(),
        "team_name": "rm-checkout",
        "room_id": "!team:matrix.local",
        "assignment_attempt_id": uuid4(),
        "generation": generation,
        "execution_id": uuid4(),
        "phase": phase,
        "package_dir": f"teams/rm-checkout/shared/tasks/{attempt_id}",
        "base_sha": "a" * 40,
        "budget_until": NOW + timedelta(seconds=2700),
        "notified_at": NOW,
        "created_at": NOW,
        "updated_at": NOW,
    }
    fields.update(changes)
    return HostedNativeAttempt(**fields)  # type: ignore[arg-type]


def _full_attempt() -> HostedNativeAttempt:
    return _attempt(
        phase=AttemptPhase.VERIFYING,
        review_dir="teams/rm-checkout/shared/tasks/review-1",
        review_budget_until=NOW + timedelta(seconds=900),
        acknowledged_at=NOW + timedelta(seconds=5),
        submitted_at=NOW + timedelta(seconds=600),
        submit_status=SubmitStatus.SUCCESS,
        review_verdict=ReviewVerdict.ACCEPT,
        verification_run_id=uuid4(),
        fenced_at=NOW + timedelta(seconds=700),
        fence_reason="generation advanced",
        updated_at=NOW + timedelta(seconds=700),
    )


def _event(attempt_id: UUID, *, marker: str, payload: dict[str, object]) -> HostedNativeEvent:
    return HostedNativeEvent(
        id=uuid4(),
        attempt_id=attempt_id,
        kind=EventKind.SUBMITTED,
        marker=marker,
        payload=payload,
        observed_at=NOW + timedelta(seconds=1),
    )


def test_attempts_are_undefined_at_0055_and_whole_at_head(scratch_database: str) -> None:
    """The failure the missing migration would cause, then its absence at head."""
    url = scratch_database
    _alembic(url, "upgrade", REVISION_BEFORE)

    # (a) Red: a database carrying every revision but the new one refuses the
    # very write ``HostedNativeRound.open()`` makes, because the table is not
    # there.
    with pytest.raises(ProgrammingError) as excinfo:
        _with_store(url, lambda store: store.add(_attempt()))
    assert getattr(excinfo.value.orig, "sqlstate", None) == UNDEFINED_TABLE

    # (b) Green: one revision later both tables and the partial index exist ...
    _alembic(url, "upgrade", "head")
    assert _table_exists(url, ATTEMPTS_TABLE) is True
    assert _table_exists(url, EVENTS_TABLE) is True
    assert _index_exists(url, OPEN_TASK_INDEX) is True
    definition = _index_definition(url, OPEN_TASK_INDEX)
    assert "UNIQUE" in definition
    assert "WHERE" in definition
    for phase in ("notified", "acknowledged", "review_pending", "verifying"):
        assert phase in definition

    # ... and an attempt with every field survives the production store.
    full = _full_attempt()
    _with_store(url, lambda store: store.add(full))
    stored = _with_store(url, lambda store: store.get(full.id))
    assert stored == full
    assert stored is not None and stored.submit_status is SubmitStatus.SUCCESS

    # (c) The index, not the store: a second open attempt for the same task
    # is refused at the database level -- through the store ...
    with pytest.raises(HostedNativeConflict):
        _with_store(url, lambda store: store.add(_attempt(generation=2)))
    # ... and through a raw INSERT that never saw the store's code.
    with pytest.raises(IntegrityError) as violation:
        _raw_insert_open_attempt(url, TASK_ID)
    assert getattr(violation.value.orig, "sqlstate", None) == UNIQUE_VIOLATION
    assert _with_store(url, lambda store: store.list_for_task(TASK_ID)) == (full,)

    # (d) Fencing the live attempt drops it out of the index and the next
    # generation opens.
    fenced = full.with_phase(
        AttemptPhase.FENCED,
        at=NOW + timedelta(seconds=800),
        fenced_at=NOW + timedelta(seconds=800),
        fence_reason="budget_expired",
    )
    _with_store(url, lambda store: store.save(fenced))
    assert _with_store(url, lambda store: store.get_open_for_task(TASK_ID)) is None
    successor = _attempt(generation=2)
    _with_store(url, lambda store: store.add(successor))
    assert _with_store(url, lambda store: store.get_open_for_task(TASK_ID)) == successor
    assert _with_store(url, lambda store: store.list_for_task(TASK_ID)) == (fenced, successor)

    # (e) The inbox deduplicates at the database level: same
    # ``(attempt_id, kind, marker)`` inserts nothing the second time.
    first = _event(successor.id, marker="etag-1", payload={"n": 1})
    replay = _event(successor.id, marker="etag-1", payload={"n": 2})
    assert _with_store(url, lambda store: store.record_event(first)) is True
    assert _with_store(url, lambda store: store.record_event(replay)) is False
    events = _with_store(url, lambda store: store.list_events(successor.id))
    assert events == (first,)
    assert events[0].payload == {"n": 1}


def test_head_downgrade_0055_upgrade_head_round_trips(scratch_database: str) -> None:
    """The new revision is reversible, re-appliable, and creates the tables itself.

    The attempt written before the downgrade is the interesting one: it does
    *not* live through the gap -- the downgrade drops the table under it,
    which is exactly what the migration's docstring warns about the audit
    trail -- and the store keeps working on the tables that come back.
    """
    url = scratch_database

    # Stopped *at* this revision, not at head: ``alembic current`` names the
    # chain's tip, and this revision stops being the tip as soon as 0057
    # lands. What must hold is that applying exactly this revision is what
    # creates the tables.
    _alembic(url, "upgrade", REVISION_UNDER_TEST)
    assert REVISION_UNDER_TEST in _alembic(url, "current")
    assert _table_exists(url, ATTEMPTS_TABLE) is True
    assert _table_exists(url, EVENTS_TABLE) is True
    assert _index_exists(url, OPEN_TASK_INDEX) is True

    _alembic(url, "upgrade", "head")
    before = _attempt()
    _with_store(url, lambda store: store.add(before))
    assert _with_store(url, lambda store: store.record_event(
        _event(before.id, marker="etag-0", payload={})
    )) is True

    # Destructive on purpose: the downgrade drops both tables and with them
    # every attempt and observation. Safe here only because the database is a
    # scratch one this test created.
    _alembic(url, "downgrade", REVISION_BEFORE)
    assert _table_exists(url, ATTEMPTS_TABLE) is False
    assert _table_exists(url, EVENTS_TABLE) is False
    assert _index_exists(url, OPEN_TASK_INDEX) is False

    _alembic(url, "upgrade", "head")
    assert _table_exists(url, ATTEMPTS_TABLE) is True
    assert _table_exists(url, EVENTS_TABLE) is True
    assert _index_exists(url, OPEN_TASK_INDEX) is True

    # The audit trail did not survive the gap ...
    assert _with_store(url, lambda store: store.get(before.id)) is None
    assert _with_store(url, lambda store: store.list_events(before.id)) == ()

    # ... and a second application is not just DDL-clean; the store works
    # through it, index included.
    fresh = _attempt()
    _with_store(url, lambda store: store.add(fresh))
    assert _with_store(url, lambda store: store.get_open_for_task(TASK_ID)) == fresh
    with pytest.raises(HostedNativeConflict):
        _with_store(url, lambda store: store.add(_attempt(generation=2)))
