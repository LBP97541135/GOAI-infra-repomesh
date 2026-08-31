"""``task_orchestration.leader_assignments`` against the real migration chain.

The store is mapped (``LeaderAssignmentRecord``) and written to in production
(``PostgresLeaderAssignmentStore``), and the SQLite suite next door builds its
schema with ``metadata.create_all`` -- so it has the table whether or not a
migration creates it. That is precisely the gap revision ``20260827_0036`` was
written to close for ``handoff_docs``, and this module refuses to let the same
hole open under the leader-actions surface: without revision
``20260828_0038``, a production database would answer every
``/agent-actions/leader/assignments/{taskId}`` with ``UndefinedTable`` while
every unit test stayed green.

Doubles are refused on both sides: the production ``PostgresLeaderAssignment
Store`` over a PostgreSQL database whose schema came from the migration chain
and nothing else. Three facts are pinned:

1. at ``20260827_0036`` (everything but the new revision) the store fails with
   ``UndefinedTable``;
2. at ``head`` the ensure/read chain works, JSONB documents included, and
   ``ensure`` keeps the first envelope through a real primary key;
3. ``head -> downgrade 0036 -> head`` round-trips.

Point it at a throwaway server::

    REPOMESH_TEST_POSTGRES_URL=postgresql+asyncpg://user:pw@127.0.0.1:15548/postgres

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
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from repomesh.modules.task_orchestration.contracts import (
    LeaderAssignmentPhase,
    LeaderAssignmentView,
    LeaderSafetyEnvelopeView,
    WorkerRosterEntryView,
)
from repomesh.modules.task_orchestration.infrastructure import PostgresLeaderAssignmentStore
from repomesh.persistence import Database

POSTGRES_URL = os.getenv("REPOMESH_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not POSTGRES_URL,
        reason=(
            "REPOMESH_TEST_POSTGRES_URL is not configured; the migration chain "
            "the leader-assignment table depends on is NOT covered by this run"
        ),
    ),
]

#: The revision that has every table *except* leader_assignments.
REVISION_BEFORE = "20260830_0045"
#: The revision under test.
REVISION_UNDER_TEST = "20260830_0046"
#: PostgreSQL's ``undefined_table`` SQLSTATE.
UNDEFINED_TABLE = "42P01"

_REPO_ROOT = Path(__file__).resolve().parents[2]

LEADER_TASK_ID = UUID("41414141-4141-4141-4141-414141414141")


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
    name = f"repomesh_leader_{uuid4().hex[:12]}"
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
                    text("SELECT to_regclass('task_orchestration.leader_assignments')")
                )
                return result.scalar() is not None
        finally:
            await engine.dispose()

    return asyncio.run(_check())


def _with_store[T](url: str, work: Callable[[PostgresLeaderAssignmentStore], Awaitable[T]]) -> T:
    """Run *work* against the production store."""

    async def _inner() -> T:
        database = Database(url)
        try:
            return await work(PostgresLeaderAssignmentStore(database))
        finally:
            await database.dispose()

    return asyncio.run(_inner())


def _assignment(*, allowed_path_roots: tuple[str, ...]) -> LeaderAssignmentView:
    return LeaderAssignmentView(
        leader_task_id=LEADER_TASK_ID,
        organization_id=uuid4(),
        project_id=uuid4(),
        repository_id=uuid4(),
        leader_agent_id=uuid4(),
        phase=LeaderAssignmentPhase.PLANNING,
        safety_envelope=LeaderSafetyEnvelopeView(
            allowed_path_roots=allowed_path_roots,
            test_paths=("tests/",),
            test_commands=("python scripts/run_tests.py",),
        ),
        worker_roster=(
            WorkerRosterEntryView(
                worker_agent_id=uuid4(),
                worker_name="pricing-codex-worker",
                responsibility_paths=("src/pricing_core/",),
            ),
        ),
    )


def test_leader_assignments_are_undefined_at_0036_and_whole_at_head(
    scratch_database: str,
) -> None:
    """The failure the missing migration would cause, then its absence at head."""
    url = scratch_database
    _alembic(url, "upgrade", REVISION_BEFORE)

    parked = _assignment(allowed_path_roots=("src/pricing_core/", "tests/"))

    # (a) Red: a database carrying every revision but the new one.
    with pytest.raises(ProgrammingError) as excinfo:
        _with_store(url, lambda store: store.ensure(parked))
    assert getattr(excinfo.value.orig, "sqlstate", None) == UNDEFINED_TABLE

    # (b) Green: the same object, one revision later.
    _alembic(url, "upgrade", "head")

    written = _with_store(url, lambda store: store.ensure(parked))
    assert written == parked

    # The JSON documents survive the round trip, not just the row.
    read_back = _with_store(url, lambda store: store.get(LEADER_TASK_ID))
    assert read_back == parked

    # ``ensure`` keeps the first envelope, over a real primary key rather than
    # a dictionary: this is what stops a batch replay moving the bounds under a
    # plan already being written.
    widened = _assignment(allowed_path_roots=("**",))
    replayed = _with_store(url, lambda store: store.ensure(widened))
    assert replayed == parked
    assert _with_store(url, lambda store: store.get(LEADER_TASK_ID)) == parked

    assert _with_store(url, lambda store: store.get(uuid4())) is None


def test_head_downgrade_0036_upgrade_head_round_trips(scratch_database: str) -> None:
    """The new revision is reversible, re-appliable, and creates the table itself."""
    url = scratch_database

    # Stopped *at* this revision, not at head: ``alembic current`` names the
    # chain's tip, and this revision will stop being the tip as soon as the
    # next one lands (20260828_0039 is already reserved). What must hold is
    # that applying exactly this revision is what creates the table.
    _alembic(url, "upgrade", REVISION_UNDER_TEST)
    assert REVISION_UNDER_TEST in _alembic(url, "current")
    assert _table_exists(url) is True

    _alembic(url, "upgrade", "head")
    assert _table_exists(url) is True

    # Destructive on purpose: the downgrade drops the table and every parked
    # assignment with it. Safe here only because the database is a scratch one
    # this test created.
    _alembic(url, "downgrade", REVISION_BEFORE)
    assert _table_exists(url) is False

    _alembic(url, "upgrade", "head")
    assert _table_exists(url) is True

    # A second application is not just DDL-clean; the store works through it.
    parked = _assignment(allowed_path_roots=("src/pricing_core/",))
    assert _with_store(url, lambda store: store.ensure(parked)) == parked
