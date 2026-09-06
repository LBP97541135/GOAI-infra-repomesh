"""``HostedNativeRound`` end to end against its real collaborators (spec §4.2 M1).

The round is driven the way the task orchestrator, the shared-directory
observer and the recovery loop drive it — ``open`` / ``observe`` / ``expire`` —
over the real assignment, reservation and task stores on SQLite, the real
disk publisher and the disk reader on one ``tmp_path`` (so the round reads
exactly what the publisher wrote), the real ``TaskExecutionState`` and the
in-memory attempt store. Only the seams the spec leaves to later milestones
(base bundle M6, verification launcher M5, room delivery, escalation and
recovery) are fakes that record what they were handed. Every assertion pins
one bullet of §4.2 M1 or one of D-3, D-8, D-9, D-12, D-13 and ``review.md``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from repomesh.integrations.agentteams.task_package import HELPER_COMMANDS
from repomesh.integrations.agentteams.task_publishing import AgentTeamsTaskPublisher
from repomesh.integrations.hosted_native import messages
from repomesh.integrations.hosted_native.contracts import (
    AttemptPhase,
    BaseBundle,
    CandidateForVerification,
    ConstructionPolicy,
    EventKind,
    HostedNativeAttempt,
    ReviewVerdict,
    RoundOpened,
    RoundOutcome,
    RoundTransition,
    SharedTaskEvent,
    SubmitStatus,
    SubmittedResult,
)
from repomesh.integrations.hosted_native.round import (
    ATTEMPT_PAYLOAD_SCHEMA,
    HostedNativeRound,
    HostedNativeRoundError,
)
from repomesh.integrations.hosted_native.storage import DiskSharedTaskDirectoryReader
from repomesh.integrations.hosted_native.store import InMemoryHostedNativeAttemptStore
from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalStatus,
    AgentPrincipalView,
    AgentRole,
)
from repomesh.modules.agent_runtime.contracts import WorkerExecutionStatus
from repomesh.modules.agent_runtime.execution_reservation import (
    PostgresWorkerExecutionReservationStore,
    WorkerExecutionReservationRecord,
)
from repomesh.modules.collaboration.contracts import (
    CollaborationDeliveryStatus,
    CollaborationMessageKind,
    CollaborationMessageView,
    SendCollaborationMessageCommand,
)
from repomesh.modules.project.contracts import (
    ProjectAgentTopologyView,
    ProjectTeamRuntimeStatus,
    RepositoryTeamView,
)
from repomesh.modules.task_orchestration.application import TaskExecutionState
from repomesh.modules.task_orchestration.assignment import (
    AssignmentReason,
    PostgresTaskAssignmentStore,
)
from repomesh.modules.task_orchestration.contracts import PathPolicy, TaskStatus, TaskView
from repomesh.modules.task_orchestration.domain import Task
from repomesh.modules.task_orchestration.infrastructure import PostgresTaskStore
from repomesh.persistence import Database
from repomesh.persistence.base import ALL_SCHEMAS

TEAM = "repomesh-team-x"
TEAM_ROOM = "!team:hs"
LEADER_ROOM = "!leader:hs"
BASE_SHA = "a" * 40
HEAD_SHA = "c42e875cd097431264b52f7c051949b0686591f7"
ATTEMPT_BUDGET = 2700
REVIEW_BUDGET = 900
HELPER_SCRIPT = b"#!/bin/bash\necho helper\n"
RUN_ID = UUID("00000000-0000-0000-0000-00000000f00d")
TEST_COMMANDS = ("python scripts/run_tests.py",)
POLICY = ConstructionPolicy(
    policy=PathPolicy(allowed_paths=("src/**", "tests/**"), denied_paths=(".git/**",)),
    test_commands=TEST_COMMANDS,
)
CANDIDATE_BUNDLE = b"# v2 git bundle\ncandidate\n"
CANDIDATE_DIFF = (
    "--- a/src/pricing_core/quote.py\n"
    "+++ b/src/pricing_core/quote.py\n"
    "@@ -1,3 +1,4 @@\n"
    '+ZERO_DECIMAL_CURRENCIES = {"JPY"}\n'
)


# ---------------------------------------------------------------------------
# Doubles for the seams later milestones fill in
# ---------------------------------------------------------------------------


class Clock:
    """The round's clock; the SQL stores keep using real time, so tests that
    compare the two allow a few seconds of slack (``_close``)."""

    def __init__(self) -> None:
        self.now = datetime.now(UTC).replace(microsecond=0)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> datetime:
        self.now += timedelta(seconds=seconds)
        return self.now


class Directory:
    def __init__(self, *principals: AgentPrincipalView) -> None:
        self._principals = {principal.id: principal for principal in principals}

    async def get_view(self, agent_id: UUID) -> AgentPrincipalView | None:
        return self._principals.get(agent_id)

    async def list_views(self) -> tuple[AgentPrincipalView, ...]:
        return tuple(self._principals.values())


class Topologies:
    def __init__(self, topology: ProjectAgentTopologyView) -> None:
        self.topology = topology

    async def get_view(self, project_id: UUID) -> ProjectAgentTopologyView | None:
        return self.topology if project_id == self.topology.project_id else None


class RecordingCollaboration:
    def __init__(self) -> None:
        self.sent: list[tuple[SendCollaborationMessageCommand, str]] = []

    async def send(
        self, command: SendCollaborationMessageCommand, *, idempotency_key: str
    ) -> CollaborationMessageView:
        self.sent.append((command, idempotency_key))
        return CollaborationMessageView(
            id=uuid4(),
            organization_id=command.organization_id,
            project_id=command.project_id,
            repository_id=command.repository_id,
            task_id=command.task_id,
            sender_agent_id=command.sender_agent_id,
            recipient_agent_id=command.recipient_agent_id,
            kind=command.kind,
            subject=command.subject,
            body=command.body,
            room_id="!delivered:hs",
            status=CollaborationDeliveryStatus.DELIVERED,
            event_id=f"$event{len(self.sent)}",
            correlation_id=command.correlation_id or uuid4(),
            created_at=datetime.now(UTC),
        )


class FakeBundles:
    def __init__(self) -> None:
        self.built: list[UUID] = []

    async def build(self, repository_id: UUID) -> BaseBundle:
        self.built.append(repository_id)
        return BaseBundle(base_sha=BASE_SHA, bundle=b"BUNDLE")


class FakePolicies:
    def __init__(self) -> None:
        self.policy = POLICY
        self.resolved: list[tuple[UUID, UUID]] = []

    async def resolve(self, task_id: UUID, *, worker_agent_id: UUID) -> ConstructionPolicy:
        self.resolved.append((task_id, worker_agent_id))
        return self.policy


class FakeVerification:
    def __init__(self) -> None:
        self.launched: list[tuple[CandidateForVerification, HostedNativeAttempt]] = []

    async def launch(
        self, candidate: CandidateForVerification, *, attempt: HostedNativeAttempt
    ) -> UUID:
        self.launched.append((candidate, attempt))
        return RUN_ID


class FlakyPublisher(AgentTeamsTaskPublisher):
    """The real disk publisher, with a switch that makes ``publish`` fail."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.failure: Exception | None = None
        self.published: list[str] = []

    async def publish(self, task: TaskView, **kwargs):  # type: ignore[override]
        if self.failure is not None:
            raise self.failure
        published = await super().publish(task, **kwargs)
        self.published.append(published.task_path)
        return published


# ---------------------------------------------------------------------------
# The world one test lives in
# ---------------------------------------------------------------------------


@dataclass
class World:
    root: Path
    database: Database
    org_leader: AgentPrincipalView
    repo_leader: AgentPrincipalView
    worker: AgentPrincipalView
    worker2: AgentPrincipalView
    task: Task
    tasks: PostgresTaskStore
    assignments: PostgresTaskAssignmentStore
    reservations: PostgresWorkerExecutionReservationStore
    publisher: FlakyPublisher
    attempts: InMemoryHostedNativeAttemptStore
    collaboration: RecordingCollaboration
    bundles: FakeBundles
    policies: FakePolicies
    verification: FakeVerification
    clock: Clock
    escalated: list[tuple[UUID, str]]
    recovered: list[tuple[UUID, str]]
    round: HostedNativeRound

    async def open(self, key: str = "open-1") -> RoundOpened:
        return await self.round.open(self.task.id, idempotency_key=key)

    async def task_view(self) -> TaskView:
        view = await self.tasks.get_view(self.task.id)
        assert view is not None
        return view

    async def reservation_rows(self) -> list[tuple[int, str, str | None]]:
        """``(attempt, status, error_detail)`` of every reservation row, oldest first."""

        async with self.database.transaction() as session:
            rows = await session.execute(
                select(
                    WorkerExecutionReservationRecord.attempt,
                    WorkerExecutionReservationRecord.status,
                    WorkerExecutionReservationRecord.error_detail,
                ).order_by(WorkerExecutionReservationRecord.attempt)
            )
            return [tuple(row) for row in rows.all()]

    def task_dir(self, attempt_id: UUID) -> Path:
        return self.root / "teams" / TEAM / "shared" / "tasks" / str(attempt_id)

    def write_candidate(
        self,
        attempt_id: UUID,
        *,
        base_sha: str = BASE_SHA,
        head_sha: str = HEAD_SHA,
        omit: tuple[str, ...] = (),
        attempt_id_in_files: UUID | None = None,
    ) -> Path:
        """Write the four files helper ``bundle`` writes, shaped like the wave-0 spike's."""

        named = str(attempt_id_in_files or attempt_id)
        changes = {
            "attempt_id": named,
            "base_sha": base_sha,
            "head_sha": head_sha,
            "changed_files": [
                {"status": "M", "path": "src/pricing_core/quote.py"},
                {"status": "M", "path": "tests/test_quote.py"},
            ],
        }
        evidence = {
            "attempt_id": named,
            "base_sha": base_sha,
            "head_sha": head_sha,
            "tree": "8" * 40,
            "tests_ran_at": "2026-09-05T10:26:44+00:00",
            "tests": [
                {
                    "command": TEST_COMMANDS[0],
                    "exit_code": 0,
                    "excerpt": "Ran 9 tests in 0.001s\n\nOK",
                }
            ],
            "produced_at": "2026-09-05T10:26:50+00:00",
        }
        files = {
            "candidate/candidate.bundle": CANDIDATE_BUNDLE,
            "candidate/candidate.diff": CANDIDATE_DIFF.encode(),
            "candidate/changes.json": (json.dumps(changes, indent=2) + "\n").encode(),
            "candidate/evidence.json": (json.dumps(evidence, indent=2) + "\n").encode(),
        }
        directory = self.task_dir(attempt_id)
        for name, content in files.items():
            if name in omit:
                continue
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return directory / "candidate"

    def event(
        self,
        attempt_id: UUID,
        kind: EventKind,
        marker: str,
        *,
        status: SubmitStatus | None = None,
        summary: str = "",
        observed_at: datetime | None = None,
    ) -> SharedTaskEvent:
        result = None if status is None else SubmittedResult(status=status, summary=summary)
        return SharedTaskEvent(
            attempt_id=attempt_id,
            kind=kind,
            marker=marker,
            observed_at=observed_at or self.clock.now,
            result=result,
        )

    async def submitted_candidate(self) -> UUID:
        """Open an attempt and take it to ``REVIEW_PENDING`` with a valid candidate."""

        opened = await self.open()
        self.write_candidate(opened.attempt.id)
        transition = await self.round.observe(
            self.event(
                opened.attempt.id,
                EventKind.SUBMITTED,
                "submitted-1",
                status=SubmitStatus.SUCCESS,
                summary="Implemented multi-currency quotes",
            )
        )
        assert transition.phase is AttemptPhase.REVIEW_PENDING
        return opened.attempt.id


def _principal(
    organization_id: UUID,
    role: AgentRole,
    *,
    leader_agent_id: UUID | None,
    repository_id: UUID | None,
    resource_name: str,
) -> AgentPrincipalView:
    return AgentPrincipalView(
        id=uuid4(),
        organization_id=organization_id,
        role=role,
        leader_agent_id=leader_agent_id,
        repository_id=repository_id,
        responsibility_paths=("src/**",),
        agentteams_resource_name=resource_name,
        status=AgentPrincipalStatus.ACTIVE,
    )


@pytest.fixture
async def make_world(tmp_path: Path):
    databases: list[Database] = []

    async def build(
        *, room_id: str | None = TEAM_ROOM, leader_room_id: str | None = LEADER_ROOM
    ) -> World:
        database = Database(
            f"sqlite+aiosqlite:///{tmp_path / f'round-{len(databases)}.db'}",
            schema_translate_map={schema: None for schema in ALL_SCHEMAS},
        )
        await database.create_all_for_tests()
        databases.append(database)

        organization_id = uuid4()
        project_id = uuid4()
        repository_id = uuid4()
        org_leader = _principal(
            organization_id,
            AgentRole.ORGANIZATION_LEADER,
            leader_agent_id=None,
            repository_id=None,
            resource_name="agt-org-leader",
        )
        repo_leader = _principal(
            organization_id,
            AgentRole.REPOSITORY_LEADER,
            leader_agent_id=org_leader.id,
            repository_id=repository_id,
            resource_name="agt-leader-x",
        )
        worker = _principal(
            organization_id,
            AgentRole.WORKER,
            leader_agent_id=repo_leader.id,
            repository_id=repository_id,
            resource_name="agt-worker-x",
        )
        worker2 = _principal(
            organization_id,
            AgentRole.WORKER,
            leader_agent_id=repo_leader.id,
            repository_id=repository_id,
            resource_name="agt-worker-y",
        )
        directory = Directory(org_leader, repo_leader, worker, worker2)
        topology = ProjectAgentTopologyView(
            id=uuid4(),
            organization_id=organization_id,
            project_id=project_id,
            organization_leader_id=org_leader.id,
            repository_teams=(
                RepositoryTeamView(
                    id=uuid4(),
                    project_id=project_id,
                    repository_id=repository_id,
                    leader_agent_id=repo_leader.id,
                    worker_agent_ids=(worker.id, worker2.id),
                    agentteams_team_name=TEAM,
                    runtime_status=ProjectTeamRuntimeStatus.READY,
                    room_id=room_id,
                    leader_room_id=leader_room_id,
                ),
            ),
        )

        task = Task(
            organization_id=organization_id,
            project_id=project_id,
            repository_id=repository_id,
            assigned_by_agent_id=repo_leader.id,
            assignee_agent_id=worker.id,
            title="Add multi-currency quotes",
            instruction="Add a currency parameter to quote() with per-currency rounding.",
            acceptance=("JPY quotes round to whole yen",),
        )
        tasks = PostgresTaskStore(database)
        await tasks.add(task, idempotency_key=f"task:{task.id}", request_fingerprint="f" * 71)

        escalated: list[tuple[UUID, str]] = []
        recovered: list[tuple[UUID, str]] = []

        async def escalate(view: TaskView, reason: str) -> None:
            escalated.append((view.id, reason))

        async def recover(attempt: HostedNativeAttempt, reason: str) -> None:
            recovered.append((attempt.id, reason))

        assignments = PostgresTaskAssignmentStore(database)
        reservations = PostgresWorkerExecutionReservationStore(database)
        publisher = FlakyPublisher(tmp_path)
        attempts = InMemoryHostedNativeAttemptStore()
        collaboration = RecordingCollaboration()
        bundles = FakeBundles()
        policies = FakePolicies()
        verification = FakeVerification()
        clock = Clock()
        round_ = HostedNativeRound(
            tasks=tasks,
            directory=directory,
            topologies=Topologies(topology),
            assignments=assignments,
            reservations=reservations,
            states=TaskExecutionState(directory, tasks),
            publisher=publisher,
            collaboration=collaboration,
            attempts=attempts,
            reader=DiskSharedTaskDirectoryReader(tmp_path),
            bundles=bundles,
            policies=policies,
            verification=verification,
            escalate=escalate,
            recover=recover,
            attempt_budget_seconds=ATTEMPT_BUDGET,
            review_budget_seconds=REVIEW_BUDGET,
            clock=clock,
            helper_script=HELPER_SCRIPT,
        )
        return World(
            root=tmp_path,
            database=database,
            org_leader=org_leader,
            repo_leader=repo_leader,
            worker=worker,
            worker2=worker2,
            task=task,
            tasks=tasks,
            assignments=assignments,
            reservations=reservations,
            publisher=publisher,
            attempts=attempts,
            collaboration=collaboration,
            bundles=bundles,
            policies=policies,
            verification=verification,
            clock=clock,
            escalated=escalated,
            recovered=recovered,
            round=round_,
        )

    yield build
    for database in databases:
        await database.dispose()


@pytest.fixture
async def world(make_world) -> World:
    return await make_world()


def _aware(value: datetime | None) -> datetime:
    assert value is not None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _close(actual: datetime | None, expected: datetime, *, seconds: int = 10) -> bool:
    return abs(_aware(actual) - expected) <= timedelta(seconds=seconds)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# open
# ---------------------------------------------------------------------------


async def test_open_reserves_starts_publishes_and_notifies_the_worker(world: World) -> None:
    opened = await world.open()

    attempt = opened.attempt
    assert opened.created is True
    assert attempt.phase is AttemptPhase.NOTIFIED
    assert await world.attempts.get(attempt.id) == attempt

    # Reservation: lease = attempt budget, bound to the assignment and the payload
    # recovery's expired-lease scan reads (D-9, D-12).
    assignment = await world.assignments.active(world.task.id)
    reservation = await world.reservations.get_active(world.task.id)
    assert assignment is not None and reservation is not None
    assert reservation.status is WorkerExecutionStatus.RUNNING
    assert reservation.worker_agent_id == world.worker.id
    assert _close(reservation.lease_expires_at, datetime.now(UTC) + timedelta(seconds=2700))
    assert reservation.assignment_attempt_id == assignment.id
    assert reservation.assignment_generation == 1
    assert reservation.task_payload is not None
    assert reservation.task_payload["schema"] == ATTEMPT_PAYLOAD_SCHEMA
    assert reservation.task_payload["attemptId"] == str(attempt.id)
    assert assignment.execution_id == reservation.id
    assert (await world.task_view()).status is TaskStatus.IN_PROGRESS

    # Package: one directory named after the attempt (D-8), the v2 layout.
    task_dir = world.task_dir(attempt.id)
    assert attempt.package_dir == f"teams/{TEAM}/shared/tasks/{attempt.id}"
    assert world.publisher.published == [attempt.package_dir]
    for name in (
        "manifest.json",
        "spec.md",
        "meta.json",
        "base/package.json",
        "base/tools/repomesh-work.sh",
        "base/base.bundle",
    ):
        assert (task_dir / name).is_file(), name
    control = _read_json(task_dir / "base/package.json")
    assert control["attempt_id"] == str(attempt.id)
    assert control["generation"] == 1
    assert control["base_sha"] == BASE_SHA
    assert control["helper_commands"] == list(HELPER_COMMANDS)
    assert control["test_commands"] == list(TEST_COMMANDS)
    assert (task_dir / "base/base.bundle").read_bytes() == b"BUNDLE"
    assert (task_dir / "base/tools/repomesh-work.sh").read_bytes() == HELPER_SCRIPT

    # Attempt row: copied from the team, the bundle, the assignment and the reservation.
    assert attempt.task_id == world.task.id
    assert attempt.worker_agent_id == world.worker.id
    assert attempt.leader_agent_id == world.repo_leader.id
    assert attempt.team_name == TEAM
    assert attempt.room_id == TEAM_ROOM
    assert attempt.base_sha == BASE_SHA
    assert attempt.generation == 1
    assert attempt.assignment_attempt_id == assignment.id
    assert attempt.execution_id == reservation.id
    assert attempt.budget_until == world.clock.now + timedelta(seconds=ATTEMPT_BUDGET)
    assert attempt.notified_at == world.clock.now

    # Exactly one room notice, to the worker, mentioning nobody (D-3, D-18).
    assert len(world.collaboration.sent) == 1
    command, key = world.collaboration.sent[0]
    assert command.recipient_agent_id == world.worker.id
    assert command.sender_agent_id == world.repo_leader.id
    assert command.kind is CollaborationMessageKind.TASK_ASSIGNMENT
    assert command.task_id == world.task.id and command.correlation_id == world.task.id
    assert command.subject == world.task.title
    assert key.startswith("open-1:g1:") and key.endswith(":notice")
    body = command.body
    assert f"shared/tasks/{attempt.id}/spec.md" in body
    for line in HELPER_COMMANDS[:3]:
        assert line in body
    assert HELPER_COMMANDS[3] not in body  # ``clean`` is not the worker's to run
    for required in ("ack_task", "submit_task", "@admin"):
        assert required in body
    for forbidden in ("MCP", "start_assigned_task", "agt-leader-x", "@agt-leader"):
        assert forbidden not in body


async def test_open_replays_the_open_attempt_of_the_current_generation(world: World) -> None:
    first = await world.open()

    second = await world.open(key="open-2")

    assert second.created is False
    assert second.attempt.id == first.attempt.id
    assert second.attempt == first.attempt
    assert len(world.publisher.published) == 1
    assert len(world.collaboration.sent) == 1
    assert await world.reservation_rows() == [(1, "running", None)]
    assert list(world.attempts.attempts) == [first.attempt.id]


async def test_open_undoes_the_reservation_when_the_publisher_fails(world: World) -> None:
    world.publisher.failure = OSError("storage sync is down")

    with pytest.raises(HostedNativeRoundError, match="could not be opened") as raised:
        await world.open()

    assert "OSError: storage sync is down" in str(raised.value)
    rows = await world.reservation_rows()
    assert len(rows) == 1
    attempt_number, status, error_detail = rows[0]
    assert (attempt_number, status) == (1, WorkerExecutionStatus.FAILED.value)
    assert error_detail is not None and "storage sync is down" in error_detail
    assert await world.reservations.get_active(world.task.id) is None
    task = await world.task_view()
    assert task.status is TaskStatus.BLOCKED
    assert task.result_summary is not None
    assert task.result_summary.startswith("Hosted-native attempt could not be opened")
    assert await world.attempts.get_open_for_task(world.task.id) is None
    assert world.attempts.attempts == {}
    assert world.collaboration.sent == []

    world.publisher.failure = None
    reopened = await world.open(key="open-2")

    assert reopened.created is True
    reservation = await world.reservations.get_active(world.task.id)
    assert reservation is not None
    assert reservation.attempt == 2
    assert reservation.id == reopened.attempt.execution_id
    assert (await world.task_view()).status is TaskStatus.IN_PROGRESS
    assert len(world.collaboration.sent) == 1


async def test_open_refuses_a_worker_that_already_holds_another_execution(world: World) -> None:
    await world.reservations.reserve(
        organization_id=world.task.organization_id,
        project_id=world.task.project_id,
        repository_id=world.task.repository_id,
        task_id=uuid4(),
        worker_agent_id=world.worker.id,
        lease_owner="another-round",
        lease_seconds=600,
    )

    with pytest.raises(HostedNativeRoundError, match="WorkerCapacityUnavailable"):
        await world.open()

    assert (await world.task_view()).status is TaskStatus.BLOCKED
    assert await world.reservations.get_active(world.task.id) is None
    assert world.attempts.attempts == {}
    assert world.publisher.published == []
    assert world.collaboration.sent == []


async def test_open_refuses_a_team_without_a_room_before_reserving(make_world) -> None:
    world = await make_world(room_id=None)

    with pytest.raises(HostedNativeRoundError, match="team room is not ready"):
        await world.open()

    assert (await world.task_view()).status is TaskStatus.BLOCKED
    assert await world.reservation_rows() == []
    assert world.attempts.attempts == {}
    assert world.publisher.published == []


async def test_open_fences_a_stale_generation_attempt_and_releases_its_reservation(
    world: World,
) -> None:
    """A generation that advanced behind the round's back (no ``expire`` first).

    The stale attempt is fenced ``generation_advanced`` (D-9) and its
    reservation is failed with it: the reservation store binds a task's active
    execution to one worker, so without that release the new generation's
    ``open`` for another worker would be refused with "different task binding"
    and the task would sit blocked until the expired-lease scan reaped the old
    lease (up to a whole attempt budget later).
    """

    first = await world.open()
    stale_reservation = await world.reservations.get_active(world.task.id)
    assert stale_reservation is not None
    task = await world.task_view()
    await world.assignments.reassign(
        world.task.id,
        expected_task_version=task.version,
        expected_generation=1,
        replacement_worker_id=world.worker2.id,
        reason=AssignmentReason.OPERATOR,
    )

    second = await world.open(key="open-g2")

    assert second.created is True
    assert second.attempt.id != first.attempt.id
    assert second.attempt.generation == 2
    assert second.attempt.worker_agent_id == world.worker2.id
    assert second.attempt.phase is AttemptPhase.NOTIFIED
    stale = await world.attempts.get(first.attempt.id)
    assert stale is not None
    assert stale.phase is AttemptPhase.FENCED
    assert stale.fence_reason == "generation_advanced"
    released = await world.reservations.get(stale_reservation.id)
    assert released is not None
    assert released.status is WorkerExecutionStatus.FAILED
    assert released.error_detail == "generation_advanced"
    fresh = await world.reservations.get_active(world.task.id)
    assert fresh is not None
    assert fresh.id != stale_reservation.id
    assert fresh.worker_agent_id == world.worker2.id
    assert fresh.assignment_generation == 2
    assert (await world.attempts.get_open_for_task(world.task.id)) == second.attempt
    assert (await world.task_view()).status is TaskStatus.IN_PROGRESS


# ---------------------------------------------------------------------------
# observe: the worker side
# ---------------------------------------------------------------------------


async def test_acknowledged_moves_the_attempt_once(world: World) -> None:
    opened = await world.open()
    observed_at = datetime(2026, 9, 5, 10, 0, 30, tzinfo=UTC)
    event = world.event(
        opened.attempt.id, EventKind.ACKNOWLEDGED, "ack-1", observed_at=observed_at
    )

    transition = await world.round.observe(event)

    assert transition == RoundTransition(
        opened.attempt.id, RoundOutcome.APPLIED, AttemptPhase.ACKNOWLEDGED
    )
    attempt = await world.attempts.get(opened.attempt.id)
    assert attempt is not None
    assert attempt.phase is AttemptPhase.ACKNOWLEDGED
    assert attempt.acknowledged_at == observed_at
    assert attempt.updated_at == world.clock.now

    again = await world.round.observe(
        world.event(opened.attempt.id, EventKind.ACKNOWLEDGED, "ack-2")
    )

    assert again.outcome is RoundOutcome.IGNORED
    assert again.reason == "phase_mismatch"
    assert again.phase is AttemptPhase.ACKNOWLEDGED
    assert (await world.attempts.get(opened.attempt.id)) == attempt
    assert (await world.task_view()).status is TaskStatus.IN_PROGRESS


async def test_success_with_a_valid_candidate_opens_the_leader_review(world: World) -> None:
    opened = await world.open()
    attempt_id = opened.attempt.id
    world.write_candidate(attempt_id)
    world.clock.advance(600)
    event = world.event(
        attempt_id,
        EventKind.SUBMITTED,
        "submitted-1",
        status=SubmitStatus.SUCCESS,
        summary="Implemented multi-currency quotes",
        observed_at=world.clock.now - timedelta(seconds=5),
    )

    transition = await world.round.observe(event)

    assert transition == RoundTransition(
        attempt_id, RoundOutcome.APPLIED, AttemptPhase.REVIEW_PENDING
    )
    attempt = await world.attempts.get(attempt_id)
    assert attempt is not None
    assert attempt.phase is AttemptPhase.REVIEW_PENDING
    assert attempt.submit_status is SubmitStatus.SUCCESS
    assert attempt.submitted_at == event.observed_at
    assert attempt.review_budget_until == world.clock.now + timedelta(seconds=REVIEW_BUDGET)
    assert attempt.review_dir is not None
    review_id = attempt.review_dir.rsplit("/", 1)[1]
    assert attempt.review_dir == f"teams/{TEAM}/shared/tasks/{review_id}"
    assert review_id != str(attempt_id)
    assert world.publisher.published == [attempt.package_dir, attempt.review_dir]

    # The review package: the Leader's own task in the Leader's own room (D-3),
    # carrying the candidate and no bundle.
    review_dir = world.root / Path(attempt.review_dir)
    spec = (review_dir / "spec.md").read_text(encoding="utf-8")
    assert "+++ b/src/pricing_core/quote.py" in spec
    assert '+ZERO_DECIMAL_CURRENCIES = {"JPY"}' in spec
    for heading in ("## Frozen task the Worker had to implement", "## How to answer"):
        assert heading in spec
    assert "## Candidate diff" in spec
    meta = _read_json(review_dir / "meta.json")
    assert meta["assigned_to"] == "agt-leader-x"
    assert meta["room_id"] == LEADER_ROOM
    assert meta["repomesh"]["kind"] == "review"
    assert meta["repomesh"]["review_of"] == str(attempt_id)
    assert (review_dir / "review/candidate.diff").read_text(encoding="utf-8") == CANDIDATE_DIFF
    assert (review_dir / "review/changes.json").is_file()
    assert (review_dir / "review/evidence.json").is_file()
    assert not (review_dir / "base/base.bundle").exists()
    assert _read_json(review_dir / "base/package.json")["kind"] == "review"

    # One new notice: organization leader -> repository leader, naming nobody else.
    assert len(world.collaboration.sent) == 2
    command, key = world.collaboration.sent[1]
    assert command.recipient_agent_id == world.repo_leader.id
    assert command.sender_agent_id == world.org_leader.id
    assert command.kind is CollaborationMessageKind.TASK_ASSIGNMENT
    assert command.subject == f"Review candidate {HEAD_SHA[:8]}: {world.task.title}"
    assert key == f"review:{attempt_id}:notice"
    assert f"shared/tasks/{review_id}/spec.md" in command.body
    assert "VERDICT:" in command.body
    assert f"construction attempt {attempt_id}" in command.body
    for forbidden in ("agt-worker-x", "@agt-worker"):
        assert forbidden not in command.body
    assert world.escalated == []
    assert world.recovered == []
    assert (await world.task_view()).status is TaskStatus.IN_PROGRESS


async def test_success_without_a_leader_room_blocks_and_escalates(make_world) -> None:
    world = await make_world(leader_room_id=None)
    opened = await world.open()
    world.write_candidate(opened.attempt.id)

    transition = await world.round.observe(
        world.event(
            opened.attempt.id,
            EventKind.SUBMITTED,
            "submitted-1",
            status=SubmitStatus.SUCCESS,
            summary="done",
        )
    )

    assert transition.outcome is RoundOutcome.APPLIED
    assert transition.phase is AttemptPhase.BLOCKED
    attempt = await world.attempts.get(opened.attempt.id)
    assert attempt is not None
    assert attempt.phase is AttemptPhase.BLOCKED
    assert attempt.submit_status is SubmitStatus.SUCCESS
    task = await world.task_view()
    assert task.status is TaskStatus.BLOCKED
    assert task.result_summary is not None
    assert "review room is not ready" in task.result_summary
    assert world.escalated == [(world.task.id, "leader_room_missing")]
    assert world.recovered == []
    assert len(world.publisher.published) == 1
    assert len(world.collaboration.sent) == 1


@pytest.mark.parametrize(
    ("spoil", "why"),
    [
        ({"omit": ("candidate/evidence.json",)}, "missing candidate/evidence.json"),
        ({"attempt_id_in_files": UUID(int=7)}, "attempt_id mismatch"),
        ({"base_sha": "b" * 40}, "base_sha mismatch"),
    ],
    ids=["evidence_missing", "foreign_attempt_id", "other_base_sha"],
)
async def test_success_with_an_invalid_candidate_fails_the_attempt(
    world: World, spoil: dict, why: str
) -> None:
    opened = await world.open()
    attempt_id = opened.attempt.id
    world.write_candidate(attempt_id, **spoil)
    event = world.event(
        attempt_id, EventKind.SUBMITTED, "submitted-1", status=SubmitStatus.SUCCESS, summary="ok"
    )

    transition = await world.round.observe(event)

    assert transition == RoundTransition(attempt_id, RoundOutcome.APPLIED, AttemptPhase.FAILED)
    attempt = await world.attempts.get(attempt_id)
    assert attempt is not None
    assert attempt.phase is AttemptPhase.FAILED
    assert attempt.fence_reason is not None
    assert attempt.fence_reason.startswith("candidate_invalid")
    assert why in attempt.fence_reason
    assert attempt.fenced_at == world.clock.now
    assert attempt.submit_status is SubmitStatus.SUCCESS
    assert attempt.submitted_at == event.observed_at
    assert attempt.review_dir is None
    task = await world.task_view()
    assert task.status is TaskStatus.BLOCKED
    assert task.result_summary is not None
    assert task.result_summary.startswith("Hosted-native candidate rejected")
    assert world.recovered == [(attempt_id, "candidate_invalid")]
    assert world.escalated == []
    assert len(world.publisher.published) == 1
    assert len(world.collaboration.sent) == 1
    assert await world.attempts.get_open_for_task(world.task.id) is None


async def test_worker_blocked_blocks_the_task_and_hands_to_recovery(world: World) -> None:
    opened = await world.open()

    transition = await world.round.observe(
        world.event(
            opened.attempt.id,
            EventKind.SUBMITTED,
            "submitted-1",
            status=SubmitStatus.BLOCKED,
            summary="The base bundle does not contain scripts/run_tests.py",
        )
    )

    assert transition.outcome is RoundOutcome.APPLIED
    assert transition.phase is AttemptPhase.BLOCKED
    attempt = await world.attempts.get(opened.attempt.id)
    assert attempt is not None
    assert attempt.phase is AttemptPhase.BLOCKED
    assert attempt.submit_status is SubmitStatus.BLOCKED
    task = await world.task_view()
    assert task.status is TaskStatus.BLOCKED
    assert task.result_summary is not None
    assert task.result_summary.startswith("Worker reported BLOCKED")
    assert "does not contain scripts/run_tests.py" in task.result_summary
    assert world.recovered == [(opened.attempt.id, "worker_blocked")]
    assert world.escalated == []
    assert await world.attempts.get_open_for_task(world.task.id) is None


async def test_worker_revision_needed_escalates_to_a_human(world: World) -> None:
    opened = await world.open()

    transition = await world.round.observe(
        world.event(
            opened.attempt.id,
            EventKind.SUBMITTED,
            "submitted-1",
            status=SubmitStatus.REVISION_NEEDED,
            summary="Which rounding applies to KWD?",
        )
    )

    assert transition.outcome is RoundOutcome.APPLIED
    assert transition.phase is AttemptPhase.BLOCKED
    attempt = await world.attempts.get(opened.attempt.id)
    assert attempt is not None
    assert attempt.phase is AttemptPhase.BLOCKED
    assert attempt.submit_status is SubmitStatus.REVISION_NEEDED
    task = await world.task_view()
    assert task.status is TaskStatus.BLOCKED
    assert task.result_summary is not None
    assert "REVISION_NEEDED" in task.result_summary
    assert "Which rounding applies to KWD?" in task.result_summary
    assert world.escalated == [(world.task.id, "worker_needs_revision")]
    assert world.recovered == []
    assert list(world.attempts.attempts) == [opened.attempt.id]  # no successor


async def test_submitted_without_a_result_is_ignored(world: World) -> None:
    opened = await world.open()

    transition = await world.round.observe(
        world.event(opened.attempt.id, EventKind.SUBMITTED, "submitted-1")
    )

    assert transition.outcome is RoundOutcome.IGNORED
    assert transition.reason == "missing_result"
    assert (await world.attempts.get(opened.attempt.id)) == opened.attempt


# ---------------------------------------------------------------------------
# observe: the Leader's verdict (review.md)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [SubmitStatus.SUCCESS, SubmitStatus.SUCCESS_WITH_NOTES])
async def test_leader_accept_launches_verification_with_the_frozen_policy(
    world: World, status: SubmitStatus
) -> None:
    attempt_id = await world.submitted_candidate()
    # What the sources say now must not matter: the attempt is judged against
    # the policy it was told (``base/package.json``).
    world.policies.policy = ConstructionPolicy(
        policy=PathPolicy(allowed_paths=("docs/**",), denied_paths=()),
        test_commands=("make check",),
    )

    transition = await world.round.observe(
        world.event(
            attempt_id,
            EventKind.REVIEW_SUBMITTED,
            "review-1",
            status=status,
            summary="VERDICT: ACCEPT\nClean change, the new tests cover the rounding.",
        )
    )

    assert transition == RoundTransition(attempt_id, RoundOutcome.APPLIED, AttemptPhase.VERIFYING)
    attempt = await world.attempts.get(attempt_id)
    assert attempt is not None
    assert attempt.phase is AttemptPhase.VERIFYING
    assert attempt.verification_run_id == RUN_ID
    assert attempt.review_verdict is ReviewVerdict.ACCEPT
    assert len(world.verification.launched) == 1
    candidate, handed = world.verification.launched[0]
    assert handed.id == attempt_id
    assert candidate.attempt_id == attempt_id
    assert candidate.task_id == world.task.id
    assert candidate.repository_id == world.task.repository_id
    assert candidate.base_sha == BASE_SHA
    assert candidate.head_sha == HEAD_SHA
    assert candidate.candidate_bundle == CANDIDATE_BUNDLE
    assert json.loads(candidate.changes_json)["head_sha"] == HEAD_SHA
    assert json.loads(candidate.evidence_json)["attempt_id"] == str(attempt_id)
    control = _read_json(world.task_dir(attempt_id) / "base/package.json")
    assert candidate.policy.allowed_paths == tuple(control["allowed_paths"])
    assert candidate.policy.allowed_paths == ("src/**", "tests/**")
    assert candidate.policy.denied_paths == tuple(control["denied_paths"]) == (".git/**",)
    assert candidate.test_commands == tuple(control["test_commands"]) == TEST_COMMANDS
    assert world.escalated == []
    assert world.recovered == []
    assert (await world.task_view()).status is TaskStatus.IN_PROGRESS


async def test_leader_revision_fences_and_reopens_the_same_generation(world: World) -> None:
    attempt_id = await world.submitted_candidate()
    before = await world.reservations.get_active(world.task.id)
    assert before is not None
    notices_before = len(world.collaboration.sent)
    world.clock.advance(300)

    transition = await world.round.observe(
        world.event(
            attempt_id,
            EventKind.REVIEW_SUBMITTED,
            "review-1",
            status=SubmitStatus.REVISION_NEEDED,
            summary="VERDICT: REVISION\nAdd a test for JPY",
        )
    )

    assert transition.outcome is RoundOutcome.APPLIED
    assert transition.attempt_id == attempt_id
    assert transition.phase is AttemptPhase.NOTIFIED
    assert transition.next_attempt_id is not None
    assert transition.next_attempt_id != attempt_id
    old = await world.attempts.get(attempt_id)
    assert old is not None
    assert old.phase is AttemptPhase.FENCED
    assert old.fence_reason == "leader_revision"
    assert old.review_verdict is ReviewVerdict.REVISION
    assert old.fenced_at == world.clock.now
    new = await world.attempts.get(transition.next_attempt_id)
    assert new is not None
    assert new.is_open and new.phase is AttemptPhase.NOTIFIED
    assert new.generation == old.generation == 1
    assert new.assignment_attempt_id == old.assignment_attempt_id
    assert new.worker_agent_id == world.worker.id
    assert new.package_dir != old.package_dir
    assert new.package_dir == f"teams/{TEAM}/shared/tasks/{new.id}"
    assert await world.attempts.get_open_for_task(world.task.id) == new
    assert new.budget_until == world.clock.now + timedelta(seconds=ATTEMPT_BUDGET)

    # A fresh directory (D-8) whose spec carries the Leader's reasons.
    new_spec = (world.task_dir(new.id) / "spec.md").read_text(encoding="utf-8")
    assert "Note from the previous attempt" in new_spec
    assert "Add a test for JPY" in new_spec
    old_spec = (world.task_dir(old.id) / "spec.md").read_text(encoding="utf-8")
    assert "Add a test for JPY" not in old_spec

    # The same reservation row, lease renewed to a full budget and re-bound (D-9).
    after = await world.reservations.get_active(world.task.id)
    assert after is not None
    assert after.id == before.id == new.execution_id
    assert after.status is WorkerExecutionStatus.RUNNING
    assert _aware(after.lease_expires_at) > _aware(before.lease_expires_at)
    assert _close(after.lease_expires_at, datetime.now(UTC) + timedelta(seconds=2700))
    assert after.task_payload is not None
    assert after.task_payload["attemptId"] == str(new.id)
    assert len(await world.reservation_rows()) == 1

    assert len(world.collaboration.sent) == notices_before + 1
    command, key = world.collaboration.sent[-1]
    assert command.recipient_agent_id == world.worker.id
    assert f"shared/tasks/{new.id}/spec.md" in command.body
    assert key.startswith(f"revision:{attempt_id}:g1:")
    assert world.escalated == []
    assert world.recovered == []
    assert (await world.task_view()).status is TaskStatus.IN_PROGRESS


async def test_leader_blocked_blocks_the_task_and_escalates(world: World) -> None:
    attempt_id = await world.submitted_candidate()

    transition = await world.round.observe(
        world.event(
            attempt_id,
            EventKind.REVIEW_SUBMITTED,
            "review-1",
            status=SubmitStatus.BLOCKED,
            summary="VERDICT: BLOCKED\nThe diff deletes tests/test_quote.py",
        )
    )

    assert transition == RoundTransition(attempt_id, RoundOutcome.APPLIED, AttemptPhase.BLOCKED)
    attempt = await world.attempts.get(attempt_id)
    assert attempt is not None
    assert attempt.phase is AttemptPhase.BLOCKED
    assert attempt.review_verdict is ReviewVerdict.BLOCKED
    task = await world.task_view()
    assert task.status is TaskStatus.BLOCKED
    assert task.result_summary is not None
    assert task.result_summary.startswith("Leader review BLOCKED")
    assert "deletes tests/test_quote.py" in task.result_summary
    assert world.escalated == [(world.task.id, "leader_blocked")]
    assert world.recovered == []
    assert world.verification.launched == []


async def test_leader_status_wins_over_the_verdict_line(world: World) -> None:
    attempt_id = await world.submitted_candidate()

    transition = await world.round.observe(
        world.event(
            attempt_id,
            EventKind.REVIEW_SUBMITTED,
            "review-1",
            status=SubmitStatus.SUCCESS,
            summary="VERDICT: BLOCKED\nActually fine, submitting anyway.",
        )
    )

    assert transition.phase is AttemptPhase.VERIFYING
    attempt = await world.attempts.get(attempt_id)
    assert attempt is not None
    assert attempt.review_verdict is ReviewVerdict.ACCEPT
    assert len(world.verification.launched) == 1
    events = await world.attempts.list_events(attempt_id)
    disagreements = [event for event in events if event.kind is EventKind.REVIEW_SUBMITTED]
    assert len(disagreements) == 1
    assert disagreements[0].marker == "review-1:disagreement"
    assert disagreements[0].payload["stated_verdict"] == "BLOCKED"
    assert disagreements[0].payload["verdict"] == "ACCEPT"
    assert disagreements[0].payload["status"] == "SUCCESS"
    assert disagreements[0].applied_at == world.clock.now
    assert world.escalated == []


# ---------------------------------------------------------------------------
# fencing (D-9)
# ---------------------------------------------------------------------------


async def test_a_terminal_attempt_ignores_events_and_records_them(world: World) -> None:
    opened = await world.open()
    await world.round.expire(opened.attempt.id, reason="budget_expired")
    task_before = await world.task_view()

    transition = await world.round.observe(
        world.event(opened.attempt.id, EventKind.ACKNOWLEDGED, "ack-late")
    )

    assert transition.outcome is RoundOutcome.IGNORED
    assert transition.reason == "attempt_terminal"
    assert transition.phase is AttemptPhase.FENCED
    events = await world.attempts.list_events(opened.attempt.id)
    fenced = [event for event in events if event.kind is EventKind.FENCED]
    assert len(fenced) == 1
    assert fenced[0].marker == "acknowledged:ack-late"
    assert fenced[0].payload["event_kind"] == "acknowledged"
    assert fenced[0].payload["fence_reason"] == "budget_expired"
    assert await world.task_view() == task_before
    attempt = await world.attempts.get(opened.attempt.id)
    assert attempt is not None
    assert attempt.acknowledged_at is None


async def test_an_advanced_generation_fences_the_attempt_and_moves_nothing(world: World) -> None:
    opened = await world.open()
    task = await world.task_view()
    replacement = await world.assignments.reassign(
        world.task.id,
        expected_task_version=task.version,
        expected_generation=1,
        replacement_worker_id=world.worker2.id,
        reason=AssignmentReason.WORKER_UNREACHABLE,
    )
    assert replacement.generation == 2
    task_after_reassign = await world.task_view()
    assert task_after_reassign.status is TaskStatus.ASSIGNED

    transition = await world.round.observe(
        world.event(opened.attempt.id, EventKind.ACKNOWLEDGED, "ack-1")
    )

    assert transition.outcome is RoundOutcome.IGNORED
    assert transition.reason == "fenced_generation"
    assert transition.phase is AttemptPhase.FENCED
    attempt = await world.attempts.get(opened.attempt.id)
    assert attempt is not None
    assert attempt.phase is AttemptPhase.FENCED
    assert attempt.fence_reason == "generation_advanced"
    assert attempt.acknowledged_at is None
    events = await world.attempts.list_events(opened.attempt.id)
    assert [event.kind for event in events] == [EventKind.FENCED]
    assert events[0].marker == "acknowledged:ack-1"
    assert events[0].payload["fence_reason"] == "generation_advanced"
    assert await world.task_view() == task_after_reassign
    assert world.escalated == []
    assert world.recovered == []


async def test_open_after_expire_and_reassign_starts_the_next_generation(world: World) -> None:
    first = await world.open()
    await world.round.expire(first.attempt.id, reason="worker_unreachable")
    task = await world.task_view()
    await world.assignments.reassign(
        world.task.id,
        expected_task_version=task.version,
        expected_generation=1,
        replacement_worker_id=world.worker2.id,
        reason=AssignmentReason.WORKER_UNREACHABLE,
    )

    second = await world.open(key="open-g2")

    assert second.created is True
    assert second.attempt.id != first.attempt.id
    assert second.attempt.generation == 2
    assert second.attempt.worker_agent_id == world.worker2.id
    assignment = await world.assignments.active(world.task.id)
    assert assignment is not None
    assert second.attempt.assignment_attempt_id == assignment.id
    assert second.attempt.execution_id != first.attempt.execution_id
    old = await world.attempts.get(first.attempt.id)
    assert old is not None
    assert old.phase is AttemptPhase.FENCED
    assert old.fence_reason == "worker_unreachable"
    history = await world.attempts.list_for_task(world.task.id)
    assert [item.id for item in history] == [first.attempt.id, second.attempt.id]
    assert _read_json(world.task_dir(second.attempt.id) / "meta.json")["assigned_to"] == (
        "agt-worker-y"
    )
    assert _read_json(world.task_dir(second.attempt.id) / "base/package.json")["generation"] == 2
    reservation = await world.reservations.get_active(world.task.id)
    assert reservation is not None
    assert reservation.id == second.attempt.execution_id
    assert reservation.worker_agent_id == world.worker2.id
    assert reservation.assignment_generation == 2
    assert await world.reservation_rows() == [
        (1, "failed", "worker_unreachable"),
        (2, "running", None),
    ]
    command, _ = world.collaboration.sent[-1]
    assert command.recipient_agent_id == world.worker2.id
    assert (await world.task_view()).status is TaskStatus.IN_PROGRESS


# ---------------------------------------------------------------------------
# expire (D-12)
# ---------------------------------------------------------------------------


async def test_expire_fences_releases_the_reservation_and_hands_to_recovery(world: World) -> None:
    opened = await world.open()
    reservation = await world.reservations.get_active(world.task.id)
    assert reservation is not None
    world.clock.advance(ATTEMPT_BUDGET)

    transition = await world.round.expire(opened.attempt.id, reason="budget_expired")

    assert transition == RoundTransition(
        opened.attempt.id, RoundOutcome.APPLIED, AttemptPhase.FENCED
    )
    attempt = await world.attempts.get(opened.attempt.id)
    assert attempt is not None
    assert attempt.phase is AttemptPhase.FENCED
    assert attempt.fence_reason == "budget_expired"
    assert attempt.fenced_at == world.clock.now
    events = await world.attempts.list_events(opened.attempt.id)
    assert [event.kind for event in events] == [EventKind.EXPIRED]
    assert events[0].payload == {"reason": "budget_expired", "phase_before": "notified"}
    assert events[0].marker.startswith("budget_expired:")
    failed = await world.reservations.get(reservation.id)
    assert failed is not None
    assert failed.status is WorkerExecutionStatus.FAILED
    assert failed.error_detail == "budget_expired"
    assert await world.reservations.get_active(world.task.id) is None
    assert world.recovered == [(opened.attempt.id, "budget_expired")]
    assert world.escalated == []
    # The task is recovery's to reassign or escalate; ``expire`` never blocks it.
    assert (await world.task_view()).status is TaskStatus.IN_PROGRESS

    again = await world.round.expire(opened.attempt.id, reason="budget_expired")
    unknown = await world.round.expire(uuid4(), reason="budget_expired")

    assert again.outcome is RoundOutcome.IGNORED
    assert again.reason == "attempt_terminal"
    assert again.phase is AttemptPhase.FENCED
    assert unknown.outcome is RoundOutcome.IGNORED
    assert unknown.reason == "unknown_attempt"
    assert unknown.phase is None
    assert len(world.recovered) == 1
    assert len(await world.attempts.list_events(opened.attempt.id)) == 1


async def test_expire_while_the_leader_holds_the_review_blocks_and_escalates(
    world: World,
) -> None:
    """D-13: a review that runs out of budget is not skipped and not recovered —
    the task is blocked and a human checkpoint opens; ``ACCEPT`` is the only
    door into verification."""

    attempt_id = await world.submitted_candidate()
    reservation = await world.reservations.get(
        (await world.attempts.get(attempt_id)).execution_id  # type: ignore[union-attr]
    )
    assert reservation is not None
    world.clock.advance(REVIEW_BUDGET)

    transition = await world.round.expire(attempt_id, reason="review_budget_expired")

    assert transition == RoundTransition(attempt_id, RoundOutcome.APPLIED, AttemptPhase.FENCED)
    attempt = await world.attempts.get(attempt_id)
    assert attempt is not None
    assert attempt.phase is AttemptPhase.FENCED
    assert attempt.fence_reason == "review_budget_expired"
    events = await world.attempts.list_events(attempt_id)
    assert [event.kind for event in events] == [EventKind.EXPIRED]
    assert events[0].payload["phase_before"] == "review_pending"
    released = await world.reservations.get(reservation.id)
    assert released is not None
    assert released.status is WorkerExecutionStatus.FAILED
    task = await world.task_view()
    assert task.status is TaskStatus.BLOCKED
    assert task.result_summary is not None
    assert "review_budget_expired" in task.result_summary
    assert world.escalated == [(world.task.id, "review_budget_expired")]
    assert world.recovered == []


async def test_observe_for_an_unknown_attempt_is_ignored(world: World) -> None:
    await world.open()
    stranger = uuid4()

    transition = await world.round.observe(world.event(stranger, EventKind.ACKNOWLEDGED, "ack-1"))

    assert transition == RoundTransition(
        stranger, RoundOutcome.IGNORED, None, reason="unknown_attempt"
    )
    assert (await world.task_view()).status is TaskStatus.IN_PROGRESS
    assert world.attempts.events == {}


# ---------------------------------------------------------------------------
# messages: the room prose on its own
# ---------------------------------------------------------------------------


def test_construction_notice_gives_the_helper_lines_and_mentions_nobody() -> None:
    attempt_id = uuid4()
    package_dir = f"teams/{TEAM}/shared/tasks/{attempt_id}"

    body = messages.construction_notice(
        attempt_id=attempt_id, package_dir=package_dir, title="Fix pricing", budget_seconds=2700
    )

    assert body.startswith("Task package ready: Fix pricing\n")
    assert f"Task directory: shared/tasks/{attempt_id}/ (object prefix {package_dir})" in body
    assert f'taskflow(action="ack_task", payload={{"taskId": "{attempt_id}"}})' in body
    assert f"Read shared/tasks/{attempt_id}/spec.md" in body
    indented = [line.strip() for line in body.splitlines() if line.startswith("   bash ")]
    assert indented == list(HELPER_COMMANDS[:3])
    assert 'taskflow(action="submit_task", ...)' in body
    assert "Budget: 45 minutes" in body
    assert "@admin" in body
    assert "Never @mention the Team Leader" in body
    for forbidden in ("MCP", "start_assigned_task", "repomesh-task-control", "clean"):
        assert forbidden not in body


def test_review_notice_names_the_review_directory_and_the_status_mapping() -> None:
    review_id = uuid4()
    attempt_id = uuid4()
    package_dir = f"teams/{TEAM}/shared/tasks/{review_id}"

    body = messages.review_notice(
        review_id=review_id,
        attempt_id=attempt_id,
        package_dir=package_dir,
        head_sha=HEAD_SHA,
        title="Fix pricing",
        budget_seconds=900,
    )

    assert body.startswith(f'Review requested: candidate {HEAD_SHA[:8]} for "Fix pricing"\n')
    assert f"Review task directory: shared/tasks/{review_id}/ (object prefix {package_dir})" in body
    assert f"It reviews construction attempt {attempt_id}." in body
    assert f'taskflow(action="ack_task", payload={{"taskId": "{review_id}"}})' in body
    assert f"Read shared/tasks/{review_id}/spec.md" in body
    assert "VERDICT: ACCEPT, VERDICT: REVISION or VERDICT: BLOCKED" in body
    mapping = (
        "SUCCESS or SUCCESS_WITH_NOTES = ACCEPT, REVISION_NEEDED = REVISION, BLOCKED = BLOCKED"
    )
    assert mapping in body
    assert "Do not @mention the Worker" in body
    assert "Budget: 15 minutes" in body
    assert "bash base/tools" not in body  # the Leader does not build


def test_revision_instruction_appends_the_leader_note_under_its_heading() -> None:
    text = messages.revision_instruction("Do the thing.\n\n", "  Add a test for JPY\nAnd KWD.  ")

    assert text == (
        "Do the thing.\n\n## Note from the previous attempt (Leader review)\n\n"
        "Add a test for JPY\nAnd KWD."
    )
    assert text.count(messages.REVISION_NOTE_HEADING) == 1
    assert messages.REVISION_NOTE_HEADING.startswith("## ")
