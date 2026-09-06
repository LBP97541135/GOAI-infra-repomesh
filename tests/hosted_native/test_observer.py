"""``SharedTaskDirectoryObserver`` over the in-memory store and the readers (spec §4.2 M2, D-6).

What is pinned here: the observer claims a directory by its name alone (the
attempt id from its own row), reads only ``meta.json`` and ``result.md`` of
that directory, turns copaw's timestamps into ``(attempt_id, kind, marker)``
events that the store de-duplicates, and hands each freshly recorded (or
recorded-but-unapplied) event to the round exactly once. The copaw fixtures
are the wave-0 spike's own ``meta.json`` / ``result.md``
(``docs/startup-records/2026-09-03-hosted-native-spike/results/...``).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from repomesh.integrations.hosted_native.contracts import (
    AttemptPhase,
    EventKind,
    HostedNativeAttempt,
    HostedNativeEvent,
    RoundOutcome,
    RoundTransition,
    SharedTaskEvent,
    SubmitStatus,
)
from repomesh.integrations.hosted_native.observer import (
    SharedTaskDirectoryObserver,
    parse_copaw_timestamp,
    parse_result_markdown,
)
from repomesh.integrations.hosted_native.storage import (
    DiskSharedTaskDirectoryReader,
    InMemorySharedTaskDirectoryReader,
    shared_task_key,
)
from repomesh.integrations.hosted_native.store import InMemoryHostedNativeAttemptStore

T0 = datetime(2026, 9, 2, 19, 59, 0, tzinfo=UTC)
CLOCK_NOW = T0 + timedelta(minutes=30)
TEAM = "repomesh-team-x"
ROOM = "!team:hs"
WORKER_AGENT_ID = UUID("00000000-0000-0000-0000-0000000000a1")
LEADER_AGENT_ID = UUID("00000000-0000-0000-0000-0000000000b1")
ASSIGNMENT_ATTEMPT_ID = UUID("00000000-0000-0000-0000-0000000000c1")
EXECUTION_ID = UUID("00000000-0000-0000-0000-0000000000d1")

#: The stamps copaw wrote into the spike's ``meta.json`` on ``ack_task`` / ``submit_task``.
ACKNOWLEDGED_AT = "2026-09-02T20:00:51Z"
SUBMITTED_AT = "2026-09-02T20:27:04Z"

SPIKE_SUMMARY = (
    "Implemented multi-currency support in quote(): added a currency parameter (ISO 4217 "
    "code, default USD) carried on Quote, with currency-specific rounding (zero-decimal "
    "currencies like JPY round the payable amount to the nearest whole major unit; standard "
    "currencies keep two decimal places). Extended tests/test_quote.py with a "
    "MultiCurrencyRoundingTests class covering JPY integer rounding (including "
    "discount/shipping/tax scenarios) and a standard-decimal (EUR) currency. All 9 unit "
    "tests pass unchanged via the frozen command python scripts/run_tests.py."
)


def spike_result(attempt_id: UUID | str) -> str:
    """The worker's ``result.md`` from the spike, with the directory renamed to *attempt_id*."""

    prefix = f"shared/tasks/{attempt_id}/candidate"
    return (
        "STATUS: SUCCESS\n"
        f"SUMMARY: {SPIKE_SUMMARY}\n"
        "\n"
        "DELIVERABLES:\n"
        f"- {prefix}/candidate.bundle\n"
        f"- {prefix}/candidate.diff\n"
        f"- {prefix}/changes.json\n"
        f"- {prefix}/evidence.json\n"
    )


def spike_meta(
    task_dir: UUID | str,
    *,
    status: str,
    acknowledged_at: str | None = None,
    submitted_at: str | None = None,
    repomesh: dict[str, object] | None = None,
) -> str:
    """``meta.json`` in the shape copaw rewrites it (the spike's, field for field)."""

    meta: dict[str, object] = {
        "task_id": str(task_dir),
        "project_id": "ff6a9f90-1e0c-5fe7-8a9f-9b354c1aa754",
        "task_title": "Implement multi-currency quote() for repomesh-e2e-pricing-core (attempt 1)",
        "assigned_to": "agt-worker-x",
        "room_id": ROOM,
        "status": status,
        "depends_on": [],
    }
    if acknowledged_at is not None:
        meta["acknowledged_at"] = acknowledged_at
    if submitted_at is not None:
        meta["submitted_at"] = submitted_at
    if repomesh is not None:
        meta["repomesh"] = repomesh
    return json.dumps(meta, indent=2) + "\n"


def make_attempt(**overrides: object) -> HostedNativeAttempt:
    values: dict[str, object] = {
        "id": uuid4(),
        "task_id": uuid4(),
        "worker_agent_id": WORKER_AGENT_ID,
        "leader_agent_id": LEADER_AGENT_ID,
        "team_name": TEAM,
        "room_id": ROOM,
        "assignment_attempt_id": ASSIGNMENT_ATTEMPT_ID,
        "generation": 1,
        "execution_id": EXECUTION_ID,
        "phase": AttemptPhase.NOTIFIED,
        "base_sha": "a" * 40,
        "budget_until": T0 + timedelta(minutes=45),
        "notified_at": T0,
        "created_at": T0,
        "updated_at": T0,
    }
    values.update(overrides)
    values.setdefault("package_dir", f"teams/{TEAM}/shared/tasks/{values['id']}")
    return HostedNativeAttempt(**values)  # type: ignore[arg-type]


class FakeRound:
    """Records every event it is handed; answers ``APPLIED`` unless told otherwise."""

    def __init__(
        self, outcome: RoundOutcome = RoundOutcome.APPLIED, *, error: Exception | None = None
    ) -> None:
        self.events: list[SharedTaskEvent] = []
        self.outcome = outcome
        self.error = error

    async def observe(self, event: SharedTaskEvent) -> RoundTransition:
        self.events.append(event)
        if self.error is not None:
            raise self.error
        return RoundTransition(
            attempt_id=event.attempt_id,
            outcome=self.outcome,
            phase=None,
            reason=None if self.outcome is RoundOutcome.APPLIED else "phase_mismatch",
        )


def observer(
    attempts: InMemoryHostedNativeAttemptStore,
    reader: InMemorySharedTaskDirectoryReader | DiskSharedTaskDirectoryReader,
    round_: FakeRound,
    *,
    interval_seconds: float = 0.01,
) -> SharedTaskDirectoryObserver:
    return SharedTaskDirectoryObserver(
        attempts, reader, round_, interval_seconds=interval_seconds, clock=lambda: CLOCK_NOW
    )


def key(task_dir: UUID | str, name: str) -> str:
    return shared_task_key(TEAM, str(task_dir), name)


@pytest.fixture
def attempts() -> InMemoryHostedNativeAttemptStore:
    return InMemoryHostedNativeAttemptStore()


@pytest.fixture
def reader() -> InMemorySharedTaskDirectoryReader:
    return InMemorySharedTaskDirectoryReader()


@pytest.fixture
def round_() -> FakeRound:
    return FakeRound()


# ---------------------------------------------------------------------------
# Worker side: ack and submit
# ---------------------------------------------------------------------------


async def test_acknowledged_meta_becomes_one_event_delivered_once(attempts, reader, round_) -> None:
    attempt = make_attempt()
    await attempts.add(attempt)
    reader.put(
        TEAM,
        str(attempt.id),
        "meta.json",
        spike_meta(attempt.id, status="in_progress", acknowledged_at=ACKNOWLEDGED_AT),
    )
    service = observer(attempts, reader, round_)

    first = await service.run_once()

    assert (first.scanned, first.events_recorded, first.applied, first.skipped_duplicates) == (
        1,
        1,
        1,
        0,
    )
    assert first.errors == ()
    assert len(round_.events) == 1
    event = round_.events[0]
    assert event.attempt_id == attempt.id
    assert event.kind is EventKind.ACKNOWLEDGED
    assert event.marker == ACKNOWLEDGED_AT
    assert event.observed_at == datetime(2026, 9, 2, 20, 0, 51, tzinfo=UTC)
    assert event.result is None
    rows = await attempts.list_events(attempt.id)
    assert len(rows) == 1
    assert rows[0].kind is EventKind.ACKNOWLEDGED
    assert rows[0].marker == ACKNOWLEDGED_AT
    assert rows[0].applied_at == CLOCK_NOW

    second = await service.run_once()

    assert (second.events_recorded, second.applied, second.skipped_duplicates) == (0, 0, 1)
    assert len(round_.events) == 1
    assert len(await attempts.list_events(attempt.id)) == 1


async def test_submitted_meta_waits_for_result_md(attempts, reader, round_) -> None:
    """copaw pushes the directory file by file: ``meta.json`` may land a tick early."""

    attempt = make_attempt(phase=AttemptPhase.ACKNOWLEDGED)
    await attempts.add(attempt)
    reader.put(
        TEAM,
        str(attempt.id),
        "meta.json",
        spike_meta(
            attempt.id,
            status="submitted",
            acknowledged_at=ACKNOWLEDGED_AT,
            submitted_at=SUBMITTED_AT,
        ),
    )
    service = observer(attempts, reader, round_)

    early = await service.run_once()

    assert early.events_recorded == 0
    assert early.errors == ()
    assert round_.events == []
    assert await attempts.list_events(attempt.id) == ()

    result_text = spike_result(attempt.id)
    reader.put(TEAM, str(attempt.id), "result.md", result_text)

    late = await service.run_once()

    assert (late.events_recorded, late.applied) == (1, 1)
    assert len(round_.events) == 1
    event = round_.events[0]
    assert event.kind is EventKind.SUBMITTED
    assert event.marker == SUBMITTED_AT
    assert event.observed_at == datetime(2026, 9, 2, 20, 27, 4, tzinfo=UTC)
    assert event.result is not None
    assert event.result.status is SubmitStatus.SUCCESS
    assert event.result.summary == SPIKE_SUMMARY
    assert len(event.result.deliverables) == 4
    assert event.result.deliverables[0] == f"shared/tasks/{attempt.id}/candidate/candidate.bundle"
    assert event.payload["status"] == "SUCCESS"
    assert event.payload["deliverables"] == list(event.result.deliverables)
    assert event.payload["result_etag"] == hashlib.sha256(result_text.encode()).hexdigest()
    assert event.payload["task_dir"] == str(attempt.id)


async def test_acknowledged_attempt_does_not_replay_its_ack(attempts, reader, round_) -> None:
    """Once the row is past ``NOTIFIED`` the ack stamp is history, not an event."""

    attempt = make_attempt(phase=AttemptPhase.ACKNOWLEDGED)
    await attempts.add(attempt)
    reader.put(
        TEAM,
        str(attempt.id),
        "meta.json",
        spike_meta(attempt.id, status="in_progress", acknowledged_at=ACKNOWLEDGED_AT),
    )

    report = await observer(attempts, reader, round_).run_once()

    assert report.events_recorded == 0
    assert round_.events == []


async def test_directory_name_is_the_claim_not_the_repomesh_block(attempts, reader, round_) -> None:
    """D-6: the ``repomesh`` block is a publish-time snapshot; the row's id wins."""

    attempt = make_attempt(phase=AttemptPhase.ACKNOWLEDGED)
    await attempts.add(attempt)
    reader.put(
        TEAM,
        str(attempt.id),
        "meta.json",
        spike_meta(
            attempt.id,
            status="submitted",
            submitted_at=SUBMITTED_AT,
            repomesh={
                "kind": "not-a-kind",
                "task_id": str(uuid4()),
                "attempt_id": str(uuid4()),
            },
        ),
    )
    reader.put(TEAM, str(attempt.id), "result.md", spike_result(attempt.id))

    report = await observer(attempts, reader, round_).run_once()

    assert (report.events_recorded, report.applied) == (1, 1)
    assert report.errors == ()
    assert [event.attempt_id for event in round_.events] == [attempt.id]
    assert set(reader.reads) == {key(attempt.id, "meta.json"), key(attempt.id, "result.md")}


async def test_a_directory_without_a_row_is_never_opened(attempts, reader, round_) -> None:
    stray = uuid4()
    reader.put(TEAM, str(stray), "meta.json", spike_meta(stray, status="in_progress"))
    attempt = make_attempt()
    await attempts.add(attempt)

    report = await observer(attempts, reader, round_).run_once()

    assert report.scanned == 1
    assert report.events_recorded == 0
    assert all(str(stray) not in read for read in reader.reads)
    assert reader.reads == [key(attempt.id, "meta.json")]


async def test_a_terminal_attempt_is_not_scanned(attempts, reader, round_) -> None:
    fenced = make_attempt(
        phase=AttemptPhase.FENCED, fenced_at=T0 + timedelta(minutes=1), fence_reason="restart"
    )
    await attempts.add(fenced)
    reader.put(
        TEAM,
        str(fenced.id),
        "meta.json",
        spike_meta(fenced.id, status="submitted", submitted_at=SUBMITTED_AT),
    )
    reader.put(TEAM, str(fenced.id), "result.md", spike_result(fenced.id))
    live = make_attempt(notified_at=T0 + timedelta(seconds=1))
    await attempts.add(live)

    report = await observer(attempts, reader, round_).run_once()

    assert report.scanned == 1
    assert report.events_recorded == 0
    assert all(str(fenced.id) not in read for read in reader.reads)
    assert round_.events == []


# ---------------------------------------------------------------------------
# Review side
# ---------------------------------------------------------------------------


async def test_leader_ack_is_not_an_event_but_its_verdict_is(attempts, reader, round_) -> None:
    review_id = uuid4()
    attempt = make_attempt(
        phase=AttemptPhase.REVIEW_PENDING,
        review_dir=f"teams/{TEAM}/shared/tasks/{review_id}",
        review_budget_until=T0 + timedelta(minutes=60),
    )
    await attempts.add(attempt)
    review_ack = "2026-09-02T20:29:10Z"
    review_submitted = "2026-09-02T20:35:42Z"
    reader.put(
        TEAM,
        str(review_id),
        "meta.json",
        spike_meta(review_id, status="in_progress", acknowledged_at=review_ack),
    )
    service = observer(attempts, reader, round_)

    acked = await service.run_once()

    assert acked.scanned == 1
    assert acked.events_recorded == 0
    assert acked.errors == ()
    assert round_.events == []

    reader.put(
        TEAM,
        str(review_id),
        "meta.json",
        spike_meta(
            review_id,
            status="submitted",
            acknowledged_at=review_ack,
            submitted_at=review_submitted,
        ),
    )
    reader.put(
        TEAM,
        str(review_id),
        "result.md",
        "STATUS: SUCCESS\n"
        "SUMMARY: VERDICT: ACCEPT — the diff stays inside src/** and the frozen tests pass.\n",
    )

    verdict = await service.run_once()

    assert (verdict.events_recorded, verdict.applied) == (1, 1)
    assert len(round_.events) == 1
    event = round_.events[0]
    assert event.kind is EventKind.REVIEW_SUBMITTED
    assert event.attempt_id == attempt.id
    assert event.marker == review_submitted
    assert event.observed_at == datetime(2026, 9, 2, 20, 35, 42, tzinfo=UTC)
    assert event.result is not None
    assert event.result.status is SubmitStatus.SUCCESS
    assert event.result.summary.startswith("VERDICT: ACCEPT")
    assert event.payload["review_dir"] == attempt.review_dir
    assert event.payload["task_dir"] == str(review_id)
    # The construction directory itself was never touched on the review side.
    assert all(str(attempt.id) not in read for read in reader.reads)


async def test_review_pending_without_a_review_dir_reads_nothing(attempts, reader, round_) -> None:
    attempt = make_attempt(phase=AttemptPhase.REVIEW_PENDING)
    await attempts.add(attempt)
    reader.put(
        TEAM,
        str(attempt.id),
        "meta.json",
        spike_meta(attempt.id, status="submitted", submitted_at=SUBMITTED_AT),
    )

    report = await observer(attempts, reader, round_).run_once()

    assert report.scanned == 1
    assert reader.reads == []
    assert round_.events == []


# ---------------------------------------------------------------------------
# Malformed files and failing readers
# ---------------------------------------------------------------------------


async def test_malformed_result_md_is_an_error_not_an_event(attempts, reader, round_) -> None:
    attempt = make_attempt(phase=AttemptPhase.ACKNOWLEDGED)
    await attempts.add(attempt)
    reader.put(
        TEAM,
        str(attempt.id),
        "meta.json",
        spike_meta(attempt.id, status="submitted", submitted_at=SUBMITTED_AT),
    )
    reader.put(TEAM, str(attempt.id), "result.md", "STATUS: DONE\nSUMMARY: finished\n")

    report = await observer(attempts, reader, round_).run_once()

    assert report.events_recorded == 0
    assert len(report.errors) == 1
    assert report.errors[0].startswith(f"{attempt.id}: {attempt.id}/result.md: ")
    assert "DONE" in report.errors[0]
    assert round_.events == []
    assert await attempts.list_events(attempt.id) == ()


async def test_malformed_meta_json_is_an_error_not_an_event(attempts, reader, round_) -> None:
    attempt = make_attempt()
    await attempts.add(attempt)
    reader.put(TEAM, str(attempt.id), "meta.json", "status: submitted\n")

    report = await observer(attempts, reader, round_).run_once()

    assert report.events_recorded == 0
    assert len(report.errors) == 1
    assert report.errors[0].startswith(f"{attempt.id}: {attempt.id}/meta.json: ")
    assert "not valid JSON" in report.errors[0]
    assert round_.events == []
    assert await attempts.list_events(attempt.id) == ()


async def test_one_failing_directory_does_not_stop_the_scan(attempts, reader, round_) -> None:
    broken = make_attempt(notified_at=T0)
    healthy = make_attempt(notified_at=T0 + timedelta(seconds=1))
    await attempts.add(broken)
    await attempts.add(healthy)
    reader.put(TEAM, str(broken.id), "meta.json", spike_meta(broken.id, status="in_progress"))
    reader.failures[key(broken.id, "meta.json")] = RuntimeError("minio unreachable")
    reader.put(
        TEAM,
        str(healthy.id),
        "meta.json",
        spike_meta(healthy.id, status="in_progress", acknowledged_at=ACKNOWLEDGED_AT),
    )

    report = await observer(attempts, reader, round_).run_once()

    assert report.scanned == 2
    assert report.errors == (f"{broken.id}: minio unreachable",)
    assert (report.events_recorded, report.applied) == (1, 1)
    assert [event.attempt_id for event in round_.events] == [healthy.id]


# ---------------------------------------------------------------------------
# The event inbox: unapplied rows, the round's answers
# ---------------------------------------------------------------------------


async def test_recorded_but_unapplied_event_is_redelivered(attempts, reader, round_) -> None:
    attempt = make_attempt()
    await attempts.add(attempt)
    reader.put(
        TEAM,
        str(attempt.id),
        "meta.json",
        spike_meta(attempt.id, status="in_progress", acknowledged_at=ACKNOWLEDGED_AT),
    )
    stale = HostedNativeEvent(
        id=uuid4(),
        attempt_id=attempt.id,
        kind=EventKind.ACKNOWLEDGED,
        marker=ACKNOWLEDGED_AT,
        payload={"task_dir": str(attempt.id), "acknowledged_at": ACKNOWLEDGED_AT},
        observed_at=datetime(2026, 9, 2, 20, 0, 51, tzinfo=UTC),
        applied_at=None,
    )
    assert await attempts.record_event(stale)

    report = await observer(attempts, reader, round_).run_once()

    assert (report.events_recorded, report.applied, report.skipped_duplicates) == (0, 1, 0)
    assert [event.marker for event in round_.events] == [ACKNOWLEDGED_AT]
    rows = await attempts.list_events(attempt.id)
    assert [row.id for row in rows] == [stale.id]
    assert rows[0].applied_at == CLOCK_NOW


async def test_a_round_that_raises_leaves_the_row_unapplied_for_the_next_tick(
    attempts, reader
) -> None:
    attempt = make_attempt()
    await attempts.add(attempt)
    reader.put(
        TEAM,
        str(attempt.id),
        "meta.json",
        spike_meta(attempt.id, status="in_progress", acknowledged_at=ACKNOWLEDGED_AT),
    )
    failing = FakeRound(error=RuntimeError("round exploded"))

    report = await observer(attempts, reader, failing).run_once()

    assert report.errors == (f"{attempt.id}: round exploded",)
    assert (report.events_recorded, report.applied) == (1, 0)
    rows = await attempts.list_events(attempt.id)
    assert len(rows) == 1
    assert rows[0].applied_at is None

    recovered = FakeRound()
    retry = await observer(attempts, reader, recovered).run_once()

    assert (retry.events_recorded, retry.applied, retry.skipped_duplicates) == (0, 1, 0)
    assert [event.marker for event in recovered.events] == [ACKNOWLEDGED_AT]
    assert (await attempts.list_events(attempt.id))[0].applied_at == CLOCK_NOW


async def test_an_event_the_round_ignores_is_still_applied(attempts, reader) -> None:
    attempt = make_attempt()
    await attempts.add(attempt)
    reader.put(
        TEAM,
        str(attempt.id),
        "meta.json",
        spike_meta(attempt.id, status="in_progress", acknowledged_at=ACKNOWLEDGED_AT),
    )
    ignoring = FakeRound(RoundOutcome.IGNORED)

    report = await observer(attempts, reader, ignoring).run_once()

    assert (report.events_recorded, report.applied, report.ignored) == (1, 0, 1)
    assert (await attempts.list_events(attempt.id))[0].applied_at == CLOCK_NOW


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-09-02T20:00:51Z", datetime(2026, 9, 2, 20, 0, 51, tzinfo=UTC)),
        ("2026-09-02T20:00:51+00:00", datetime(2026, 9, 2, 20, 0, 51, tzinfo=UTC)),
        ("2026-09-02T22:00:51+02:00", datetime(2026, 9, 2, 20, 0, 51, tzinfo=UTC)),
        ("2026-09-02T20:00:51", datetime(2026, 9, 2, 20, 0, 51, tzinfo=UTC)),
        ("  2026-09-02T20:00:51Z  ", datetime(2026, 9, 2, 20, 0, 51, tzinfo=UTC)),
        ("garbage", None),
        ("", None),
        (None, None),
        (1756843251, None),
    ],
)
def test_parse_copaw_timestamp(value: object, expected: datetime | None) -> None:
    assert parse_copaw_timestamp(value) == expected


def test_parse_result_markdown_reads_the_spike_sample() -> None:
    spike = UUID("ca0ef2b0-a6c7-4d03-a0e3-b7bf13aef13a")

    result = parse_result_markdown(spike_result(spike))

    assert result.status is SubmitStatus.SUCCESS
    assert result.summary == SPIKE_SUMMARY
    assert result.deliverables == (
        f"shared/tasks/{spike}/candidate/candidate.bundle",
        f"shared/tasks/{spike}/candidate/candidate.diff",
        f"shared/tasks/{spike}/candidate/changes.json",
        f"shared/tasks/{spike}/candidate/evidence.json",
    )
    assert result.notes == ()


def test_parse_result_markdown_collects_notes() -> None:
    result = parse_result_markdown(
        "STATUS: SUCCESS_WITH_NOTES\n"
        "SUMMARY: done with caveats\n"
        "\n"
        "DELIVERABLES:\n"
        "- candidate/candidate.bundle\n"
        "\n"
        "NOTES:\n"
        "- the flaky test was retried once\n"
        "-   trailing spaces are trimmed   \n"
    )

    assert result.status is SubmitStatus.SUCCESS_WITH_NOTES
    assert result.deliverables == ("candidate/candidate.bundle",)
    assert result.notes == ("the flaky test was retried once", "trailing spaces are trimmed")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("STATUS: SUCCESS\n", "summary is required"),
        ("STATUS: DONE\nSUMMARY: x\n", "invalid result status: DONE"),
        ("SUMMARY: no status\n", "invalid result status: <missing>"),
        ("", "invalid result status: <missing>"),
    ],
)
def test_parse_result_markdown_rejects_malformed_text(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_result_markdown(text)


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------


async def test_disk_reader_reads_and_stats_the_publisher_layout(tmp_path: Path) -> None:
    task_dir = uuid4()
    target = tmp_path / "teams" / TEAM / "shared" / "tasks" / str(task_dir) / "meta.json"
    target.parent.mkdir(parents=True)
    content = spike_meta(task_dir, status="assigned").encode()
    target.write_bytes(content)
    disk = DiskSharedTaskDirectoryReader(tmp_path)

    assert await disk.read(TEAM, str(task_dir), "meta.json") == content
    stat = await disk.stat(TEAM, str(task_dir), "meta.json")
    assert stat is not None
    assert stat.size == len(content)
    assert stat.etag == hashlib.sha256(content).hexdigest()
    assert stat.last_modified is not None
    assert stat.last_modified.tzinfo is not None

    assert await disk.read(TEAM, str(task_dir), "result.md") is None
    assert await disk.stat(TEAM, str(task_dir), "result.md") is None
    assert await disk.read(TEAM, str(uuid4()), "meta.json") is None


async def test_disk_reader_reads_nested_names(tmp_path: Path) -> None:
    task_dir = uuid4()
    target = tmp_path / "teams" / TEAM / "shared" / "tasks" / str(task_dir) / "base/package.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"{}")

    assert await DiskSharedTaskDirectoryReader(tmp_path).read(
        TEAM, str(task_dir), "base/package.json"
    ) == b"{}"


@pytest.mark.parametrize(
    ("team", "task_dir", "name"),
    [
        ("..", "d", "meta.json"),
        ("a/b", "d", "meta.json"),
        ("a\\b", "d", "meta.json"),
        ("", "d", "meta.json"),
        (".", "d", "meta.json"),
        ("t", "..", "meta.json"),
        ("t", "x/y", "meta.json"),
        ("t", "", "meta.json"),
        ("t", "d", ""),
        ("t", "d", "/meta.json"),
        ("t", "d", "../meta.json"),
        ("t", "d", "base//package.json"),
        ("t", "d", "base/./package.json"),
    ],
)
def test_shared_task_key_refuses_path_walking_segments(team: str, task_dir: str, name: str) -> None:
    with pytest.raises(ValueError, match="invalid shared task"):
        shared_task_key(team, task_dir, name)


def test_shared_task_key_spells_the_publisher_prefix() -> None:
    key_ = shared_task_key("t", "d", "base/package.json")
    assert key_ == "teams/t/shared/tasks/d/base/package.json"


async def test_in_memory_reader_stat_etag_is_the_content_sha256() -> None:
    memory = InMemorySharedTaskDirectoryReader()
    memory.put("t", "d", "result.md", "STATUS: SUCCESS\nSUMMARY: ok\n")

    stat = await memory.stat("t", "d", "result.md")

    assert stat is not None
    assert stat.size == len(b"STATUS: SUCCESS\nSUMMARY: ok\n")
    assert stat.etag == hashlib.sha256(b"STATUS: SUCCESS\nSUMMARY: ok\n").hexdigest()
    assert stat.last_modified is None
    assert await memory.stat("t", "d", "meta.json") is None
    assert memory.reads == ["teams/t/shared/tasks/d/result.md", "teams/t/shared/tasks/d/meta.json"]


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def test_start_and_close_round_trip(attempts, reader, round_) -> None:
    attempt = make_attempt()
    await attempts.add(attempt)
    reader.put(
        TEAM,
        str(attempt.id),
        "meta.json",
        spike_meta(attempt.id, status="in_progress", acknowledged_at=ACKNOWLEDGED_AT),
    )
    service = observer(attempts, reader, round_, interval_seconds=0.01)

    await service.start()
    await service.start()  # idempotent: one loop, not two
    await asyncio.sleep(0.05)
    await asyncio.wait_for(service.close(), timeout=2)
    await asyncio.wait_for(service.close(), timeout=2)  # closing twice is a no-op

    assert len(round_.events) == 1
    assert round_.events[0].kind is EventKind.ACKNOWLEDGED
    assert (await attempts.list_events(attempt.id))[0].applied_at == CLOCK_NOW
