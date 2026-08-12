"""``PlanSnapshotStore`` against a real database engine, not a stand-in.

The column under test is ``execution_plan_id``. Nothing else records that a
console round was started: the delivery read model keys a round's
``plan_version``/``created_at``/``updated_at`` off it, and contract v0.4 §8's
"this issue's plan has already been materialised" 409 is nothing more than
``current_draft`` running ``WHERE execution_plan_id IS NULL`` and finding
nothing. A snapshot that fails to take the value is a round the server has
lost, and a second materialize will start a second execution plan for it.

Every other test that touches this store reaches it through a fake — either the
in-memory ``RecordingSnapshotStore`` in ``tests/test_plan_execution_bridge.py``
or, one layer down, SQLite. Both are doubles of production, and the project has
already been bitten once by a suite that was green about an object nobody runs.
So this module runs the *production class* over whatever engine it is given,
and takes a real PostgreSQL when one is offered::

    REPOMESH_TEST_POSTGRES_URL=postgresql+asyncpg://user:pw@127.0.0.1:5544/db

Point it at a throwaway database with ``alembic upgrade head`` already applied;
it writes rows under freshly generated project ids and does not clean up. With
the variable unset the PostgreSQL parameter is skipped and only SQLite runs —
so a green suite with no PostgreSQL means less than a green suite with one, and
the skip line says so rather than hiding it.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
import pytest_asyncio

from repomesh.modules.repository_intelligence.infrastructure.plan_snapshot_store import (
    PlanSnapshotStore,
)
from repomesh.persistence import Database
from repomesh.persistence.base import ALL_SCHEMAS

_POSTGRES_URL = os.environ.get("REPOMESH_TEST_POSTGRES_URL")

_ENGINES = [
    pytest.param("sqlite", id="sqlite"),
    pytest.param(
        "postgres",
        id="postgres",
        marks=pytest.mark.skipif(
            not _POSTGRES_URL,
            reason=(
                "REPOMESH_TEST_POSTGRES_URL is unset; the dialect production "
                "actually runs on is NOT covered by this run"
            ),
        ),
    ),
]


class _Engine:
    """A database plus the means to open a *second*, independent handle on it.

    The reopen is what makes the persistence assertion mean anything — see
    ``test_link_execution_plan_survives_the_connection_that_wrote_it``.
    """

    def __init__(self, connect) -> None:
        self._connect = connect
        self._open: list[Database] = []

    def open(self) -> Database:
        instance = self._connect()
        self._open.append(instance)
        return instance

    async def close(self) -> None:
        for instance in self._open:
            await instance.dispose()


@pytest_asyncio.fixture(params=_ENGINES)
async def engine(request, tmp_path: object) -> _Engine:
    if request.param == "postgres":
        harness = _Engine(lambda: Database(_POSTGRES_URL))
        yield harness
        await harness.close()
        return
    url = f"sqlite+aiosqlite:///{tmp_path.joinpath('repomesh-plan-snapshots.db')}"
    harness = _Engine(
        lambda: Database(url, schema_translate_map={s: None for s in ALL_SCHEMAS})
    )
    await harness.open().create_all_for_tests()
    yield harness
    await harness.close()


async def _open_draft(store: PlanSnapshotStore, project_id):
    return await store.save(
        project_id=project_id,
        plan_version=1,
        engineering_spec="",
        contracts=[],
        task_dag=[],
        execution_batches=[],
        graph_edges=[],
        requirement_text="fix the billing rounding bug",
        discovery={"schema_version": 1, "analysis": {"sufficient": True}},
    )


@pytest.mark.asyncio
async def test_link_execution_plan_survives_the_connection_that_wrote_it(
    engine: _Engine,
) -> None:
    """The ``UPDATE`` commits, and a later reader sees it.

    Read back through a second ``Database`` — a new engine, a new pool, a new
    connection — because the failure this guards against is a write that is
    visible to the session that made it and to nobody else. Re-reading through
    the same store would agree with itself either way, which is exactly how a
    persistence bug stays green.
    """

    project_id, plan_id = uuid4(), uuid4()
    store = PlanSnapshotStore(engine.open())
    draft = await _open_draft(store, project_id)
    assert draft.execution_plan_id is None

    await store.link_execution_plan(draft.id, plan_id)

    reader = PlanSnapshotStore(engine.open())
    rows = await reader.list_all(project_id)
    assert [row.execution_plan_id for row in rows] == [plan_id]


@pytest.mark.asyncio
async def test_linking_consumes_the_draft_which_is_the_whole_409(
    engine: _Engine,
) -> None:
    """§8's guard, stated as the query it actually is.

    ``current_draft`` is the only thing standing between one issue and two
    execution plans, and it asks one question: is ``execution_plan_id`` still
    NULL? Before the link it must answer with the row; after it, with nothing.
    """

    project_id = uuid4()
    store = PlanSnapshotStore(engine.open())
    draft = await _open_draft(store, project_id)

    assert (await store.current_draft(project_id)).id == draft.id

    await store.link_execution_plan(draft.id, uuid4())

    assert await store.current_draft(project_id) is None


@pytest.mark.asyncio
async def test_the_round_keeps_its_integration_when_it_is_linked(
    engine: _Engine,
) -> None:
    """Both writes of the consuming pair land on the one row.

    ``materialize`` calls ``set_integration`` and then ``link_execution_plan``
    on the same draft, in two separate transactions. Checking them together is
    the point: the live snapshot that started this investigation had its
    integration columns filled and ``execution_plan_id`` NULL, and the shape of
    a half-written row is what makes "the plan is there, so the round must have
    started" a wrong inference.
    """

    project_id, plan_id = uuid4(), uuid4()
    store = PlanSnapshotStore(engine.open())
    draft = await _open_draft(store, project_id)

    await store.set_integration(
        draft.id,
        engineering_spec="raise the rounding to 4 decimal places",
        contracts=[{"producer": "billing", "consumer": "checkout"}],
        task_dag=[{"repository": "billing", "depends_on": []}],
        execution_batches=[["billing", "checkout"]],
        integration_method="llm_only",
    )
    await store.link_execution_plan(draft.id, plan_id)

    row = (await store.list_all(project_id))[0]
    assert row.execution_plan_id == plan_id
    assert row.execution_batches == [["billing", "checkout"]]
    assert row.contracts[0]["producer"] == "billing"
    assert row.integration_method == "llm_only"
    # The discovery archive rides on the same row and is not disturbed by
    # either write; the materialization receipt is appended to it afterwards.
    assert row.discovery["analysis"] == {"sufficient": True}
