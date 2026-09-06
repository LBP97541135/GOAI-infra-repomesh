"""Both ``HostedNativeAttemptStore`` implementations, side by side.

Every test runs once against the in-memory store and once against
``PostgresHostedNativeAttemptStore`` over SQLite (``create_all`` builds the
partial unique index and the events constraint there too), so the two cannot
drift in what they refuse. The real partial index on PostgreSQL is pinned by
``tests/integration/test_hosted_native_attempts_postgres.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from repomesh.integrations.hosted_native.contracts import (
    OPEN_PHASES_SQL,
    TERMINAL_PHASES,
    AttemptPhase,
    EventKind,
    HostedNativeAttempt,
    HostedNativeAttemptStore,
    HostedNativeConflict,
    HostedNativeEvent,
    ReviewVerdict,
    SubmitStatus,
)
from repomesh.integrations.hosted_native.store import (
    OPEN_PHASE_VALUES,
    InMemoryHostedNativeAttemptStore,
    PostgresHostedNativeAttemptStore,
)
from repomesh.persistence import Database
from repomesh.persistence.base import ALL_SCHEMAS

NOW = datetime.now(UTC)


def _at(seconds: int) -> datetime:
    return NOW + timedelta(seconds=seconds)


@pytest.fixture(params=["memory", "sqlite"])
async def store(request, tmp_path):
    if request.param == "memory":
        yield InMemoryHostedNativeAttemptStore()
        return
    database = Database(
        f"sqlite+aiosqlite:///{tmp_path / 'hosted_native.db'}",
        schema_translate_map={schema: None for schema in ALL_SCHEMAS},
    )
    await database.create_all_for_tests()
    try:
        yield PostgresHostedNativeAttemptStore(database)
    finally:
        await database.dispose()


def _attempt(
    *,
    task_id: UUID | None = None,
    phase: AttemptPhase = AttemptPhase.NOTIFIED,
    generation: int = 1,
    notified_at: datetime | None = None,
    created_at: datetime | None = None,
    **changes: object,
) -> HostedNativeAttempt:
    attempt_id = uuid4()
    notified = notified_at or _at(0)
    fields: dict[str, object] = {
        "id": attempt_id,
        "task_id": task_id or uuid4(),
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
        "budget_until": notified + timedelta(seconds=2700),
        "notified_at": notified,
        "created_at": created_at or notified,
        "updated_at": created_at or notified,
    }
    fields.update(changes)
    return HostedNativeAttempt(**fields)  # type: ignore[arg-type]


def _full_attempt() -> HostedNativeAttempt:
    """Every optional field set, so a round trip exercises each column."""
    return _attempt(
        phase=AttemptPhase.VERIFYING,
        review_dir="teams/rm-checkout/shared/tasks/review-1",
        review_budget_until=_at(3600),
        acknowledged_at=_at(5),
        submitted_at=_at(600),
        submit_status=SubmitStatus.SUCCESS_WITH_NOTES,
        review_verdict=ReviewVerdict.ACCEPT,
        verification_run_id=uuid4(),
        fenced_at=_at(700),
        fence_reason="generation advanced",
        updated_at=_at(700),
    )


def _event(
    attempt_id: UUID,
    *,
    kind: EventKind = EventKind.ACKNOWLEDGED,
    marker: str = "2026-09-05T12:00:00Z",
    observed_at: datetime | None = None,
    payload: dict[str, object] | None = None,
) -> HostedNativeEvent:
    return HostedNativeEvent(
        id=uuid4(),
        attempt_id=attempt_id,
        kind=kind,
        marker=marker,
        payload=payload if payload is not None else {"source": "meta.json"},
        observed_at=observed_at or _at(0),
    )


def test_open_phases_sql_names_exactly_the_non_terminal_phases() -> None:
    """The index predicate, the migration's copy and the store's queries all
    say the same thing about what "open" means."""
    for value in OPEN_PHASE_VALUES:
        assert f"'{value}'" in OPEN_PHASES_SQL
    for phase in TERMINAL_PHASES:
        assert f"'{phase.value}'" not in OPEN_PHASES_SQL
    assert OPEN_PHASES_SQL.count("'") == 2 * len(OPEN_PHASE_VALUES)


async def test_add_and_get_round_trip_every_field(store: HostedNativeAttemptStore) -> None:
    attempt = _full_attempt()
    await store.add(attempt)

    stored = await store.get(attempt.id)
    assert stored == attempt
    assert stored is not None
    assert stored.phase is AttemptPhase.VERIFYING
    assert stored.submit_status is SubmitStatus.SUCCESS_WITH_NOTES
    assert stored.review_verdict is ReviewVerdict.ACCEPT
    for value in (
        stored.budget_until,
        stored.review_budget_until,
        stored.notified_at,
        stored.acknowledged_at,
        stored.submitted_at,
        stored.fenced_at,
        stored.created_at,
        stored.updated_at,
    ):
        assert value is not None and value.tzinfo is not None
    assert await store.get(uuid4()) is None


async def test_get_open_for_task(store: HostedNativeAttemptStore) -> None:
    task_id = uuid4()
    terminal = _attempt(task_id=task_id, phase=AttemptPhase.FAILED)
    open_attempt = _attempt(task_id=task_id, generation=2, phase=AttemptPhase.ACKNOWLEDGED)
    await store.add(terminal)
    await store.add(open_attempt)

    assert await store.get_open_for_task(task_id) == open_attempt
    assert await store.get_open_for_task(uuid4()) is None


async def test_second_open_attempt_for_same_task_conflicts(
    store: HostedNativeAttemptStore,
) -> None:
    first = _attempt()
    await store.add(first)
    second = _attempt(task_id=first.task_id, generation=2, phase=AttemptPhase.REVIEW_PENDING)

    with pytest.raises(HostedNativeConflict):
        await store.add(second)

    assert await store.get(first.id) == first
    assert await store.get(second.id) is None
    assert await store.list_for_task(first.task_id) == (first,)


async def test_adding_the_same_attempt_id_twice_conflicts(
    store: HostedNativeAttemptStore,
) -> None:
    attempt = _attempt()
    await store.add(attempt)
    with pytest.raises(HostedNativeConflict):
        await store.add(attempt)


async def test_terminal_attempt_frees_the_task_for_a_new_one(
    store: HostedNativeAttemptStore,
) -> None:
    task_id = uuid4()
    for phase in TERMINAL_PHASES:
        await store.add(_attempt(task_id=task_id, phase=phase))

    fresh = _attempt(task_id=task_id, generation=2)
    await store.add(fresh)

    assert await store.get_open_for_task(task_id) == fresh
    assert len(await store.list_for_task(task_id)) == len(TERMINAL_PHASES) + 1


async def test_save_moves_phase_and_fields(store: HostedNativeAttemptStore) -> None:
    attempt = _attempt()
    await store.add(attempt)

    acknowledged = attempt.with_phase(
        AttemptPhase.ACKNOWLEDGED, at=_at(5), acknowledged_at=_at(4)
    )
    await store.save(acknowledged)
    assert await store.get(attempt.id) == acknowledged

    fenced = acknowledged.with_phase(
        AttemptPhase.FENCED, at=_at(9), fenced_at=_at(9), fence_reason="budget_expired"
    )
    await store.save(fenced)
    stored = await store.get(attempt.id)
    assert stored == fenced
    assert stored is not None and stored.acknowledged_at == _at(4)
    assert await store.get_open_for_task(attempt.task_id) is None


async def test_save_of_unknown_attempt_raises(store: HostedNativeAttemptStore) -> None:
    with pytest.raises(HostedNativeConflict):
        await store.save(_attempt())


async def test_save_that_would_reopen_a_second_attempt_raises(
    store: HostedNativeAttemptStore,
) -> None:
    task_id = uuid4()
    closed = _attempt(task_id=task_id, phase=AttemptPhase.FENCED)
    live = _attempt(task_id=task_id, generation=2)
    await store.add(closed)
    await store.add(live)

    with pytest.raises(HostedNativeConflict):
        await store.save(closed.with_phase(AttemptPhase.NOTIFIED, at=_at(1)))

    assert await store.get(closed.id) == closed
    assert await store.get_open_for_task(task_id) == live


async def test_list_open_orders_by_notified_at_and_excludes_terminal(
    store: HostedNativeAttemptStore,
) -> None:
    late = _attempt(notified_at=_at(30), phase=AttemptPhase.VERIFYING)
    early = _attempt(notified_at=_at(10))
    middle = _attempt(notified_at=_at(20), phase=AttemptPhase.REVIEW_PENDING)
    done = _attempt(notified_at=_at(0), phase=AttemptPhase.VERIFIED)
    fenced = _attempt(notified_at=_at(1), phase=AttemptPhase.FENCED)
    for attempt in (late, early, middle, done, fenced):
        await store.add(attempt)

    assert await store.list_open() == (early, middle, late)


async def test_list_for_task_orders_by_generation_then_created_at(
    store: HostedNativeAttemptStore,
) -> None:
    task_id = uuid4()
    gen2 = _attempt(task_id=task_id, generation=2, phase=AttemptPhase.FAILED, created_at=_at(1))
    gen1_late = _attempt(
        task_id=task_id, generation=1, phase=AttemptPhase.BLOCKED, created_at=_at(50)
    )
    gen1_early = _attempt(
        task_id=task_id, generation=1, phase=AttemptPhase.FENCED, created_at=_at(2)
    )
    gen3 = _attempt(task_id=task_id, generation=3, created_at=_at(0))
    other = _attempt()
    for attempt in (gen2, gen1_late, gen1_early, gen3, other):
        await store.add(attempt)

    assert await store.list_for_task(task_id) == (gen1_early, gen1_late, gen2, gen3)


async def test_record_event_dedupes_on_attempt_kind_marker(
    store: HostedNativeAttemptStore,
) -> None:
    attempt = _attempt()
    await store.add(attempt)
    first = _event(attempt.id, payload={"n": 1})
    replay = _event(attempt.id, payload={"n": 2})

    assert await store.record_event(first) is True
    assert await store.record_event(replay) is False

    events = await store.list_events(attempt.id)
    assert events == (first,)
    assert events[0].payload == {"n": 1}
    assert await store.find_event(attempt.id, EventKind.ACKNOWLEDGED, first.marker) == first


async def test_same_marker_under_a_different_kind_is_a_new_row(
    store: HostedNativeAttemptStore,
) -> None:
    attempt = _attempt()
    await store.add(attempt)
    acknowledged = _event(attempt.id, kind=EventKind.ACKNOWLEDGED, marker="m")
    submitted = _event(attempt.id, kind=EventKind.SUBMITTED, marker="m", observed_at=_at(1))

    assert await store.record_event(acknowledged) is True
    assert await store.record_event(submitted) is True
    assert await store.list_events(attempt.id) == (acknowledged, submitted)


async def test_find_event(store: HostedNativeAttemptStore) -> None:
    attempt = _attempt()
    await store.add(attempt)
    event = _event(attempt.id, kind=EventKind.AUTO_APPROVED, marker="$event:matrix.local")
    await store.record_event(event)

    found = await store.find_event(attempt.id, EventKind.AUTO_APPROVED, "$event:matrix.local")
    assert found == event
    assert found is not None and found.observed_at.tzinfo is not None
    assert await store.find_event(attempt.id, EventKind.FENCED, "$event:matrix.local") is None
    assert await store.find_event(uuid4(), EventKind.AUTO_APPROVED, "$event:matrix.local") is None


async def test_mark_applied(store: HostedNativeAttemptStore) -> None:
    attempt = _attempt()
    await store.add(attempt)
    event = _event(attempt.id)
    await store.record_event(event)
    assert (await store.list_events(attempt.id))[0].applied_at is None

    await store.mark_applied(event.id, applied_at=_at(3))
    applied = await store.find_event(attempt.id, event.kind, event.marker)
    assert applied is not None
    assert applied.applied_at == _at(3)
    assert applied.payload == event.payload

    # Unknown ids are ignored, not an error.
    await store.mark_applied(uuid4(), applied_at=_at(4))


async def test_list_events_orders_by_observed_at(store: HostedNativeAttemptStore) -> None:
    attempt = _attempt()
    other = _attempt()
    await store.add(attempt)
    await store.add(other)
    late = _event(attempt.id, kind=EventKind.SUBMITTED, marker="late", observed_at=_at(30))
    early = _event(attempt.id, kind=EventKind.ACKNOWLEDGED, marker="early", observed_at=_at(10))
    middle = _event(attempt.id, kind=EventKind.EXPIRED, marker="middle", observed_at=_at(20))
    elsewhere = _event(other.id, marker="elsewhere", observed_at=_at(0))
    for event in (late, early, middle, elsewhere):
        assert await store.record_event(event) is True

    assert await store.list_events(attempt.id) == (early, middle, late)
    assert await store.list_events(other.id) == (elsewhere,)
    assert await store.list_events(uuid4()) == ()
