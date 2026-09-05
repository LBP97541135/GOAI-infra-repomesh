"""``project.repository_agent_teams.construction_mode`` against the real migration chain.

The column is mapped (``ProjectRepositoryTeamRecord.construction_mode``) and
written by ``PostgresProjectTopologyStore``, and the SQLite suite builds its
schema with ``metadata.create_all`` -- so it has the column whether or not a
migration adds it. Revision ``20260904_0055`` is what adds it in production,
and this module pins that the way ``test_leader_assignments_postgres.py`` pins
0046 for its table: without the revision a production database would refuse
every topology write with ``UndefinedColumn`` while every unit test stayed
green.

Three facts are pinned, over the production store and a PostgreSQL database
whose schema came from the migration chain and nothing else:

1. at ``20260902_0054`` (everything but the new revision) writing a topology
   fails with ``UndefinedColumn``;
2. at ``head`` a ``local_cli`` team survives the round trip, and a team that
   says nothing reads back ``hosted_native``;
3. ``head -> downgrade 0054 -> head`` round-trips, and a row that lived through
   the gap comes back ``hosted_native`` -- the column's server default, which
   is what every pre-0055 row means (spec §5.3.1).

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
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from repomesh.modules.project.contracts import ConstructionMode, TeamDecompositionMode
from repomesh.modules.project.domain import ProjectAgentTopology, RepositoryTeam
from repomesh.modules.project.infrastructure import (
    PersistedTeamConstructionModeReader,
    PostgresProjectTopologyStore,
)
from repomesh.persistence import Database

POSTGRES_URL = os.getenv("REPOMESH_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not POSTGRES_URL,
        reason=(
            "REPOMESH_TEST_POSTGRES_URL is not configured; the migration chain "
            "the construction_mode column depends on is NOT covered by this run"
        ),
    ),
]

#: The revision that has every column *except* construction_mode.
REVISION_BEFORE = "20260902_0054"
#: The revision under test.
REVISION_UNDER_TEST = "20260904_0055"
#: PostgreSQL's ``undefined_column`` SQLSTATE.
UNDEFINED_COLUMN = "42703"

_REPO_ROOT = Path(__file__).resolve().parents[2]

PROJECT_ID = UUID("51515151-5151-5151-5151-515151515151")
REPOSITORY_ID = UUID("52525252-5252-5252-5252-525252525252")


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
    name = f"repomesh_hosted_native_{uuid4().hex[:12]}"
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


def _column_exists(url: str) -> bool:
    async def _check() -> bool:
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                result = await connection.execute(
                    text(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_schema = 'project' "
                        "AND table_name = 'repository_agent_teams' "
                        "AND column_name = 'construction_mode'"
                    )
                )
                return result.scalar() is not None
        finally:
            await engine.dispose()

    return asyncio.run(_check())


def _index_exists(url: str) -> bool:
    async def _check() -> bool:
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                result = await connection.execute(
                    text(
                        "SELECT 1 FROM pg_indexes WHERE schemaname = 'project' "
                        "AND indexname = 'ix_repository_agent_teams_construction_mode'"
                    )
                )
                return result.scalar() is not None
        finally:
            await engine.dispose()

    return asyncio.run(_check())


def _with_store[T](url: str, work: Callable[[PostgresProjectTopologyStore], Awaitable[T]]) -> T:
    """Run *work* against the production store."""

    async def _inner() -> T:
        database = Database(url)
        try:
            return await work(PostgresProjectTopologyStore(database))
        finally:
            await database.dispose()

    return asyncio.run(_inner())


def _topology(
    *,
    project_id: UUID = PROJECT_ID,
    construction_mode: ConstructionMode | None = ConstructionMode.LOCAL_CLI,
) -> ProjectAgentTopology:
    team_fields: dict[str, object] = {}
    if construction_mode is not None:
        team_fields["construction_mode"] = construction_mode
    return ProjectAgentTopology(
        organization_id=uuid4(),
        project_id=project_id,
        organization_leader_id=uuid4(),
        repository_teams=(
            RepositoryTeam(
                project_id=project_id,
                repository_id=REPOSITORY_ID,
                leader_agent_id=uuid4(),
                worker_agent_ids=(uuid4(),),
                **team_fields,  # type: ignore[arg-type]
            ),
        ),
    )


def _add(store: PostgresProjectTopologyStore, topology: ProjectAgentTopology) -> Awaitable[None]:
    return store.add(
        topology,
        idempotency_key=f"hosted-native-{topology.project_id}",
        request_fingerprint="sha256:" + "0" * 64,
    )


def test_construction_mode_is_undefined_at_0054_and_whole_at_head(
    scratch_database: str,
) -> None:
    """The failure the missing migration would cause, then its absence at head."""
    url = scratch_database
    _alembic(url, "upgrade", REVISION_BEFORE)

    # (a) Red: a database carrying every revision but the new one refuses the
    # very write materialize makes, because the mapped column is not there.
    with pytest.raises(ProgrammingError) as excinfo:
        _with_store(url, lambda store: _add(store, _topology()))
    assert getattr(excinfo.value.orig, "sqlstate", None) == UNDEFINED_COLUMN

    # (b) Green: the same topology, one revision later, and the mode survives.
    _alembic(url, "upgrade", "head")
    assert _column_exists(url) is True
    assert _index_exists(url) is True

    _with_store(url, lambda store: _add(store, _topology()))
    stored = _with_store(url, lambda store: store.get(PROJECT_ID))
    assert stored is not None
    team = stored.repository_teams[0]
    assert team.construction_mode is ConstructionMode.LOCAL_CLI
    # Untouched by the new column: adoption still starts server-side.
    assert team.decomposition_mode is TeamDecompositionMode.SERVER

    # The production reader answers from the same row.
    assert (
        _with_store(
            url,
            lambda store: PersistedTeamConstructionModeReader(store).construction_mode(
                PROJECT_ID, REPOSITORY_ID
            ),
        )
        is ConstructionMode.LOCAL_CLI
    )
    # ... and the product default for everything it cannot find.
    assert (
        _with_store(
            url,
            lambda store: PersistedTeamConstructionModeReader(store).construction_mode(
                uuid4(), REPOSITORY_ID
            ),
        )
        is ConstructionMode.HOSTED_NATIVE
    )

    # A team that says nothing is written, and read back, as hosted-native.
    silent_project = uuid4()
    _with_store(
        url,
        lambda store: _add(
            store, _topology(project_id=silent_project, construction_mode=None)
        ),
    )
    silent = _with_store(url, lambda store: store.get(silent_project))
    assert silent is not None
    assert silent.repository_teams[0].construction_mode is ConstructionMode.HOSTED_NATIVE


def test_head_downgrade_0054_upgrade_head_round_trips(scratch_database: str) -> None:
    """The new revision is reversible, re-appliable, and adds the column itself.

    The row written before the downgrade is the interesting one: it lives
    through the gap, and on the way back up the column arrives with its
    server default, so the team reads ``hosted_native`` -- which is exactly
    what the migration's docstring promises (and warns) about a Bridge-served
    team after a round trip.
    """
    url = scratch_database

    # Stopped *at* this revision, not at head: ``alembic current`` names the
    # chain's tip, and this revision stops being the tip as soon as 0056
    # lands. What must hold is that applying exactly this revision is what
    # adds the column.
    _alembic(url, "upgrade", REVISION_UNDER_TEST)
    assert REVISION_UNDER_TEST in _alembic(url, "current")
    assert _column_exists(url) is True

    _alembic(url, "upgrade", "head")
    _with_store(url, lambda store: _add(store, _topology()))
    before = _with_store(url, lambda store: store.get(PROJECT_ID))
    assert before is not None
    assert before.repository_teams[0].construction_mode is ConstructionMode.LOCAL_CLI

    # Destructive on purpose: the downgrade drops the column and with it
    # which teams were staffed for Bridges. Safe here only because the
    # database is a scratch one this test created.
    _alembic(url, "downgrade", REVISION_BEFORE)
    assert _column_exists(url) is False
    assert _index_exists(url) is False

    _alembic(url, "upgrade", "head")
    assert _column_exists(url) is True
    assert _index_exists(url) is True

    after = _with_store(url, lambda store: store.get(PROJECT_ID))
    assert after is not None
    assert after.repository_teams[0].id == before.repository_teams[0].id
    assert after.repository_teams[0].construction_mode is ConstructionMode.HOSTED_NATIVE

    # A second application is not just DDL-clean; the store works through it.
    fresh_project = uuid4()
    _with_store(url, lambda store: _add(store, _topology(project_id=fresh_project)))
    fresh = _with_store(url, lambda store: store.get(fresh_project))
    assert fresh is not None
    assert fresh.repository_teams[0].construction_mode is ConstructionMode.LOCAL_CLI
