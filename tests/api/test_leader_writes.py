"""``POST .../plan`` and ``POST .../review`` over HTTP, against the freeze.

The two writes of ``contracts/leader-actions/v1``. Every request body that
should be accepted is a frozen fixture, every receipt is checked against the
frozen receipt schema, and every refusal against ``fixtures/error-matrix.json``
and the structured-error schema — so a test cannot go on passing after the
freeze moves, which is the one thing a contract test must not do.

The two invalid plan fixtures are consumed as what their filenames claim. The
three clamp families the freeze ships no fixture for (coverage, allowed paths,
tests) are built by *mutating the valid fixture*, one field at a time: a
hand-written invalid plan could fail for a reason nobody intended and still
look like it proved something, while a one-field edit of the known-good
document proves the field under test is the thing being refused.

The stores are in-memory and the orchestrator is a double, but the *use cases*
and the *routes* are production — the matrix under test is the translation
between them. What a real batch assignment writes into these stores is settled
next door in ``tests/task_orchestration/test_leader_review_lifecycle.py``.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from contracts.test_leader_actions_v1_contract import validate_document
from fastapi import FastAPI
from fastapi.testclient import TestClient

from repomesh.api.router import api_router
from repomesh.integrations.runner import StartAssignedWorkerTask
from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalStatus,
    AgentPrincipalView,
    AgentRole,
)
from repomesh.modules.project.contracts import TeamDecompositionMode
from repomesh.modules.task_orchestration.application import (
    SubmitRepositoryPlan,
    SubmitRepositoryReview,
)
from repomesh.modules.task_orchestration.contracts import (
    AssignTaskCommand,
    LeaderAssignmentPhase,
    LeaderAssignmentView,
    LeaderReviewEvidenceView,
    LeaderSafetyEnvelopeView,
    ReportTaskCommand,
    TaskOrigin,
    TaskStatus,
    TaskTestResultView,
    TaskView,
    WorkerEvidenceView,
    WorkerRosterEntryView,
)
from repomesh.modules.task_orchestration.domain import Task
from repomesh.modules.task_orchestration.infrastructure import (
    InMemoryLeaderAssignmentStore,
    InMemoryTaskStore,
)
from repomesh.settings import get_settings

CONTRACT = Path(__file__).parents[2] / "contracts" / "leader-actions" / "v1"
FIXTURES = CONTRACT / "fixtures"

ORGANIZATION_ID = uuid4()
PROJECT_ID = uuid4()
REPOSITORY_ID = uuid4()
LEADER_ID = uuid4()
OTHER_LEADER_ID = uuid4()
LEADER_TASK_ID = uuid4()

LEADER_TOKEN = "leader-token-value"
OTHER_LEADER_TOKEN = "other-leader-token-value"
WORKER_TOKEN = "worker-token-value"

#: The fixtures' own worker agent id. The plan bodies name it as their
#: assignee, so the roster this test parks must contain exactly it — the
#: fixtures form one coherent scenario and splitting it would make
#: ``plan_invalid_assignee`` pass for the wrong reason.
FIXTURE_WORKER_ID = UUID("00000000-0000-0000-0000-000000000002")
FIXTURE_OUTSIDER_ID = UUID("00000000-0000-0000-0000-000000000099")

TITLE = "Multi-currency quoting: extend the pricing contract"
ACCEPTANCE = ("Quote contract carries currency", "Existing USD behavior unchanged")


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def plan_body(**edits: Any) -> dict[str, Any]:
    """The valid plan fixture, optionally with one field replaced."""

    body = deepcopy(load(FIXTURES / "plan-decision.valid.json"))
    body.update(edits)
    return body


def review_body(name: str, **edits: Any) -> dict[str, Any]:
    body = deepcopy(load(FIXTURES / name))
    body.update(edits)
    return body


# ---------------------------------------------------------------------------
# Doubles: real stores, a recording orchestrator
# ---------------------------------------------------------------------------


def principal(agent_id: UUID, role: AgentRole) -> AgentPrincipalView:
    return AgentPrincipalView(
        id=agent_id,
        organization_id=ORGANIZATION_ID,
        role=role,
        leader_agent_id=None,
        repository_id=REPOSITORY_ID,
        responsibility_paths=("src/pricing_core/",),
        agentteams_resource_name=f"member-{role.value}",
        status=AgentPrincipalStatus.ACTIVE,
    )


class StubDirectory:
    def __init__(self) -> None:
        self._principals = {
            LEADER_ID: principal(LEADER_ID, AgentRole.REPOSITORY_LEADER),
            OTHER_LEADER_ID: principal(OTHER_LEADER_ID, AgentRole.REPOSITORY_LEADER),
            FIXTURE_WORKER_ID: principal(FIXTURE_WORKER_ID, AgentRole.WORKER),
        }

    async def get_view(self, agent_id: UUID) -> AgentPrincipalView | None:
        return self._principals.get(agent_id)

    async def list_views(self) -> tuple[AgentPrincipalView, ...]:
        return tuple(self._principals.values())


class StubModes:
    def __init__(self, mode: TeamDecompositionMode) -> None:
        self._mode = mode

    async def decomposition_mode(
        self, project_id: UUID, repository_id: UUID
    ) -> TeamDecompositionMode:
        return self._mode


class RecordingOrchestrator:
    """Assign, permit and report, over the same task store the use cases read.

    Idempotent by key like the real one, because the plan path's whole replay
    story rests on it: a resubmission must find the tasks it already created
    rather than make a second set beside them.
    """

    def __init__(self, tasks: InMemoryTaskStore) -> None:
        self._tasks = tasks
        self.assigned: list[tuple[str, AssignTaskCommand, TaskOrigin]] = []
        self.permits: list[tuple[UUID, tuple[str, ...], tuple[str, ...]]] = []
        self.reports: list[ReportTaskCommand] = []
        #: Task ids announced, in order. The endpoint tests care that the
        #: announcement happens at all; the ordering proof against the permit
        #: lives in ``tests/task_orchestration/test_dispatch_ordering.py``.
        self.announced: list[UUID] = []

    async def assign(
        self,
        command: AssignTaskCommand,
        *,
        idempotency_key: str,
        origin: TaskOrigin = TaskOrigin.PLANNED,
        deliver: bool = True,
    ) -> TaskView:
        existing = await self._tasks.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            return existing[0].to_view()
        task = Task(
            id=uuid4(),
            organization_id=command.organization_id,
            project_id=command.project_id,
            repository_id=command.repository_id,
            parent_task_id=command.parent_task_id,
            assigned_by_agent_id=command.assigned_by_agent_id,
            assignee_agent_id=command.assignee_agent_id,
            title=command.title,
            instruction=command.instruction,
            acceptance=command.acceptance,
            status=TaskStatus.ASSIGNED,
            origin=origin,
            database_change=command.database_change,
        )
        await self._tasks.add(task, idempotency_key=idempotency_key, request_fingerprint="fp")
        self.assigned.append((idempotency_key, command, origin))
        return task.to_view()

    async def deliver_assignment(self, task_id: UUID) -> None:
        self.announced.append(task_id)

    async def ensure_approved(
        self,
        task: TaskView,
        *,
        allowed_paths: tuple[str, ...],
        tests: tuple[str, ...],
        idempotency_key: str,
    ) -> None:
        self.permits.append((task.id, allowed_paths, tests))

    async def report(
        self, command: ReportTaskCommand, *, idempotency_key: str
    ) -> TaskView:
        self.reports.append(command)
        task = await self._tasks.get(command.task_id)
        assert task is not None
        updated = task.report(command.status, command.summary)
        await self._tasks.update(updated, expected_version=task.version)
        return updated.to_view()


class StubContainer:
    def __init__(self, plan: SubmitRepositoryPlan, review: SubmitRepositoryReview) -> None:
        self._plan = plan
        self._review = review

    def leader_plan_submitter(self) -> SubmitRepositoryPlan:
        return self._plan

    def leader_review_submitter(self) -> SubmitRepositoryReview:
        return self._review


def leader_task(assignee_agent_id: UUID = LEADER_ID) -> Task:
    return Task(
        id=LEADER_TASK_ID,
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        repository_id=REPOSITORY_ID,
        parent_task_id=None,
        assigned_by_agent_id=uuid4(),
        assignee_agent_id=assignee_agent_id,
        title=TITLE,
        instruction="Extend the shared quote contract with a currency field.",
        acceptance=ACCEPTANCE,
        status=TaskStatus.ASSIGNED,
    )


def evidence(*worker_task_ids: UUID, review_revision: int = 1) -> LeaderReviewEvidenceView:
    return LeaderReviewEvidenceView(
        review_revision=review_revision,
        worker_evidence=tuple(
            WorkerEvidenceView(
                worker_task_id=task_id,
                worker_agent_id=FIXTURE_WORKER_ID,
                status=TaskStatus.SUCCEEDED,
                run_id=uuid4(),
                commit_sha="3f2a9d1b7c4e8f0a6b5d2c1e9f8a7b6c5d4e3f2a",
                changed_files=("src/pricing_core/quote.py",),
                test_results=(
                    TaskTestResultView(command="python scripts/run_tests.py", exit_code=0),
                ),
            )
            for task_id in worker_task_ids
        ),
    )


def assignment(
    *,
    phase: LeaderAssignmentPhase = LeaderAssignmentPhase.PLANNING,
    review_evidence: LeaderReviewEvidenceView | None = None,
    review_revision: int = 1,
) -> LeaderAssignmentView:
    return LeaderAssignmentView(
        leader_task_id=LEADER_TASK_ID,
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        repository_id=REPOSITORY_ID,
        leader_agent_id=LEADER_ID,
        phase=phase,
        safety_envelope=LeaderSafetyEnvelopeView(
            allowed_path_roots=("src/pricing_core/", "tests/"),
            test_paths=("tests/",),
            test_commands=("python scripts/run_tests.py",),
        ),
        worker_roster=(
            WorkerRosterEntryView(
                worker_agent_id=FIXTURE_WORKER_ID,
                worker_name="pricing-codex-worker",
                responsibility_paths=("src/pricing_core/",),
            ),
        ),
        review_revision=review_revision,
        review_evidence=review_evidence,
    )


class World:
    """One parked assignment plus everything the two writes act through."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        mode: TeamDecompositionMode = TeamDecompositionMode.LEADER,
        parked: LeaderAssignmentView | None = None,
        with_assignment: bool = True,
        assignee_agent_id: UUID = LEADER_ID,
    ) -> None:
        monkeypatch.setenv(
            "REPOMESH_RUNNER_WORKER_TOKENS",
            json.dumps(
                {
                    str(LEADER_ID): LEADER_TOKEN,
                    str(OTHER_LEADER_ID): OTHER_LEADER_TOKEN,
                    str(FIXTURE_WORKER_ID): WORKER_TOKEN,
                }
            ),
        )
        get_settings.cache_clear()
        self.tasks = InMemoryTaskStore()
        self.tasks.tasks[LEADER_TASK_ID] = leader_task(assignee_agent_id)
        self.assignments = InMemoryLeaderAssignmentStore()
        if with_assignment:
            self.assignments.assignments[LEADER_TASK_ID] = parked or assignment()
        self.orchestrator = RecordingOrchestrator(self.tasks)
        self.advanced: list[UUID] = []
        directory = StubDirectory()
        modes = StubModes(mode)
        application = FastAPI()
        application.include_router(api_router)
        application.state.container = StubContainer(
            SubmitRepositoryPlan(
                self.assignments,
                self.tasks,
                directory,
                modes,
                self.orchestrator,
                spec_author=self.orchestrator,
            ),
            SubmitRepositoryReview(
                self.assignments,
                self.tasks,
                directory,
                modes,
                self.orchestrator,
                self.orchestrator,
                spec_author=self.orchestrator,
                on_leader_task_terminal=self._advance,
            ),
        )
        self.client = TestClient(application)

    async def _advance(self, leader_task_id: UUID) -> None:
        self.advanced.append(leader_task_id)

    def post_plan(self, body: dict[str, Any], *, token: str | None = LEADER_TOKEN):
        return self._post("plan", body, token)

    def post_review(self, body: dict[str, Any], *, token: str | None = LEADER_TOKEN):
        return self._post("review", body, token)

    def _post(self, leaf: str, body: dict[str, Any], token: str | None):
        headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
        return self.client.post(
            f"/api/v1/agent-actions/leader/assignments/{LEADER_TASK_ID}/{leaf}",
            json=body,
            headers=headers,
        )


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    return World(monkeypatch)


def assert_refusal(response, *, status: int, code: str) -> None:
    """One refusal, checked against the frozen schema and the frozen matrix."""

    assert response.status_code == status, response.text
    body = response.json()
    validate_document(load(CONTRACT / "structured-error.schema.json"), body)
    assert body["detail"]["code"] == code
    matrix: dict[str, list[str]] = load(FIXTURES / "error-matrix.json")
    assert code in matrix[str(status)]
    assert body["detail"]["message"]
    assert LEADER_TOKEN not in body["detail"]["message"]


# ---------------------------------------------------------------------------
# POST /plan — 200 and the receipt
# ---------------------------------------------------------------------------


def test_a_valid_plan_is_accepted_and_answers_the_frozen_receipt(world: World) -> None:
    response = world.post_plan(plan_body())

    assert response.status_code == 200, response.text
    body = response.json()
    validate_document(load(CONTRACT / "plan-receipt.schema.json"), body)
    assert body["schemaVersion"] == "repomesh.leader-actions.plan-receipt.v1"
    assert body["leaderTaskId"] == str(LEADER_TASK_ID)
    assert body["planRevision"] == 1
    assert len(body["workerTaskIds"]) == 2


def test_the_plan_creates_worker_tasks_in_the_order_it_declared_them(world: World) -> None:
    body = world.post_plan(plan_body()).json()

    titles = [command.title for _, command, _ in world.orchestrator.assigned]
    assert titles == [
        "Extend the quote contract with currency and rounding rules",
        "Implement per-currency rounding",
    ]
    # The receipt names the same tasks, in the same order.
    assert body["workerTaskIds"] == [
        str(command_id) for command_id in _created_ids(world)
    ]


def test_the_worker_tasks_hang_off_the_leader_task_and_are_planned_work(world: World) -> None:
    world.post_plan(plan_body())

    for _, command, origin in world.orchestrator.assigned:
        assert command.parent_task_id == LEADER_TASK_ID
        assert command.assignee_agent_id == FIXTURE_WORKER_ID
        assert command.assigned_by_agent_id == LEADER_ID
        assert origin is TaskOrigin.PLANNED


def test_each_permit_carries_the_leaders_own_paths_and_tests(world: World) -> None:
    """The point of the slice: the leader's plan reaches the execution permit.

    Not ``derive_allowed_paths`` over the worker's responsibility — the leader
    said which paths this particular task needs, and it has already been
    clamped to the envelope, so what lands here is narrower than the server's
    own derivation rather than wider.
    """

    world.post_plan(plan_body())

    paths = [permit[1] for permit in world.orchestrator.permits]
    tests = [permit[2] for permit in world.orchestrator.permits]
    assert paths == [
        ("src/pricing_core/contracts/", "tests/"),
        ("src/pricing_core/", "tests/"),
    ]
    assert tests == [("python scripts/run_tests.py",)] * 2


def test_the_assignment_moves_to_executing_and_keeps_the_plan(world: World) -> None:
    world.post_plan(plan_body())

    stored = world.assignments.assignments[LEADER_TASK_ID]
    assert stored.phase is LeaderAssignmentPhase.EXECUTING
    assert stored.accepted_plan is not None
    # Persisted verbatim: the leader's Engineering Spec is the leader's.
    assert stored.accepted_plan.decision["engineeringSpec"] == (
        load(FIXTURES / "plan-decision.valid.json")["engineeringSpec"]
    )


# ---------------------------------------------------------------------------
# POST /plan — idempotency (frozen invariant 2)
# ---------------------------------------------------------------------------


def test_resubmitting_the_same_plan_returns_the_same_receipt_byte_for_byte(
    world: World,
) -> None:
    first = world.post_plan(plan_body())
    second = world.post_plan(plan_body())

    assert (first.status_code, second.status_code) == (200, 200)
    assert first.json() == second.json()
    # And nothing was created twice.
    assert len(world.orchestrator.assigned) == 2


def test_key_order_does_not_make_a_resubmission_a_different_plan(world: World) -> None:
    """JSON objects are unordered, so a reserialised retry is the same plan."""

    first = world.post_plan(plan_body())
    reordered = dict(reversed(list(plan_body().items())))
    second = world.post_plan(reordered)

    assert second.status_code == 200, second.text
    assert first.json() == second.json()


def test_a_different_plan_under_the_same_key_is_a_phase_conflict(world: World) -> None:
    """Never a silent replacement: worker tasks are already in flight."""

    world.post_plan(plan_body())
    changed = plan_body()
    changed["engineeringSpec"]["summary"] = "A different plan entirely."

    assert_refusal(world.post_plan(changed), status=409, code="phase_conflict")
    assert len(world.orchestrator.assigned) == 2


def test_a_plan_submitted_outside_planning_is_a_phase_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World(
        monkeypatch,
        parked=assignment(
            phase=LeaderAssignmentPhase.REVIEW_DUE, review_evidence=evidence(uuid4())
        ),
    )

    assert_refusal(world.post_plan(plan_body()), status=409, code="phase_conflict")
    assert world.orchestrator.assigned == []


# ---------------------------------------------------------------------------
# POST /plan — the five clamp families (frozen invariants 3, 4 and 5)
# ---------------------------------------------------------------------------


def test_a_cyclic_dag_is_refused(world: World) -> None:
    """The frozen fixture, refused for the reason its filename claims."""

    body = load(FIXTURES / "plan-decision.invalid-dag-cycle.json")

    assert_refusal(world.post_plan(body), status=409, code="plan_invalid_dag_cycle")
    assert world.orchestrator.assigned == []


def test_an_assignee_outside_the_roster_is_refused(world: World) -> None:
    body = load(FIXTURES / "plan-decision.invalid-assignee.json")
    assert body["workerTasks"][0]["assigneeWorkerAgentId"] == str(FIXTURE_OUTSIDER_ID)

    assert_refusal(world.post_plan(body), status=409, code="plan_invalid_assignee")


def test_a_worker_task_naming_no_declared_node_is_refused(world: World) -> None:
    body = plan_body()
    body["workerTasks"][1]["nodeId"] = "a-node-the-dag-never-declared"

    assert_refusal(world.post_plan(body), status=409, code="plan_invalid_dag_coverage")


def test_a_node_no_worker_task_claims_is_refused(world: World) -> None:
    body = plan_body()
    body["taskDag"]["nodes"].append({"nodeId": "orphan"})

    assert_refusal(world.post_plan(body), status=409, code="plan_invalid_dag_coverage")


def test_an_edge_naming_an_undeclared_node_is_refused(world: World) -> None:
    body = plan_body()
    body["taskDag"]["edges"].append({"from": "contract", "to": "nowhere"})

    assert_refusal(world.post_plan(body), status=409, code="plan_invalid_dag_coverage")


def test_allowed_paths_outside_the_envelope_are_refused(world: World) -> None:
    body = plan_body()
    body["workerTasks"][0]["allowedPaths"] = ["src/billing/"]

    assert_refusal(world.post_plan(body), status=409, code="plan_invalid_allowed_paths")


def test_dropping_an_envelope_test_command_is_refused(world: World) -> None:
    body = plan_body()
    body["workerTasks"][1]["tests"] = []

    assert_refusal(world.post_plan(body), status=409, code="plan_invalid_tests_removed")


def test_a_plan_may_add_tests_but_not_remove_them(world: World) -> None:
    """The clamp is a superset rule, not an equality one."""

    body = plan_body()
    for draft in body["workerTasks"]:
        draft["tests"] = ["python scripts/run_tests.py", "ruff check ."]

    assert world.post_plan(body).status_code == 200


def test_manager_database_declaration_is_copied_to_worker_task(world: World) -> None:
    body = plan_body()
    body["workerTasks"][0]["databaseChange"] = {
        "declared": True,
        "required": True,
        "changeKinds": ["schema", "migration"],
        "affectedTables": ["quotes"],
        "migrationRequired": True,
        "backfillRequired": False,
        "requiredChecks": ["migration_apply"],
    }

    assert world.post_plan(body).status_code == 200
    command = world.orchestrator.assigned[0][1]
    assert command.database_change.required
    assert command.database_change.affected_tables == ("quotes",)
    assert command.database_change.required_checks == ("migration_apply",)
    assert ".repomesh/database-change-report.json" in world.orchestrator.permits[0][1]


def test_provenance_that_is_not_a_leader_session_is_refused(world: World) -> None:
    body = plan_body()
    body["provenance"]["source"] = "server-decomposer"

    assert_refusal(world.post_plan(body), status=409, code="plan_invalid_provenance")


def test_nothing_is_dispatched_by_a_refused_plan(world: World) -> None:
    body = plan_body()
    body["workerTasks"][0]["allowedPaths"] = ["/etc/"]

    world.post_plan(body)

    assert world.orchestrator.assigned == []
    assert world.orchestrator.permits == []
    assert world.assignments.assignments[LEADER_TASK_ID].phase is LeaderAssignmentPhase.PLANNING


# ---------------------------------------------------------------------------
# POST /review — the three verdicts
# ---------------------------------------------------------------------------


def _planned_world(monkeypatch: pytest.MonkeyPatch) -> tuple[World, list[UUID]]:
    """A world whose plan was accepted and whose round is now waiting on review."""

    world = World(monkeypatch)
    world.post_plan(plan_body())
    worker_task_ids = _created_ids(world)
    world.assignments.assignments[LEADER_TASK_ID] = assignment(
        phase=LeaderAssignmentPhase.REVIEW_DUE,
        review_evidence=evidence(*worker_task_ids),
    )
    return world, worker_task_ids


def _created_ids(world: World) -> list[UUID]:
    return [
        task.id
        for task in world.tasks.tasks.values()
        if task.parent_task_id == LEADER_TASK_ID
    ]


def test_approve_succeeds_the_leader_task_with_the_leaders_own_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, _ = _planned_world(monkeypatch)
    body = review_body("review-decision.approve.json")

    response = world.post_review(body)

    assert response.status_code == 200, response.text
    receipt = response.json()
    validate_document(load(CONTRACT / "review-receipt.schema.json"), receipt)
    assert receipt["verdict"] == "approve"
    assert receipt["leaderTaskStatus"] == "succeeded"
    assert receipt["reworkTaskIds"] == []
    assert receipt["reviewRevision"] == 1
    # Through the ordinary report gateway, so the roll-up body is the summary.
    assert [report.summary for report in world.orchestrator.reports] == [body["summary"]]
    assert world.tasks.tasks[LEADER_TASK_ID].status is TaskStatus.SUCCEEDED


def test_approve_matches_the_frozen_receipt_fixture_field_for_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, _ = _planned_world(monkeypatch)

    receipt = world.post_review(review_body("review-decision.approve.json")).json()
    frozen = load(FIXTURES / "review-receipt.approve.json")

    assert {key: receipt[key] for key in frozen if key != "leaderTaskId"} == {
        key: frozen[key] for key in frozen if key != "leaderTaskId"
    }


def test_approve_lets_the_plan_advance(monkeypatch: pytest.MonkeyPatch) -> None:
    """In leader mode this is the only way a leader task reaches a terminal status."""

    world, _ = _planned_world(monkeypatch)

    world.post_review(review_body("review-decision.approve.json"))

    assert world.advanced == [LEADER_TASK_ID]


def test_escalate_blocks_the_leader_task_and_does_not_advance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BLOCKED is not final, so the round waits for an operator rather than failing."""

    world, _ = _planned_world(monkeypatch)
    body = review_body("review-decision.approve.json", verdict="escalate")

    receipt = world.post_review(body).json()

    assert receipt["leaderTaskStatus"] == "blocked"
    assert receipt["reworkTaskIds"] == []
    assert world.tasks.tasks[LEADER_TASK_ID].status is TaskStatus.BLOCKED
    assert world.advanced == []


def test_request_rework_creates_a_revision_task_and_reopens_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, worker_task_ids = _planned_world(monkeypatch)
    body = review_body("review-decision.request-rework.json")
    body["findings"][0]["workerTaskId"] = str(worker_task_ids[1])

    receipt = world.post_review(body).json()

    assert receipt["verdict"] == "request_rework"
    assert receipt["leaderTaskStatus"] == "in_progress"
    assert len(receipt["reworkTaskIds"]) == 1
    created = UUID(receipt["reworkTaskIds"][0])
    assert world.tasks.tasks[created].origin is TaskOrigin.REWORK
    assert world.tasks.tasks[created].parent_task_id == LEADER_TASK_ID
    assert body["findings"][0]["reworkInstruction"] in world.tasks.tasks[created].instruction

    stored = world.assignments.assignments[LEADER_TASK_ID]
    assert stored.phase is LeaderAssignmentPhase.EXECUTING
    assert stored.review_revision == 2
    # The next round is judged against its own snapshot.
    assert stored.review_evidence is None


def test_request_rework_never_touches_the_task_it_criticises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Frozen invariant 5: the evidence a verdict was based on stays as reviewed."""

    world, worker_task_ids = _planned_world(monkeypatch)
    reviewed = world.tasks.tasks[worker_task_ids[1]]
    body = review_body("review-decision.request-rework.json")
    body["findings"][0]["workerTaskId"] = str(worker_task_ids[1])

    world.post_review(body)

    assert world.tasks.tasks[worker_task_ids[1]] == reviewed
    assert world.orchestrator.reports == []


def test_a_rework_permit_is_bounded_by_the_stored_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, worker_task_ids = _planned_world(monkeypatch)
    body = review_body("review-decision.request-rework.json")
    body["findings"][0]["workerTaskId"] = str(worker_task_ids[1])

    world.post_review(body)

    assert world.orchestrator.permits[-1][1] == ("src/pricing_core/", "tests/")
    assert world.orchestrator.permits[-1][2] == ("python scripts/run_tests.py",)


# ---------------------------------------------------------------------------
# POST /review — findings, phase and idempotency
# ---------------------------------------------------------------------------


def test_rework_without_an_actionable_finding_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, worker_task_ids = _planned_world(monkeypatch)
    body = review_body("review-decision.request-rework.json")
    body["findings"] = [
        {"workerTaskId": str(worker_task_ids[0]), "note": "A remark with nothing to do."}
    ]

    assert_refusal(world.post_review(body), status=409, code="review_invalid_findings")


def test_rework_with_no_findings_at_all_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    world, _ = _planned_world(monkeypatch)
    body = review_body("review-decision.request-rework.json", findings=[])

    assert_refusal(world.post_review(body), status=409, code="review_invalid_findings")


def test_a_finding_outside_the_evidence_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A verdict about something the leader was never shown."""

    world, _ = _planned_world(monkeypatch)
    body = review_body("review-decision.request-rework.json")
    body["findings"][0]["workerTaskId"] = str(uuid4())

    assert_refusal(world.post_review(body), status=409, code="review_invalid_findings")


def test_a_review_before_review_is_due_is_a_phase_conflict(world: World) -> None:
    assert_refusal(
        world.post_review(review_body("review-decision.approve.json")),
        status=409,
        code="phase_conflict",
    )


def test_resubmitting_the_same_verdict_returns_the_same_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, _ = _planned_world(monkeypatch)
    body = review_body("review-decision.approve.json")

    first = world.post_review(body)
    second = world.post_review(body)

    assert (first.status_code, second.status_code) == (200, 200)
    assert first.json() == second.json()
    # The leader task was reported once, not twice.
    assert len(world.orchestrator.reports) == 1


def test_a_different_verdict_for_the_same_round_is_a_phase_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, _ = _planned_world(monkeypatch)
    world.post_review(review_body("review-decision.approve.json"))

    second = world.post_review(
        review_body("review-decision.approve.json", summary="I changed my mind.")
    )

    assert_refusal(second, status=409, code="phase_conflict")


def test_a_rework_receipt_stays_replayable_after_its_round_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round 1's receipt must survive round 2 opening (the revision is the key)."""

    world, worker_task_ids = _planned_world(monkeypatch)
    body = review_body("review-decision.request-rework.json")
    body["findings"][0]["workerTaskId"] = str(worker_task_ids[1])

    first = world.post_review(body)
    replay = world.post_review(body)

    assert first.json() == replay.json()
    assert first.json()["reviewRevision"] == 1
    assert world.assignments.assignments[LEADER_TASK_ID].review_revision == 2


# ---------------------------------------------------------------------------
# The guard, shared by both writes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("leaf", ["plan", "review"])
def test_no_token_is_invalid_token(world: World, leaf: str) -> None:
    response = world._post(leaf, plan_body() if leaf == "plan" else review_body(
        "review-decision.approve.json"
    ), None)
    assert_refusal(response, status=401, code="invalid_token")


@pytest.mark.parametrize("leaf", ["plan", "review"])
def test_a_worker_token_is_forbidden_by_role(world: World, leaf: str) -> None:
    response = world._post(
        leaf,
        plan_body() if leaf == "plan" else review_body("review-decision.approve.json"),
        WORKER_TOKEN,
    )
    assert_refusal(response, status=403, code="forbidden_role")


@pytest.mark.parametrize("leaf", ["plan", "review"])
def test_another_leaders_token_is_forbidden_as_not_assignee(world: World, leaf: str) -> None:
    response = world._post(
        leaf,
        plan_body() if leaf == "plan" else review_body("review-decision.approve.json"),
        OTHER_LEADER_TOKEN,
    )
    assert_refusal(response, status=403, code="forbidden_not_assignee")


def test_an_unknown_assignment_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    world = World(monkeypatch, with_assignment=False)
    assert_refusal(world.post_plan(plan_body()), status=404, code="assignment_not_found")


def test_a_server_mode_team_refuses_leader_submissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World(monkeypatch, mode=TeamDecompositionMode.SERVER)
    assert_refusal(
        world.post_plan(plan_body()), status=409, code="decomposition_mode_conflict"
    )


def test_the_guard_runs_before_anything_is_created(world: World) -> None:
    world.post_plan(plan_body(), token=WORKER_TOKEN)
    assert world.orchestrator.assigned == []


# ---------------------------------------------------------------------------
# Shape versus judgement
# ---------------------------------------------------------------------------


def test_a_body_that_is_not_the_frozen_document_is_refused_by_the_framework(
    world: World,
) -> None:
    """The one status outside the frozen matrix, and why it is outside it.

    The matrix enumerates the verdicts this surface renders. A body with no
    ``taskDag`` never reaches a verdict — mapping it onto ``plan_invalid_dag_*``
    would tell a leader its DAG is wrong when it did not send one, and would
    put a frozen code under a status the matrix does not give it. So shape is
    the framework's 422, exactly as on every other endpoint here.
    """

    body = plan_body()
    del body["taskDag"]

    assert world.post_plan(body).status_code == 422
    assert world.orchestrator.assigned == []


def test_an_undeclared_field_is_refused_rather_than_dropped(world: World) -> None:
    """Every frozen schema sets ``additionalProperties: false``."""

    assert world.post_plan(plan_body(unexpectedField="x")).status_code == 422


def test_the_body_may_not_carry_the_leader_task_id(world: World) -> None:
    """The id lives in the path; a body repeating it is a second place to disagree."""

    assert world.post_plan(plan_body(leaderTaskId=str(LEADER_TASK_ID))).status_code == 422


# ---------------------------------------------------------------------------
# AC-02: the credential that plans still cannot execute
# ---------------------------------------------------------------------------


def test_the_leaders_own_token_still_cannot_start_a_coding_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reverse boundary, for the credential this slice just widened.

    ``REPOMESH_RUNNER_WORKER_TOKENS`` now opens three more endpoints to a
    Repository Leader's token, so the interesting question is no longer whether
    a leader *principal* is refused execution — that was already pinned — but
    whether the same token that may now submit a plan may also start the work
    it planned. It may not, and nothing in this PR touches the check that says
    so: ``StartAssignedWorkerTask`` refuses a non-worker identity before it
    reads a task.
    """

    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", "")
    monkeypatch.setenv(
        "REPOMESH_RUNNER_WORKER_TOKENS", json.dumps({str(LEADER_ID): LEADER_TOKEN})
    )
    get_settings.cache_clear()

    tasks = InMemoryTaskStore()
    tasks.tasks[LEADER_TASK_ID] = leader_task()
    execution = StartAssignedWorkerTask(
        StubDirectory(),
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
    )
    application = FastAPI()
    application.include_router(api_router)
    application.state.container = _ExecutionContainer(execution)
    client = TestClient(application)

    response = client.post(
        "/api/v1/agent-actions/start-worker-task",
        headers={"Authorization": f"Bearer {LEADER_TOKEN}"},
        json={
            "task_id": str(uuid4()),
            "worker_agent_id": str(LEADER_ID),
            "adapter_id": "codex",
        },
    )

    assert response.status_code == 409
    assert "restricted to Worker identities" in response.json()["detail"]
    get_settings.cache_clear()


class _ExecutionContainer:
    def __init__(self, execution: object) -> None:
        self._execution = execution

    def worker_execution_service(self) -> object:
        return self._execution
