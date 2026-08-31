"""One suite over both ``LeaderAssignmentStore`` adapters.

The port has a production adapter and a memory one, and the whole point of the
pair is that a test running against the fast one is evidence about the slow
one. So every test here is parametrised over both, and neither store gets a
test of its own: a behaviour only one of them has is a behaviour the port does
not promise.

The property that carries the most weight is ``ensure`` keeping the *first*
record. A leader plans against the safety envelope it was handed, and batch
assignment re-runs whole on every resume (``AdvanceExecutionPlan._resume``), so
a store that took the later write would move the bounds underneath a plan
already being written and then reject it for being outside them.
"""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio

from repomesh.modules.task_orchestration.contracts import (
    LeaderAssignmentPhase,
    LeaderAssignmentView,
    LeaderSafetyEnvelopeView,
    WorkerRosterEntryView,
)
from repomesh.modules.task_orchestration.infrastructure import (
    InMemoryLeaderAssignmentStore,
    PostgresLeaderAssignmentStore,
)
from repomesh.modules.task_orchestration.ports import LeaderAssignmentStore
from repomesh.persistence import Database
from repomesh.persistence.base import ALL_SCHEMAS


@pytest_asyncio.fixture(params=["memory", "sql"])
async def store(request: pytest.FixtureRequest, tmp_path: object) -> AsyncIterator[
    LeaderAssignmentStore
]:
    if request.param == "memory":
        yield InMemoryLeaderAssignmentStore()
        return
    database_path = tmp_path.joinpath("repomesh-leader-assignments.db")
    database = Database(
        f"sqlite+aiosqlite:///{database_path}",
        schema_translate_map={schema: None for schema in ALL_SCHEMAS},
    )
    await database.create_all_for_tests()
    try:
        yield PostgresLeaderAssignmentStore(database)
    finally:
        await database.dispose()


def build_assignment(**overrides: object) -> LeaderAssignmentView:
    defaults: dict[str, object] = {
        "leader_task_id": uuid4(),
        "organization_id": uuid4(),
        "project_id": uuid4(),
        "repository_id": uuid4(),
        "leader_agent_id": uuid4(),
        "phase": LeaderAssignmentPhase.PLANNING,
        "safety_envelope": LeaderSafetyEnvelopeView(
            allowed_path_roots=("src/pricing_core/", "tests/"),
            test_paths=("tests/",),
            test_commands=("python scripts/run_tests.py",),
        ),
        "worker_roster": (
            WorkerRosterEntryView(
                worker_agent_id=uuid4(),
                worker_name="pricing-codex-worker",
                responsibility_paths=("src/pricing_core/",),
            ),
        ),
    }
    return LeaderAssignmentView(**(defaults | overrides))  # type: ignore[arg-type]


async def test_an_unknown_leader_task_has_no_assignment(store: LeaderAssignmentStore) -> None:
    assert await store.get(uuid4()) is None


async def test_ensure_round_trips_the_envelope_and_the_roster(
    store: LeaderAssignmentStore,
) -> None:
    assignment = build_assignment()

    written = await store.ensure(assignment)
    read_back = await store.get(assignment.leader_task_id)

    assert written == assignment
    assert read_back == assignment


async def test_ensure_keeps_the_first_envelope_a_leader_was_handed(
    store: LeaderAssignmentStore,
) -> None:
    """A replay of the batch must not move the bounds under a leader's plan."""

    original = build_assignment()
    await store.ensure(original)

    widened = LeaderAssignmentView(
        leader_task_id=original.leader_task_id,
        organization_id=original.organization_id,
        project_id=original.project_id,
        repository_id=original.repository_id,
        leader_agent_id=original.leader_agent_id,
        phase=original.phase,
        safety_envelope=LeaderSafetyEnvelopeView(
            allowed_path_roots=("**",), test_paths=(), test_commands=()
        ),
        worker_roster=original.worker_roster,
    )
    replayed = await store.ensure(widened)

    assert replayed == original
    assert await store.get(original.leader_task_id) == original


async def test_assignments_of_different_leader_tasks_do_not_collide(
    store: LeaderAssignmentStore,
) -> None:
    first = build_assignment()
    second = build_assignment()

    await store.ensure(first)
    await store.ensure(second)

    assert await store.get(first.leader_task_id) == first
    assert await store.get(second.leader_task_id) == second
