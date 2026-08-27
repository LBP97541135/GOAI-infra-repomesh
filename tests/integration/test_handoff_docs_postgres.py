"""``repository_intelligence.handoff_docs`` against the real migration chain.

The table is mapped (``HandoffDocRecord``) and written to in production
(``PostgresHandoffDocStore``), but until revision ``20260827_0036`` no
migration ever created it. Every database built by ``alembic upgrade head``
therefore answered the handoff-document endpoints with ``UndefinedTable``,
and plan materialization degraded silently -- the generation failure is
swallowed into a "Failed to generate handoff documents" warning, so the
bug never surfaced as a 500 on the materialize path.

That is precisely the shape a SQLite suite cannot catch: the SQLite tests
build their schema with ``metadata.create_all``, which has the table
whether or not a migration exists. So this module refuses doubles on both
sides -- the production ``HandoffDocService`` over the production
``PostgresHandoffDocStore``, over a PostgreSQL database whose schema came
from the migration chain and nothing else -- and pins three facts:

1. at revision ``20260816_0035`` (everything but the new revision) the
   service and the ``/handoff-docs`` routes fail with ``UndefinedTable``;
2. at ``head`` the whole generate → read → decide → supersede chain works;
3. ``head → downgrade 0035 → head`` round-trips.

Point it at a throwaway server::

    REPOMESH_TEST_POSTGRES_URL=postgresql+asyncpg://user:pw@127.0.0.1:5544/postgres

Each test creates its *own* database on that server, migrates it, and drops
it afterwards, so the URL's database is never migrated in place: pointing
this at a shared instance costs a scratch database, not its schema. With
the variable unset the module skips.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from repomesh.modules.repository_intelligence.api.router import router as ri_router
from repomesh.modules.repository_intelligence.application.handoff_docs import (
    HandoffDoc,
    HandoffDocService,
    HandoffDocStatus,
)
from repomesh.modules.repository_intelligence.application.plan_integration import (
    ContractSpec,
    IntegratedPlan,
    TaskNode,
)
from repomesh.modules.repository_intelligence.infrastructure.handoff_doc_store import (
    PostgresHandoffDocStore,
)
from repomesh.persistence import Database

POSTGRES_URL = os.getenv("REPOMESH_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not POSTGRES_URL,
        reason=(
            "REPOMESH_TEST_POSTGRES_URL is not configured; the migration chain "
            "the handoff-doc table depends on is NOT covered by this run"
        ),
    ),
]

#: The revision that has every table *except* handoff_docs.
REVISION_BEFORE = "20260816_0035"
#: The revision under test.
REVISION_UNDER_TEST = "20260827_0036"
#: PostgreSQL's ``undefined_table`` SQLSTATE, surfaced by asyncpg as
#: ``UndefinedTableError`` and wrapped by SQLAlchemy in ``ProgrammingError``.
UNDEFINED_TABLE = "42P01"

_REPO_ROOT = Path(__file__).resolve().parents[2]

PROJECT_ID = UUID("31313131-3131-3131-3131-313131313131")
OWNER_ID = UUID("32323232-3232-3232-3232-323232323232")


# ---------------------------------------------------------------------------
# Throwaway database + migration driver
# ---------------------------------------------------------------------------


async def _admin_execute(admin_url: URL, statement: str) -> None:
    """Run one cluster-level statement outside a transaction block.

    ``CREATE``/``DROP DATABASE`` are refused inside a transaction, hence the
    AUTOCOMMIT isolation level; ``NullPool`` keeps no connection open, so the
    database being dropped is not held by this engine.
    """
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
    name = f"repomesh_handoff_{uuid4().hex[:12]}"
    asyncio.run(_admin_execute(admin_url, f'CREATE DATABASE "{name}"'))
    try:
        yield admin_url.set(database=name).render_as_string(hide_password=False)
    finally:
        # FORCE terminates whatever the FastAPI test client left connected;
        # without it a lingering pooled connection blocks the drop.
        asyncio.run(_admin_execute(admin_url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def _alembic(url: str, *args: str) -> str:
    """Run alembic against *url* in a subprocess, returning its output.

    A subprocess rather than ``alembic.command``: ``migrations/env.py`` ends
    in ``asyncio.run(...)``, which cannot be called from inside a running
    event loop, and it overrides ``sqlalchemy.url`` from ``get_settings()``,
    so ``REPOMESH_DATABASE_URL`` in a child process is the only way to aim
    the chain at the scratch database. Environment variables outrank the
    repository's ``.env``, so a developer's local URL cannot leak in.
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
                    text("SELECT to_regclass('repository_intelligence.handoff_docs')")
                )
                return result.scalar() is not None
        finally:
            await engine.dispose()

    return asyncio.run(_check())


# ---------------------------------------------------------------------------
# Production objects under test
# ---------------------------------------------------------------------------


def _with_service[T](url: str, work: Callable[[HandoffDocService], Awaitable[T]]) -> T:
    """Run *work* against the production service over the production store."""

    async def _inner() -> T:
        database = Database(url)
        try:
            return await work(HandoffDocService(PostgresHandoffDocStore(database)))
        finally:
            await database.dispose()

    return asyncio.run(_inner())


def _handoff_app(url: str) -> FastAPI:
    """The real ``/handoff-docs`` routes over the real store.

    Only ``handoff_doc_service`` is stubbed onto the container: the two read
    routes ask for nothing else, and building the whole container would drag
    in AgentTeams and object storage for a database question.
    """
    # One engine per app, created lazily enough that its connections are
    # opened inside the test client's own event loop.
    service = HandoffDocService(PostgresHandoffDocStore(Database(url)))
    app = FastAPI()
    app.include_router(ri_router)
    app.state.container = SimpleNamespace(handoff_doc_service=lambda: service)
    return app


def _make_plan() -> IntegratedPlan:
    return IntegratedPlan(
        engineering_spec="Unify the payment retry pipeline across services.",
        contracts=[
            ContractSpec(
                producer="ts-payment-service",
                consumer="ts-order-service",
                interface="POST /payments/retry",
                agreement="order calls payment with the same idempotency key",
            ),
        ],
        task_dag=[
            TaskNode(
                repository="ts-payment-service",
                instruction="add the retry endpoint",
                depends_on=(),
                parallelizable_with=(),
            ),
            TaskNode(
                repository="ts-order-service",
                instruction="call the new retry endpoint",
                depends_on=("ts-payment-service",),
                parallelizable_with=(),
            ),
        ],
        execution_batches=[["ts-payment-service"], ["ts-order-service"]],
    )


def _generate(service: HandoffDocService, version: int) -> Awaitable[list[HandoffDoc]]:
    return service.generate_for_plan(
        project_id=PROJECT_ID,
        plan_version=version,
        plan=_make_plan(),
        requirement="make payments retryable",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_handoff_doc_chain_is_undefined_table_at_0035_and_whole_at_head(
    scratch_database: str,
) -> None:
    """The failure the missing migration causes, then its absence at head."""
    url = scratch_database
    _alembic(url, "upgrade", REVISION_BEFORE)

    # (a) Red: a database carrying every revision but the new one.
    with pytest.raises(ProgrammingError) as excinfo:
        _with_service(url, lambda service: _generate(service, 1))
    assert getattr(excinfo.value.orig, "sqlstate", None) == UNDEFINED_TABLE

    # The same hole as the console sees it: the read routes 500 rather than
    # returning an empty list, because the relation is absent, not empty.
    with TestClient(_handoff_app(url), raise_server_exceptions=False) as client:
        listed = client.get("/handoff-docs", params={"project_id": str(PROJECT_ID)})
        fetched = client.get(f"/handoff-docs/{uuid4()}")
    assert listed.status_code == 500
    assert fetched.status_code == 500

    # (b) Green: the same objects, one revision later.
    _alembic(url, "upgrade", "head")

    docs = _with_service(url, lambda service: _generate(service, 1))
    assert [doc.repository for doc in docs] == ["ts-payment-service", "ts-order-service"]
    assert {doc.status for doc in docs} == {HandoffDocStatus.PENDING}
    # The JSONB payload survives the round trip, not just the row.
    assert docs[0].content["interfaces"]["produced"][0]["consumer"] == "ts-order-service"

    reread = _with_service(url, lambda service: service.get_doc(docs[0].id))
    assert reread is not None
    assert reread.content == docs[0].content

    decided = _with_service(
        url,
        lambda service: service.decide(
            doc_id=docs[0].id,
            approved=True,
            decided_by_agent_id=OWNER_ID,
            reason="interface change looks fine",
        ),
    )
    assert decided.status is HandoffDocStatus.APPROVED
    assert decided.decision == "approved"
    persisted = _with_service(url, lambda service: service.get_doc(docs[0].id))
    assert persisted is not None
    assert persisted.status is HandoffDocStatus.APPROVED
    assert persisted.decided_by_agent_id == OWNER_ID
    assert persisted.decision_reason == "interface change looks fine"

    # Replan: regenerating one repository supersedes its earlier document,
    # which is the bulk UPDATE path the store owns.
    regenerated = _with_service(
        url,
        lambda service: service.generate_for_plan(
            project_id=PROJECT_ID,
            plan_version=2,
            plan=_make_plan(),
            requirement="make payments retryable",
            repositories=["ts-payment-service"],
        ),
    )
    assert [doc.plan_version for doc in regenerated] == [2]

    superseded = _with_service(
        url,
        lambda service: service.list_docs(
            project_id=PROJECT_ID, status=HandoffDocStatus.SUPERSEDED
        ),
    )
    assert [(doc.id, doc.superseded_by_version) for doc in superseded] == [(docs[0].id, 2)]

    # Untouched repository keeps its own document; the filter is per-repo.
    still_pending = _with_service(
        url,
        lambda service: service.list_docs(
            project_id=PROJECT_ID,
            repository="ts-order-service",
            status=HandoffDocStatus.PENDING,
        ),
    )
    assert [doc.id for doc in still_pending] == [docs[1].id]

    with TestClient(_handoff_app(url), raise_server_exceptions=False) as client:
        listed = client.get("/handoff-docs", params={"project_id": str(PROJECT_ID)})
        fetched = client.get(f"/handoff-docs/{docs[0].id}")
    assert listed.status_code == 200
    assert len(listed.json()) == 3
    assert fetched.status_code == 200
    assert fetched.json()["decision"] == "approved"


def test_head_downgrade_0035_upgrade_head_round_trips(scratch_database: str) -> None:
    """The new revision is reversible and re-appliable, and it *is* the head."""
    url = scratch_database

    _alembic(url, "upgrade", "head")
    assert _table_exists(url) is True
    assert REVISION_UNDER_TEST in _alembic(url, "current")

    # Destructive on purpose: the downgrade drops the table and every
    # approval decision with it. Safe here only because the database is a
    # scratch one this test created.
    _alembic(url, "downgrade", REVISION_BEFORE)
    assert _table_exists(url) is False

    _alembic(url, "upgrade", "head")
    assert _table_exists(url) is True

    # A second application is not just DDL-clean; the chain works through it.
    docs = _with_service(url, lambda service: _generate(service, 1))
    assert len(docs) == 2
