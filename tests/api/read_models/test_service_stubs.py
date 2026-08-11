"""Service-level branches not reachable through the public write paths.

MANUAL_INTERVENTION recovery actions are only produced by external recovery
flows, so escalated_to_human and the archived/failed list branches are driven
through stub sources implementing the read-model source protocols.
"""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from repomesh.api.read_models import REWORK_TASK_TITLE, DeliveryReadModelService
from repomesh.api.read_models.sources import PlanSnapshotData
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
)
from repomesh.modules.task_orchestration.contracts import (
    ExecutionPlanStatus,
    ExecutionPlanView,
    PlannedRepositoryTaskView,
    TaskStatus,
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

    async def matrix_room_id(self, project_id: UUID):
        return None


def _service(plans, snapshots, tasks, change_sets, archives, repositories=None):
    empty = _Empty()
    return DeliveryReadModelService(
        plans=plans,
        snapshots=snapshots,
        tasks=tasks,
        change_sets=change_sets,
        archives=archives,
        validations=empty,
        specifications=empty,
        repositories=repositories if repositories is not None else empty,
        agents=empty,
        topology=empty,
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
    archived_plan = _plan(
        project_id, repository_id, uuid4(), ExecutionPlanStatus.COMPLETED
    )
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
        item["delivery_id"]: item["phase"]
        for item in default_listing["projects"][0]["deliveries"]
    }
    assert phases == {failed_plan.id: "failed"}

    full_listing = await service.list_deliveries(include_archived=True)
    phases = {
        item["delivery_id"]: item["phase"]
        for item in full_listing["projects"][0]["deliveries"]
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
        worker, id=uuid4(), title=REWORK_TASK_TITLE, status=TaskStatus.SUCCEEDED
    )
    running_rework = replace(
        worker, id=uuid4(), title=REWORK_TASK_TITLE, status=TaskStatus.IN_PROGRESS
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
    assert all(
        entry["at"] is not None
        for timeline in timelines.values()
        for entry in timeline
    )


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
            _repository_view(
                RepositoryDeliveryStatus.READY_TO_MERGE, repository_id, worker.id
            ),
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
    rework = replace(
        worker, id=uuid4(), title=REWORK_TASK_TITLE, status=TaskStatus.IN_PROGRESS
    )
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
