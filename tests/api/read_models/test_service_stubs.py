"""Service-level branches not reachable through the public write paths.

MANUAL_INTERVENTION recovery actions are only produced by external recovery
flows, so escalated_to_human and the archived/failed list branches are driven
through stub sources implementing the read-model source protocols.
"""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from repomesh.api.read_models import DeliveryReadModelService
from repomesh.api.read_models.sources import (
    PlanSnapshotData,
    RepositoryProfileData,
    RunnerEventData,
)
from repomesh.modules.collaboration.contracts import (
    CollaborationDeliveryStatus,
    CollaborationMessageKind,
    CollaborationMessageView,
    RoomTimelineEntryView,
)
from repomesh.modules.delivery.contracts import (
    MERGE_GATE_GOVERNANCE_MISSING_REASON,
    CandidateRevisionView,
    ChangeSetStatus,
    ChangeSetView,
    DeliveryArchiveView,
    MergeGateDecision,
    RecoveryActionKind,
    RecoveryActionStatus,
    RecoveryActionView,
    RecoveryPlanView,
    RecoveryTrigger,
    RepositoryDeliveryStatus,
    RepositoryDeliveryView,
    SCMObservationSource,
    SCMObservationStatus,
    SCMObservationView,
)
from repomesh.modules.task_orchestration.contracts import (
    DeliveryRefusalView,
    ExecutionPlanStatus,
    ExecutionPlanView,
    PlannedRepositoryTaskView,
    TaskEvidenceView,
    TaskOrigin,
    TaskStatus,
    TaskTestResultView,
    TaskView,
)

NOW = datetime.now(UTC)


class StubPlans:
    def __init__(self, *plans: ExecutionPlanView) -> None:
        self.plans = {plan.id: plan for plan in plans}

    async def list_all(self):
        return tuple(self.plans.values())

    async def get(self, plan_id: UUID):
        return self.plans.get(plan_id)


class StubSnapshots:
    def __init__(self, *snapshots: PlanSnapshotData) -> None:
        self.snapshots = tuple(snapshots)

    async def project_ids(self):
        return tuple({snapshot.project_id for snapshot in self.snapshots})

    async def for_project(self, project_id: UUID):
        return tuple(s for s in self.snapshots if s.project_id == project_id)


class StubTasks:
    def __init__(self, *tasks: TaskView) -> None:
        self.tasks = tuple(tasks)

    async def list_by_project(self, project_id: UUID):
        return tuple(t for t in self.tasks if t.project_id == project_id)

    async def list_all(self):
        return self.tasks


class StubChangeSets:
    def __init__(
        self,
        mapping: dict[UUID, ChangeSetView],
        gate_reasons: tuple[str, ...] = ("blocked",),
    ) -> None:
        self.mapping = mapping
        self.gate_reasons = gate_reasons

    async def for_delivery(self, delivery_id: UUID):
        return self.mapping.get(delivery_id)

    async def merge_gate(self, change_set_id: UUID, repository_id: UUID):
        return MergeGateDecision(
            change_set_id, repository_id, not self.gate_reasons, tuple(self.gate_reasons)
        )


class StubArchives:
    def __init__(self, *archived: UUID) -> None:
        self.archived = set(archived)

    async def get(self, delivery_id: UUID):
        if delivery_id in self.archived:
            return DeliveryArchiveView(delivery_id=delivery_id, archived_at=NOW)
        return None


class _Empty:
    async def for_project(self, project_id: UUID):
        return ()

    async def engineering_contract(self, project_id: UUID):
        return None

    async def list(self):
        return ()

    async def name(self, agent_id: UUID):
        return "worker-01"

    async def organization_id(self, agent_id: UUID):
        return None

    async def matrix_room_id(self, project_id: UUID):
        return None

    async def get_view(self, project_id: UUID):
        return None

    async def find_by_room(self, room_id: str):
        return None

    async def for_room(self, room_id: str):
        return ()

    async def last_assignment_at(self, project_id: UUID):
        return {}

    async def repository_spec(self, project_id: UUID, repository_id: UUID):
        return None

    async def profiles(self):
        return ()

    async def list_all(self):
        return ()

    async def list_views(self):
        return ()

    async def for_change_set(self, change_set_id: UUID):
        return ()


def _service(
    plans,
    snapshots,
    tasks,
    change_sets,
    archives,
    repositories=None,
    runner_events=None,
    messages=None,
    observations=None,
    topology=None,
    agents=None,
    specifications=None,
    runtime=None,
    probe_concurrency=None,
    room_timeline=None,
):
    empty = _Empty()
    return DeliveryReadModelService(
        plans=plans,
        snapshots=snapshots,
        tasks=tasks,
        change_sets=change_sets,
        archives=archives,
        validations=empty,
        specifications=specifications if specifications is not None else empty,
        repositories=repositories if repositories is not None else empty,
        agents=agents if agents is not None else empty,
        topology=topology if topology is not None else empty,
        runner_events=runner_events if runner_events is not None else empty,
        messages=messages if messages is not None else empty,
        observations=observations if observations is not None else empty,
        room_timeline=room_timeline,
        runtime=runtime,
        probe_concurrency=probe_concurrency,
    )


def _plan(project_id: UUID, repository_id: UUID, leader_task_id: UUID, status):
    return ExecutionPlanView(
        id=uuid4(),
        organization_id=uuid4(),
        project_id=project_id,
        created_by_agent_id=uuid4(),
        status=status,
        current_batch_index=0,
        batches=(
            (
                PlannedRepositoryTaskView(
                    repository_id=repository_id,
                    title="Deliver repo",
                    instruction="Do it.",
                    acceptance=("Tests pass",),
                    leader_task_id=leader_task_id,
                ),
            ),
        ),
    )


def _worker(project_id: UUID, repository_id: UUID, leader_task_id: UUID) -> TaskView:
    return TaskView(
        id=uuid4(),
        organization_id=uuid4(),
        project_id=project_id,
        repository_id=repository_id,
        parent_task_id=leader_task_id,
        assigned_by_agent_id=uuid4(),
        assignee_agent_id=uuid4(),
        title="Implement scope",
        instruction="Implement.",
        acceptance=("Tests pass",),
        status=TaskStatus.BLOCKED,
        result_summary=None,
        version=1,
    )


def _manual_intervention_change_set(
    delivery_plan: ExecutionPlanView, repository_id: UUID, task_id: UUID
) -> ChangeSetView:
    return ChangeSetView(
        id=uuid4(),
        organization_id=delivery_plan.organization_id,
        project_id=delivery_plan.project_id,
        created_by_agent_id=uuid4(),
        title="stuck delivery",
        validation_snapshot_id=None,
        status=ChangeSetStatus.COMPENSATING,
        version=3,
        merge_cursor=0,
        repositories=(
            RepositoryDeliveryView(
                repository_id=repository_id,
                task_id=task_id,
                commit_sha="a" * 40,
                base_sha="b" * 40,
                branch_name="repomesh/stuck",
                depends_on=(),
                merge_order=0,
                status=RepositoryDeliveryStatus.MANUAL_INTERVENTION,
                pull_request_number=None,
                pull_request_url=None,
                ci_check_run_id=None,
                ci_summary=None,
                merge_sha=None,
                required_checks=(),
                ci_checks=(),
                required_approvals=0,
                reviews=(),
            ),
        ),
        recovery_plans=(
            RecoveryPlanView(
                id=uuid4(),
                trigger=RecoveryTrigger.PR_CONFLICT,
                reason="revert conflict",
                created_at=NOW,
                actions=(
                    RecoveryActionView(
                        id=uuid4(),
                        sequence=1,
                        kind=RecoveryActionKind.MANUAL_INTERVENTION,
                        status=RecoveryActionStatus.WAITING_WORKER,
                        repository_id=None,
                        run_id=None,
                        detail="Operator must resolve the revert conflict.",
                    ),
                ),
            ),
        ),
        governance_decisions=(),
        candidate_revisions=(),
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.asyncio
async def test_unfinished_manual_intervention_escalates_to_human() -> None:
    project_id = uuid4()
    repository_id = uuid4()
    leader_task_id = uuid4()
    plan = _plan(project_id, repository_id, leader_task_id, ExecutionPlanStatus.COMPLETED)
    worker = _worker(project_id, repository_id, leader_task_id)
    change_set = _manual_intervention_change_set(plan, repository_id, worker.id)
    service = _service(
        StubPlans(plan),
        StubSnapshots(),
        StubTasks(worker),
        StubChangeSets({plan.id: change_set}),
        StubArchives(),
        repositories=None,
    )

    detail = await service.get_delivery(plan.id)

    task = detail["tasks"][0]
    assert task["escalated_to_human"] is True
    assert task["display_status"] == "blocked"  # blocked stays its own display state
    repository = detail["change_set"]["repositories"][0]
    assert repository["gate_display"] == "blocked"
    assert repository["pull_request_number"] is None
    assert repository["head_sha"] == "a" * 40


@pytest.mark.asyncio
async def test_list_filters_archived_and_reports_failed_phase() -> None:
    project_id = uuid4()
    repository_id = uuid4()
    archived_plan = _plan(project_id, repository_id, uuid4(), ExecutionPlanStatus.COMPLETED)
    failed_plan = _plan(project_id, repository_id, uuid4(), ExecutionPlanStatus.FAILED)
    service = _service(
        StubPlans(archived_plan, failed_plan),
        StubSnapshots(),
        StubTasks(),
        StubChangeSets({}),
        StubArchives(archived_plan.id),
    )

    default_listing = await service.list_deliveries()
    phases = {
        item["delivery_id"]: item["phase"] for item in default_listing["projects"][0]["deliveries"]
    }
    assert phases == {failed_plan.id: "failed"}

    full_listing = await service.list_deliveries(include_archived=True)
    phases = {
        item["delivery_id"]: item["phase"] for item in full_listing["projects"][0]["deliveries"]
    }
    assert phases == {failed_plan.id: "failed", archived_plan.id: "archived"}


def _repository_view(status: RepositoryDeliveryStatus, repository_id: UUID, task_id: UUID):
    return RepositoryDeliveryView(
        repository_id=repository_id,
        task_id=task_id,
        commit_sha="a" * 40,
        base_sha="b" * 40,
        branch_name="repomesh/gate",
        depends_on=(),
        merge_order=0,
        status=status,
        pull_request_number=None,
        pull_request_url=None,
        ci_check_run_id=None,
        ci_summary=None,
        merge_sha=None,
        required_checks=(),
        ci_checks=(),
        required_approvals=0,
        reviews=(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expect_gate"),
    [
        (RepositoryDeliveryStatus.PENDING, True),
        (RepositoryDeliveryStatus.PR_OPEN, True),
        (RepositoryDeliveryStatus.CI_PENDING, True),
        (RepositoryDeliveryStatus.CI_FAILED, True),
        (RepositoryDeliveryStatus.REVIEW_PENDING, True),
        (RepositoryDeliveryStatus.REVIEW_CHANGES_REQUESTED, True),
        (RepositoryDeliveryStatus.READY_TO_MERGE, True),
        (RepositoryDeliveryStatus.MANUAL_INTERVENTION, True),
        (RepositoryDeliveryStatus.MERGE_REQUESTED, False),
        (RepositoryDeliveryStatus.MERGED, False),
        (RepositoryDeliveryStatus.COMPENSATION_PENDING, False),
        (RepositoryDeliveryStatus.COMPENSATED, False),
    ],
)
async def test_merge_gate_is_null_once_the_question_is_moot(status, expect_gate) -> None:
    """Contract 889464e: terminal-side states answer merge_gate with null."""

    project_id = uuid4()
    repository_id = uuid4()
    leader_task_id = uuid4()
    plan = _plan(project_id, repository_id, leader_task_id, ExecutionPlanStatus.COMPLETED)
    worker = _worker(project_id, repository_id, leader_task_id)
    change_set = _manual_intervention_change_set(plan, repository_id, worker.id)
    change_set = ChangeSetView(
        **{
            **{f: getattr(change_set, f) for f in change_set.__dataclass_fields__},
            "repositories": (_repository_view(status, repository_id, worker.id),),
            "recovery_plans": (),
        }
    )
    service = _service(
        StubPlans(plan),
        StubSnapshots(),
        StubTasks(worker),
        StubChangeSets({plan.id: change_set}),
        StubArchives(),
    )

    detail = await service.attach_merge_gates(await service.get_delivery(plan.id))

    gate = detail["change_set"]["repositories"][0]["merge_gate"]
    if expect_gate:
        assert gate == {"allowed": False, "reasons": ["blocked"]}
    else:
        assert gate is None


@pytest.mark.asyncio
async def test_repair_timeline_at_is_always_a_timestamp() -> None:
    """Contract §3: `at` is a string; rework entries fall back to the candidate
    revision the rework produced, else the change set's persisted timestamp."""

    project_id = uuid4()
    repository_id = uuid4()
    leader_task_id = uuid4()
    plan = _plan(project_id, repository_id, leader_task_id, ExecutionPlanStatus.IN_PROGRESS)
    worker = _worker(project_id, repository_id, leader_task_id)
    revised_rework = replace(
        worker, id=uuid4(), origin=TaskOrigin.REWORK, status=TaskStatus.SUCCEEDED
    )
    running_rework = replace(
        worker, id=uuid4(), origin=TaskOrigin.REWORK, status=TaskStatus.IN_PROGRESS
    )
    revision_at = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    change_set = replace(
        _manual_intervention_change_set(plan, repository_id, worker.id),
        recovery_plans=(),
        candidate_revisions=(
            CandidateRevisionView(
                id=uuid4(),
                repository_id=repository_id,
                task_id=revised_rework.id,
                sequence=1,
                head_sha="c" * 40,
                previous_head_sha="a" * 40,
                reason="candidate rework",
                created_at=revision_at,
            ),
        ),
    )
    service = _service(
        StubPlans(plan),
        StubSnapshots(),
        StubTasks(worker, revised_rework, running_rework),
        StubChangeSets({plan.id: change_set}),
        StubArchives(),
    )

    detail = await service.get_delivery(plan.id)

    timelines = {item["task_id"]: item["repair_timeline"] for item in detail["tasks"]}
    worker_entries = {entry["what"]: entry["at"] for entry in timelines[worker.id]}
    assert worker_entries == {
        "返工任务 succeeded": revision_at,
        "返工任务 in_progress": change_set.updated_at,
    }
    assert all(entry["at"] is not None for timeline in timelines.values() for entry in timeline)


@pytest.mark.asyncio
async def test_decisions_approve_only_when_governance_is_the_sole_blocker() -> None:
    """Contract §4.3 (5a60148): approve derives from 'no blocking reason other
    than the missing head-bound READY decision', via the contracts constant."""

    project_id = uuid4()
    repository_id = uuid4()
    leader_task_id = uuid4()
    plan = _plan(project_id, repository_id, leader_task_id, ExecutionPlanStatus.COMPLETED)
    worker = _worker(project_id, repository_id, leader_task_id)
    change_set = replace(
        _manual_intervention_change_set(plan, repository_id, worker.id),
        status=ChangeSetStatus.DELIVERING,
        recovery_plans=(),
        repositories=(
            _repository_view(RepositoryDeliveryStatus.READY_TO_MERGE, repository_id, worker.id),
        ),
    )

    async def decisions(gate_reasons: tuple[str, ...]):
        service = _service(
            StubPlans(plan),
            StubSnapshots(),
            StubTasks(worker),
            StubChangeSets({plan.id: change_set}, gate_reasons=gate_reasons),
            StubArchives(),
        )
        return await service.list_decisions(plan.id)

    sole = await decisions((MERGE_GATE_GOVERNANCE_MISSING_REASON,))
    assert [item["kind"] for item in sole["items"]] == ["approve"]
    item = sole["items"][0]
    assert item["repository_id"] == repository_id
    assert item["head_sha"] == "a" * 40
    assert item["created_at"] == change_set.updated_at
    assert item["actions"] == ["approve_merge", "view_evidence"]

    mixed = await decisions(
        ("required CI checks have not passed", MERGE_GATE_GOVERNANCE_MISSING_REASON)
    )
    assert mixed["items"] == []


@pytest.mark.asyncio
async def test_decisions_watch_covers_active_rework_and_missing_delivery() -> None:
    project_id = uuid4()
    repository_id = uuid4()
    leader_task_id = uuid4()
    plan = _plan(project_id, repository_id, leader_task_id, ExecutionPlanStatus.IN_PROGRESS)
    worker = _worker(project_id, repository_id, leader_task_id)
    rework = replace(worker, id=uuid4(), origin=TaskOrigin.REWORK, status=TaskStatus.IN_PROGRESS)
    change_set = replace(
        _manual_intervention_change_set(plan, repository_id, worker.id),
        recovery_plans=(),
        repositories=(
            _repository_view(RepositoryDeliveryStatus.CI_FAILED, repository_id, worker.id),
        ),
    )
    service = _service(
        StubPlans(plan),
        StubSnapshots(),
        StubTasks(worker, rework),
        StubChangeSets({plan.id: change_set}),
        StubArchives(),
    )

    payload = await service.list_decisions(plan.id)

    assert [item["kind"] for item in payload["items"]] == ["watch"]
    assert payload["items"][0]["repository_id"] == repository_id
    assert payload["items"][0]["actions"] == ["view_evidence"]

    assert await service.list_decisions(uuid4()) is None


class StubRunnerEvents:
    def __init__(self, *events: RunnerEventData) -> None:
        self.events = events

    async def for_project(self, project_id: UUID):
        return self.events


class StubMessages:
    def __init__(self, *messages: CollaborationMessageView) -> None:
        self.messages = messages

    async def for_project(self, project_id: UUID):
        return self.messages

    async def for_room(self, room_id: str):
        return tuple(item for item in self.messages if item.room_id == room_id)

    async def last_assignment_at(self, project_id: UUID):
        latest: dict[UUID, object] = {}
        for item in self.messages:
            if item.task_id is None:
                continue
            seen = latest.get(item.task_id)
            if seen is None or item.created_at > seen:
                latest[item.task_id] = item.created_at
        return latest


class StubRoomTimeline:
    """What ``collaboration`` recorded from the room itself (v0.2 §5.2 + PR 9).

    Sorted here the way the real read use case sorts, so a test that feeds
    entries out of order is testing the merge rather than the stub.
    """

    def __init__(self, *entries: RoomTimelineEntryView) -> None:
        self.entries = entries

    async def for_room(self, room_id: str):
        return tuple(
            sorted(
                (entry for entry in self.entries if entry.room_id == room_id),
                key=lambda entry: (entry.occurred_at, entry.event_id),
            )
        )


class StubObservations:
    def __init__(self, *observations: SCMObservationView) -> None:
        self.observations = observations

    async def for_change_set(self, change_set_id: UUID):
        return self.observations


@pytest.mark.asyncio
async def test_events_merges_four_sources_with_stable_cursor() -> None:
    """Contract §4.1: runner/matrix/gate/plan in one chronological timeline;
    the offset cursor resumes without reordering and foreign tasks are cut."""

    project_id = uuid4()
    repository_id = uuid4()
    leader_task_id = uuid4()
    plan = _plan(project_id, repository_id, leader_task_id, ExecutionPlanStatus.COMPLETED)
    worker = _worker(project_id, repository_id, leader_task_id)
    change_set = _manual_intervention_change_set(plan, repository_id, worker.id)
    t0 = datetime(2026, 8, 11, 10, 0, tzinfo=UTC)

    def runner_event(minute: int, task_id: UUID, event_type: str) -> RunnerEventData:
        return RunnerEventData(
            event_id=uuid4(),
            run_id=uuid4(),
            sequence=1,
            event_type=event_type,
            occurred_at=t0.replace(minute=minute),
            task_id=task_id,
            repository_id=repository_id,
        )

    message = CollaborationMessageView(
        id=uuid4(),
        organization_id=plan.organization_id,
        project_id=project_id,
        repository_id=repository_id,
        task_id=worker.id,
        sender_agent_id=uuid4(),
        recipient_agent_id=worker.assignee_agent_id,
        kind=CollaborationMessageKind.TASK_ASSIGNMENT,
        subject="任务指派",
        body="body",
        room_id="!room:local",
        status=CollaborationDeliveryStatus.DELIVERED,
        event_id="$evt",
        correlation_id=uuid4(),
        created_at=t0.replace(minute=5),
    )
    observation = SCMObservationView(
        id=uuid4(),
        provider="github",
        source=SCMObservationSource.WEBHOOK,
        external_id="obs-1",
        event_type="check_run.completed",
        payload={},
        payload_hash="0" * 64,
        status=SCMObservationStatus.PROCESSED,
        change_set_id=change_set.id,
        repository_id=repository_id,
        attempts=1,
        version=1,
        last_error=None,
        observed_at=t0.replace(minute=20),
        received_at=t0.replace(minute=20),
        claimed_at=None,
        processed_at=None,
    )
    snapshot = PlanSnapshotData(
        id=uuid4(),
        project_id=project_id,
        plan_version=1,
        created_at=t0,
        engineering_spec="spec",
        requirement_text="req",
        execution_batches=(("repo",),),
        task_dag=(),
        execution_plan_id=plan.id,
    )
    service = _service(
        StubPlans(plan),
        StubSnapshots(snapshot),
        StubTasks(worker),
        StubChangeSets({plan.id: change_set}),
        StubArchives(),
        runner_events=StubRunnerEvents(
            runner_event(10, worker.id, "runner.started"),
            runner_event(15, uuid4(), "runner.started"),  # foreign task: excluded
        ),
        messages=StubMessages(message),
        observations=StubObservations(observation),
    )

    full = await service.list_events(plan.id)
    assert [item["kind"] for item in full["items"]] == ["plan", "matrix", "runner", "gate"]
    assert full["next_cursor"] is None
    assert all(isinstance(item["at"], datetime) for item in full["items"])

    page_one = await service.list_events(plan.id, limit=2)
    page_two = await service.list_events(plan.id, offset=2, limit=2)
    assert page_one["next_cursor"] == "2"
    assert page_two["next_cursor"] is None
    assert [i["payload_ref"] for i in page_one["items"] + page_two["items"]] == [
        item["payload_ref"] for item in full["items"]
    ]

    gate_only = await service.list_events(plan.id, kind="gate")
    assert [item["kind"] for item in gate_only["items"]] == ["gate"]
    assert await service.list_events(uuid4()) is None


@pytest.mark.asyncio
async def test_messages_projects_direction_and_scopes_to_delivery() -> None:
    """Contract §4.2: direct projection; the Leader→Worker-only limitation is
    discernible via the direction field."""

    project_id = uuid4()
    repository_id = uuid4()
    leader_task_id = uuid4()
    plan = _plan(project_id, repository_id, leader_task_id, ExecutionPlanStatus.COMPLETED)
    worker = _worker(project_id, repository_id, leader_task_id)

    def message(task_id: UUID | None) -> CollaborationMessageView:
        return CollaborationMessageView(
            id=uuid4(),
            organization_id=plan.organization_id,
            project_id=project_id,
            repository_id=repository_id,
            task_id=task_id,
            sender_agent_id=uuid4(),
            recipient_agent_id=worker.assignee_agent_id,
            kind=CollaborationMessageKind.TASK_ASSIGNMENT,
            subject="任务指派",
            body="body",
            room_id="!room:local",
            status=CollaborationDeliveryStatus.DELIVERED,
            event_id=None,
            correlation_id=uuid4(),
            created_at=datetime(2026, 8, 11, 10, 0, tzinfo=UTC),
        )

    service = _service(
        StubPlans(plan),
        StubSnapshots(),
        StubTasks(worker),
        StubChangeSets({}),
        StubArchives(),
        messages=StubMessages(message(worker.id), message(uuid4())),
    )

    payload = await service.list_messages(plan.id)

    assert len(payload["items"]) == 1  # foreign-task message excluded
    item = payload["items"][0]
    assert item["direction"] == "leader_to_worker"
    assert item["status"] == "delivered"
    assert item["created_at"] is not None
    assert await service.list_messages(uuid4()) is None


@pytest.mark.asyncio
async def test_diffs_read_the_declared_evidence_not_the_free_text_summary() -> None:
    """§3 diffs[] comes from TaskView.evidence, which the producer declares.

    This projection used to json.loads(result_summary) — a column contracted as
    free text. The task below carries human prose there and its evidence in the
    declared view, which is exactly the case the old parse got wrong: it would
    hit JSONDecodeError and silently report no Runner evidence at all.
    """

    project_id = uuid4()
    repository_id = uuid4()
    leader_task_id = uuid4()
    run_id = uuid4()
    plan = _plan(project_id, repository_id, leader_task_id, ExecutionPlanStatus.COMPLETED)
    worker = replace(
        _worker(project_id, repository_id, leader_task_id),
        status=TaskStatus.SUCCEEDED,
        result_summary="Implemented pricing; 2 files changed, all tests pass.",
        evidence=TaskEvidenceView(
            commit_sha="a" * 40,
            run_id=run_id,
            changed_files=("src/pricing.py", "tests/test_pricing.py"),
            base_sha="9" * 40,
        ),
    )
    service = _service(
        StubPlans(plan),
        StubSnapshots(),
        StubTasks(worker),
        StubChangeSets({plan.id: _manual_intervention_change_set(plan, repository_id, worker.id)}),
        StubArchives(),
    )

    detail = await service.get_delivery(plan.id)

    assert detail["diffs"] == [
        {
            "repository_id": repository_id,
            "run_id": run_id,
            "commit_sha": "a" * 40,
            "changed_files": ["src/pricing.py", "tests/test_pricing.py"],
            "diffstat": None,
        }
    ]


@pytest.mark.asyncio
async def test_a_task_without_declared_evidence_contributes_no_diff() -> None:
    """No evidence is reported as no evidence, not rescued by an exception."""

    project_id = uuid4()
    repository_id = uuid4()
    leader_task_id = uuid4()
    plan = _plan(project_id, repository_id, leader_task_id, ExecutionPlanStatus.COMPLETED)
    worker = replace(
        _worker(project_id, repository_id, leader_task_id),
        status=TaskStatus.SUCCEEDED,
        result_summary="SUPERSEDED: replaced by plan v2",
        evidence=None,
    )
    service = _service(
        StubPlans(plan),
        StubSnapshots(),
        StubTasks(worker),
        StubChangeSets({plan.id: _manual_intervention_change_set(plan, repository_id, worker.id)}),
        StubArchives(),
    )

    assert (await service.get_delivery(plan.id))["diffs"] == []


@pytest.mark.asyncio
async def test_a_task_reports_when_it_was_last_dispatched() -> None:
    """§8.7.4: the honest context the console's re-dispatch entry sits next to.

    The *latest* assignment message wins, which is what makes the field useful
    after a re-dispatch: pressing the button moves this timestamp, and that
    movement is the only feedback the console can give that is a fact rather
    than a guess about whether the Worker woke up. A task nobody ever told
    reports ``None`` — itself worth seeing, because it means the dispatch never
    happened at all.
    """

    project_id = uuid4()
    repository_id = uuid4()
    leader_task_id = uuid4()
    plan = _plan(project_id, repository_id, leader_task_id, ExecutionPlanStatus.COMPLETED)
    worker = _worker(project_id, repository_id, leader_task_id)

    def message(task_id: UUID | None, at: datetime) -> CollaborationMessageView:
        return CollaborationMessageView(
            id=uuid4(),
            organization_id=plan.organization_id,
            project_id=project_id,
            repository_id=repository_id,
            task_id=task_id,
            sender_agent_id=uuid4(),
            recipient_agent_id=worker.assignee_agent_id,
            kind=CollaborationMessageKind.TASK_ASSIGNMENT,
            subject="任务指派",
            body="body",
            room_id="!room:local",
            status=CollaborationDeliveryStatus.DELIVERED,
            event_id=None,
            correlation_id=uuid4(),
            created_at=at,
        )

    first = datetime(2026, 8, 12, 10, 0, tzinfo=UTC)
    redispatched = datetime(2026, 8, 12, 14, 30, tzinfo=UTC)
    service = _service(
        StubPlans(plan),
        StubSnapshots(),
        StubTasks(worker),
        StubChangeSets({}),
        StubArchives(),
        messages=StubMessages(message(worker.id, first), message(worker.id, redispatched)),
    )

    detail = await service.get_delivery(plan.id)

    assert detail["tasks"][0]["last_dispatched_at"] == redispatched

    silent = _service(
        StubPlans(plan),
        StubSnapshots(),
        StubTasks(worker),
        StubChangeSets({}),
        StubArchives(),
    )
    assert (await silent.get_delivery(plan.id))["tasks"][0]["last_dispatched_at"] is None


# --------------------------------------------------------------- A-18 -------

UNVERIFIED_SUMMARY = (
    "Implementation is complete. I could not execute anything to verify it — see below.\n"
    "\n"
    "1. **Nothing was executed.** The sandbox refused every `python` invocation. "
    "Please re-run before merging."
)
"""Abridged from the live A-18 row (task 6ba476ab / run d261dbb4), verbatim at
both ends. The two sentences kept are the ones the GUI never showed."""


@pytest.mark.asyncio
async def test_tasks_carry_the_agents_verification_claim() -> None:
    """§3 tasks[].evidence: the reason A-18 was invisible, made visible.

    Everything here was already in ``result_summary`` — as a JSON string the
    GUI received and never opened. The task is a genuine ``succeeded`` run and
    stays one; what changes is that the run's own report of having executed
    nothing now has a field to live in.
    """

    project_id = uuid4()
    repository_id = uuid4()
    leader_task_id = uuid4()
    plan = _plan(project_id, repository_id, leader_task_id, ExecutionPlanStatus.COMPLETED)
    worker = replace(
        _worker(project_id, repository_id, leader_task_id),
        status=TaskStatus.SUCCEEDED,
        result_summary="{...}",
        evidence=TaskEvidenceView(
            commit_sha="5" * 40,
            run_id=uuid4(),
            changed_files=("src/checkout/tax_calculator.py",),
            base_sha="e" * 40,
            summary_text=UNVERIFIED_SUMMARY,
            test_results=(),
            test_command=None,
            artifact_count=0,
        ),
    )
    service = _service(
        StubPlans(plan),
        StubSnapshots(),
        StubTasks(worker),
        StubChangeSets({plan.id: _manual_intervention_change_set(plan, repository_id, worker.id)}),
        StubArchives(),
    )

    task = (await service.get_delivery(plan.id))["tasks"][0]

    # The run did succeed and still says so — the display status is not the lie.
    assert task["display_status"] == "succeeded"
    assert task["evidence"]["verified"] is False
    assert task["evidence"]["test_results"] == []
    assert task["evidence"]["test_command"] is None
    assert task["evidence"]["artifact_count"] == 0
    # Verbatim: the projection transcribes, it does not summarise.
    assert task["evidence"]["summary_text"] == UNVERIFIED_SUMMARY
    # No blocker list was declared, so none is reported — the words are in the
    # summary and the GUI shows them from there.
    assert task["evidence"]["blockers"] == []


@pytest.mark.asyncio
async def test_a_verified_task_says_what_it_ran() -> None:
    project_id = uuid4()
    repository_id = uuid4()
    leader_task_id = uuid4()
    plan = _plan(project_id, repository_id, leader_task_id, ExecutionPlanStatus.COMPLETED)
    worker = replace(
        _worker(project_id, repository_id, leader_task_id),
        status=TaskStatus.SUCCEEDED,
        result_summary="{...}",
        evidence=TaskEvidenceView(
            commit_sha="a" * 40,
            run_id=uuid4(),
            changed_files=("src/pricing.py",),
            base_sha="9" * 40,
            summary_text="Implemented pricing; the suite is green.",
            test_command="python scripts/run_tests.py",
            test_results=(TaskTestResultView(command="python scripts/run_tests.py", exit_code=0),),
            artifact_count=1,
        ),
    )
    service = _service(
        StubPlans(plan),
        StubSnapshots(),
        StubTasks(worker),
        StubChangeSets({plan.id: _manual_intervention_change_set(plan, repository_id, worker.id)}),
        StubArchives(),
    )

    task = (await service.get_delivery(plan.id))["tasks"][0]

    assert task["evidence"]["verified"] is True
    assert task["evidence"]["test_results"] == [
        {"command": "python scripts/run_tests.py", "exit_code": 0, "summary": ""}
    ]
    assert task["evidence"]["artifact_count"] == 1


@pytest.mark.asyncio
async def test_a_task_without_declared_evidence_reports_null_not_unverified() -> None:
    """"We do not know" and "it did not verify" are different claims.

    A superseded or pre-Runner task has no evidence at all. Flattening that to
    ``verified: false`` would put an accusing marker on rows nobody ever made a
    claim about, which is the mirror image of the defect.
    """

    project_id = uuid4()
    repository_id = uuid4()
    leader_task_id = uuid4()
    plan = _plan(project_id, repository_id, leader_task_id, ExecutionPlanStatus.COMPLETED)
    worker = replace(
        _worker(project_id, repository_id, leader_task_id),
        status=TaskStatus.SUCCEEDED,
        result_summary="Implemented pricing and all tests pass.",
        evidence=None,
    )
    service = _service(
        StubPlans(plan),
        StubSnapshots(),
        StubTasks(worker),
        StubChangeSets({plan.id: _manual_intervention_change_set(plan, repository_id, worker.id)}),
        StubArchives(),
    )

    assert (await service.get_delivery(plan.id))["tasks"][0]["evidence"] is None


# ---------------------------------------------------------------------------
# A refused delivery is projected, not left to be guessed at (A-19)
# ---------------------------------------------------------------------------


class StubRepositoryProfiles:
    """One catalog row, so the refusal can name a repository rather than a UUID."""

    def __init__(self, repository_id: UUID, name: str) -> None:
        self._profile = RepositoryProfileData(
            id=repository_id,
            name=name,
            url=f"https://github.com/example/{name}",
            description="",
            topics=(),
            languages=(),
            profiled_at=NOW,
        )

    async def list(self):
        return (self._profile,)

    async def profiles(self):
        return (self._profile,)


@pytest.mark.asyncio
async def test_a_refused_delivery_is_projected_with_the_servers_own_words() -> None:
    """Defect A-19: the console must be able to say why the round is stopped.

    Before this, a round whose batch had succeeded and whose delivery kept
    refusing projected as EXECUTE with "第 1/1 批执行中" — a note that was true
    of nothing. The batch was done; delivery had refused it; the refusal lived
    only in a log line nobody was reading.

    ``reason`` is asserted character-for-character against what
    ``_candidates_for_batch`` raises. A projection that translated or
    summarised it would hand the operator a sentence they cannot match against
    the evidence or the log.
    """

    project_id = uuid4()
    repository_id = uuid4()
    leader_task_id = uuid4()
    refused_at = datetime(2026, 8, 12, 3, 4, 5, tzinfo=UTC)
    plan = replace(
        _plan(project_id, repository_id, leader_task_id, ExecutionPlanStatus.IN_PROGRESS),
        delivery_refusal=DeliveryRefusalView(
            reason="Runner evidence has no test results",
            batch_index=0,
            repository_id=repository_id,
            task_id=leader_task_id,
            at=refused_at,
        ),
    )
    service = _service(
        StubPlans(plan),
        StubSnapshots(),
        StubTasks(),
        StubChangeSets({}),
        StubArchives(),
        repositories=StubRepositoryProfiles(repository_id, "repomesh-e2e-checkout"),
    )

    detail = await service.get_delivery(plan.id)
    assert detail["delivery_refusal"] == {
        "reason": "Runner evidence has no test results",
        "batch_index": 0,
        "repository_id": repository_id,
        "repository_name": "repomesh-e2e-checkout",
        "task_id": leader_task_id,
        "at": refused_at,
    }
    # And the list a console opens on says it too, ahead of the batch counter
    # that used to be the only thing this round reported.
    listed = await service.list_deliveries()
    summary = listed["projects"][0]["deliveries"][0]
    assert summary["phase_note"] == "交付被拒:Runner evidence has no test results"


@pytest.mark.asyncio
async def test_a_round_with_no_refusal_projects_null_and_its_ordinary_note() -> None:
    """The reverse: nothing invented for the ordinary case."""

    project_id = uuid4()
    repository_id = uuid4()
    leader_task_id = uuid4()
    plan = _plan(project_id, repository_id, leader_task_id, ExecutionPlanStatus.IN_PROGRESS)
    service = _service(
        StubPlans(plan), StubSnapshots(), StubTasks(), StubChangeSets({}), StubArchives()
    )

    assert (await service.get_delivery(plan.id))["delivery_refusal"] is None
    listed = await service.list_deliveries()
    assert listed["projects"][0]["deliveries"][0]["phase_note"] == "第 1/1 批执行中"


# -------------------------------------------------- A-18, fourth face -------


@pytest.mark.asyncio
async def test_a_failed_task_projects_its_reason_and_contributes_no_diff() -> None:
    """The reason an operator can act on, and nothing that pretends to be a change.

    ``changed_path_denied: tests/test_discount.py`` is the live text, and it
    names the fix (add ``tests/`` to allowed_paths). It had no field to live in
    while evidence was gated on a commit sha, so the console rendered "failed"
    and stopped there.

    diffs[] must stay empty for the same row: §3 declares ``commit_sha`` a
    string, and a run that never committed produced no change to diff.
    """

    project_id = uuid4()
    repository_id = uuid4()
    leader_task_id = uuid4()
    reason = "changed_path_denied: tests/test_discount.py"
    plan = _plan(project_id, repository_id, leader_task_id, ExecutionPlanStatus.IN_PROGRESS)
    worker = replace(
        _worker(project_id, repository_id, leader_task_id),
        status=TaskStatus.FAILED,
        result_summary="{...}",
        evidence=TaskEvidenceView(
            commit_sha=None,
            run_id=uuid4(),
            changed_files=("src/checkout/tiers.py", "tests/test_discount.py"),
            base_sha="0" * 40,
            summary_text=reason,
            test_command=None,
            test_results=(),
            artifact_count=0,
        ),
    )
    service = _service(
        StubPlans(plan),
        StubSnapshots(),
        StubTasks(worker),
        StubChangeSets({plan.id: _manual_intervention_change_set(plan, repository_id, worker.id)}),
        StubArchives(),
    )

    detail = await service.get_delivery(plan.id)
    task = detail["tasks"][0]

    assert task["display_status"] == "failed"
    assert task["evidence"] is not None, "a failed run's evidence is still evidence"
    assert task["evidence"]["summary_text"] == reason
    assert task["evidence"]["verified"] is False
    assert detail["diffs"] == []


class LiveChangeSets:
    """The composition root's read-model change-set source, verbatim.

    ``container.py`` wires ``merge_gate`` straight to
    ``DeliveryService.evaluate_merge_gate``. Every other merge-gate test in
    this file stubs that call, which is exactly the seam a real regression
    would hide behind, so this one runs the real evaluator.
    """

    def __init__(self, delivery, delivery_id: UUID) -> None:
        self._delivery = delivery
        self._delivery_id = delivery_id

    async def for_delivery(self, delivery_id: UUID):
        from repomesh.modules.delivery import delivery_change_set_key

        return await self._delivery.get_by_idempotency_key(
            delivery_change_set_key(delivery_id)
        )

    async def merge_gate(self, change_set_id: UUID, repository_id: UUID):
        return await self._delivery.evaluate_merge_gate(change_set_id, repository_id)


@pytest.mark.asyncio
async def test_a_reviewable_candidate_projects_a_real_merge_gate_verdict() -> None:
    """The decision has to be *visible*, and 'blocked, pending review' is a decision.

    The live shape after a publish converges: PR open, non-draft, required CI
    green, one approval required and none given. The honest answer is
    ``allowed: false`` with reasons -- and it must reach the read model, since
    ``null`` there means "the question is moot", which this is not.

    ``merge_gate`` is computed on read (``attach_merge_gates``), never stored,
    so what makes it appear is the projection call, not anything a reconciler
    sweep does or does not write.
    """

    from repomesh.modules.delivery import (
        DeliveryService,
        InMemoryChangeSetStore,
        delivery_change_set_key,
    )
    from repomesh.modules.delivery.contracts import (
        CIObservationCommand,
        PrepareChangeSetCommand,
        PullRequestObservationCommand,
        RepositoryCandidateInput,
    )

    project_id = uuid4()
    repository_id = uuid4()
    leader_task_id = uuid4()
    plan = _plan(project_id, repository_id, leader_task_id, ExecutionPlanStatus.COMPLETED)
    worker = _worker(project_id, repository_id, leader_task_id)

    delivery = DeliveryService(InMemoryChangeSetStore())
    change_set = await delivery.prepare(
        PrepareChangeSetCommand(
            organization_id=uuid4(),
            project_id=project_id,
            created_by_agent_id=uuid4(),
            title="Live shape",
            validation_snapshot_id=uuid4(),
            candidates=(
                RepositoryCandidateInput(
                    repository_id=repository_id,
                    task_id=worker.id,
                    commit_sha="a" * 40,
                    base_sha="b" * 40,
                    branch_name="repomesh/a762abba/9dfa78f2",
                    required_checks=("unit-tests",),
                    required_approvals=1,
                ),
            ),
        ),
        idempotency_key=delivery_change_set_key(plan.id),
    )
    await delivery.observe_pull_request(
        PullRequestObservationCommand(
            change_set.id,
            repository_id,
            3,
            "https://github.com/acme/pricing/pull/3",
            "a" * 40,
        )
    )
    current = await delivery.observe_ci(
        CIObservationCommand(change_set.id, repository_id, True, "901", "passed", "unit-tests")
    )
    assert current.repositories[0].status is RepositoryDeliveryStatus.REVIEW_PENDING

    service = _service(
        StubPlans(plan),
        StubSnapshots(),
        StubTasks(worker),
        LiveChangeSets(delivery, plan.id),
        StubArchives(),
    )

    detail = await service.attach_merge_gates(await service.get_delivery(plan.id))

    gate = detail["change_set"]["repositories"][0]["merge_gate"]
    assert gate is not None, "a reviewable candidate's gate verdict must be visible"
    assert gate["allowed"] is False
    assert "required reviews have not passed" in gate["reasons"]
