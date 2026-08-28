"""``GET /agent-actions/leader/assignments/{taskId}`` over HTTP, against the freeze.

The producer side of ``contracts/leader-actions/v1``. Every assertion about
shape or status comes from the contract's own files -- the schema for the 200,
``fixtures/error-matrix.json`` for the refusals, and the sample error fixtures
for the envelope -- rather than from a copy written here. A test that hand-
rolled the wire shape would go on passing after the freeze moved, which is the
one thing a contract test must not do.

``validate_document`` is imported from the wave-0 contract test rather than
reimplemented for the same reason: one walker, one set of schema keywords it
knows how to check, and a schema construct it does not recognise fails loudly
in both suites at once.

The stores and the directory are doubles; the *use case* is the production
``ReadLeaderAssignment`` and the route is the production route, because the
matrix under test is exactly the translation between them. What the assignment
records contain is settled next door in
``tests/task_orchestration/test_leader_mode_assignment.py``, where a real batch
assignment writes them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from contracts.test_leader_actions_v1_contract import validate_document
from fastapi import FastAPI
from fastapi.testclient import TestClient

from repomesh.api.leader_actions import LEADER_ACTION_ERROR_STATUS
from repomesh.api.router import api_router
from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalStatus,
    AgentPrincipalView,
    AgentRole,
)
from repomesh.modules.project.contracts import TeamDecompositionMode
from repomesh.modules.task_orchestration.application import ReadLeaderAssignment
from repomesh.modules.task_orchestration.contracts import (
    LeaderAssignmentPhase,
    LeaderAssignmentView,
    LeaderSafetyEnvelopeView,
    TaskStatus,
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
WORKER_ID = uuid4()
LEADER_TASK_ID = uuid4()

LEADER_TOKEN = "leader-token-value"
OTHER_LEADER_TOKEN = "other-leader-token-value"
WORKER_TOKEN = "worker-token-value"

TITLE = "Multi-currency quoting: extend the pricing contract"
INSTRUCTION = "Extend the shared quote contract with a currency field and rounding rules."
ACCEPTANCE = ("Quote contract carries currency", "Existing USD behavior unchanged")


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


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
            WORKER_ID: principal(WORKER_ID, AgentRole.WORKER),
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


class StubContainer:
    def __init__(self, reader: ReadLeaderAssignment) -> None:
        self._reader = reader

    def leader_assignment_reader(self) -> ReadLeaderAssignment:
        return self._reader


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
        instruction=INSTRUCTION,
        acceptance=ACCEPTANCE,
        status=TaskStatus.ASSIGNED,
    )


def assignment() -> LeaderAssignmentView:
    return LeaderAssignmentView(
        leader_task_id=LEADER_TASK_ID,
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        repository_id=REPOSITORY_ID,
        leader_agent_id=LEADER_ID,
        phase=LeaderAssignmentPhase.PLANNING,
        safety_envelope=LeaderSafetyEnvelopeView(
            allowed_path_roots=("src/pricing_core/", "tests/"),
            test_paths=("tests/",),
            test_commands=("python scripts/run_tests.py",),
        ),
        worker_roster=(
            WorkerRosterEntryView(
                worker_agent_id=WORKER_ID,
                worker_name="pricing-codex-worker",
                responsibility_paths=("src/pricing_core/",),
            ),
        ),
    )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """The default world: one parked assignment, one leader-mode team."""

    return build_client(monkeypatch)


def build_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: TeamDecompositionMode = TeamDecompositionMode.LEADER,
    with_assignment: bool = True,
    assignee_agent_id: UUID = LEADER_ID,
    tokens: dict[str, str] | None = None,
) -> TestClient:
    monkeypatch.setenv(
        "REPOMESH_RUNNER_WORKER_TOKENS",
        json.dumps(
            tokens
            if tokens is not None
            else {
                str(LEADER_ID): LEADER_TOKEN,
                str(OTHER_LEADER_ID): OTHER_LEADER_TOKEN,
                str(WORKER_ID): WORKER_TOKEN,
            }
        ),
    )
    get_settings.cache_clear()
    tasks = InMemoryTaskStore()
    tasks.tasks[LEADER_TASK_ID] = leader_task(assignee_agent_id)
    assignments = InMemoryLeaderAssignmentStore()
    if with_assignment:
        assignments.assignments[LEADER_TASK_ID] = assignment()
    application = FastAPI()
    application.include_router(api_router)
    application.state.container = StubContainer(
        ReadLeaderAssignment(assignments, tasks, StubDirectory(), StubModes(mode))
    )
    return TestClient(application)


def get(
    client: TestClient,
    *,
    token: str | None = LEADER_TOKEN,
    task_id: UUID = LEADER_TASK_ID,
):
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return client.get(f"/api/v1/agent-actions/leader/assignments/{task_id}", headers=headers)


def assert_refusal(response, *, status: int, code: str) -> None:
    """One refusal, checked against the frozen schema and the frozen matrix."""

    assert response.status_code == status
    body = response.json()
    validate_document(load(CONTRACT / "structured-error.schema.json"), body)
    assert body["detail"]["code"] == code
    matrix: dict[str, list[str]] = load(FIXTURES / "error-matrix.json")
    assert code in matrix[str(status)]
    # A message a Bridge may log: prose, and none of the caller's credential.
    assert body["detail"]["message"]
    assert LEADER_TOKEN not in body["detail"]["message"]


# ---------------------------------------------------------------------------
# 200 — the planning package
# ---------------------------------------------------------------------------


def test_the_planning_package_matches_the_frozen_schema(client: TestClient) -> None:
    response = get(client)

    assert response.status_code == 200
    body = response.json()
    validate_document(load(CONTRACT / "repository-assignment-package.schema.json"), body)
    assert body["schemaVersion"] == "repomesh.leader-actions.assignment-package.v1"
    assert body["phase"] == "planning"
    assert body["leaderTaskId"] == str(LEADER_TASK_ID)
    assert body["organizationId"] == str(ORGANIZATION_ID)
    assert body["projectId"] == str(PROJECT_ID)
    assert body["repositoryId"] == str(REPOSITORY_ID)


def test_the_package_carries_the_repository_task_and_the_roster(client: TestClient) -> None:
    body = get(client).json()

    assert body["repositoryTask"] == {
        "title": TITLE,
        "instruction": INSTRUCTION,
        # The row keeps criteria as separate lines; the wire spends one string.
        "acceptance": "\n".join(ACCEPTANCE),
    }
    assert body["workerRoster"] == [
        {
            "workerAgentId": str(WORKER_ID),
            "workerName": "pricing-codex-worker",
            "responsibilityPaths": ["src/pricing_core/"],
        }
    ]


def test_the_package_carries_the_envelope_it_was_parked_with(client: TestClient) -> None:
    body = get(client).json()

    assert body["safetyEnvelope"] == {
        "allowedPathRoots": ["src/pricing_core/", "tests/"],
        "testPaths": ["tests/"],
        "testCommands": ["python scripts/run_tests.py"],
    }


def test_planning_carries_no_review_evidence_and_no_authority(client: TestClient) -> None:
    """Frozen invariant 1, and the advisory object's whole point."""

    body = get(client).json()

    assert body["reviewEvidence"] is None
    assert body["advisoryContext"]["authoritative"] is False


def test_the_package_names_no_place_on_disk(client: TestClient) -> None:
    """Adjudication D-8: a leader coordinates over text, never over a workspace."""

    rendered = get(client).text

    assert "workspace" not in rendered.lower()
    assert "\\\\" not in rendered
    assert "/home/" not in rendered and "/var/" not in rendered


# ---------------------------------------------------------------------------
# The refusal matrix
# ---------------------------------------------------------------------------


def test_no_token_is_401_invalid_token(client: TestClient) -> None:
    assert_refusal(get(client, token=None), status=401, code="invalid_token")


def test_an_unknown_token_is_401_invalid_token(client: TestClient) -> None:
    assert_refusal(get(client, token="not-a-token"), status=401, code="invalid_token")


def test_a_token_map_that_cannot_be_parsed_authenticates_nobody(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A misconfigured deployment refuses every caller rather than admitting one.

    The frozen error enum has no code for "this server is misconfigured", so
    the caller is told the truth it can act on -- its credential did not
    authenticate -- and the operator is told the rest through the log. The same
    token authenticates in every other test here, so the refusal is the
    document's fault and not the credential's.
    """

    client = build_client(monkeypatch)
    monkeypatch.setenv("REPOMESH_RUNNER_WORKER_TOKENS", "{not json")
    get_settings.cache_clear()

    with caplog.at_level("ERROR", logger="repomesh.api.leader_actions"):
        assert_refusal(get(client), status=401, code="invalid_token")

    assert "REPOMESH_RUNNER_WORKER_TOKENS is not valid JSON" in caplog.text
    # The operator gets the diagnosis, never the document it came from.
    assert LEADER_TOKEN not in caplog.text


def test_a_worker_token_is_403_forbidden_role(client: TestClient) -> None:
    """AC-02's mirror: the leader surface is as closed to a Worker as
    ``start-worker-task`` is to a leader."""

    assert_refusal(get(client, token=WORKER_TOKEN), status=403, code="forbidden_role")


def test_another_leaders_task_id_is_403_forbidden_not_assignee(client: TestClient) -> None:
    """The forged-taskId case: a real credential, somebody else's assignment."""

    assert_refusal(
        get(client, token=OTHER_LEADER_TOKEN), status=403, code="forbidden_not_assignee"
    )


def test_an_unknown_task_id_is_404_assignment_not_found(client: TestClient) -> None:
    assert_refusal(get(client, task_id=uuid4()), status=404, code="assignment_not_found")


def test_a_task_with_no_parked_assignment_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """A server-mode leader task exists but is not an assignment this surface owns."""

    client = build_client(monkeypatch, with_assignment=False)

    assert_refusal(get(client), status=404, code="assignment_not_found")


def test_a_server_mode_team_is_409_decomposition_mode_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = build_client(monkeypatch, mode=TeamDecompositionMode.SERVER)

    assert_refusal(get(client), status=409, code="decomposition_mode_conflict")


# ---------------------------------------------------------------------------
# The mapping itself
# ---------------------------------------------------------------------------


def test_the_producers_status_table_is_the_frozen_matrix() -> None:
    """Every code, at the status the freeze gives it — not only the ones raised.

    The matrix is frozen as one table; a producer holding half of it could
    reuse a code under a different status with nothing to catch it.
    """

    matrix: dict[str, list[str]] = load(FIXTURES / "error-matrix.json")
    frozen = {code: int(status) for status, codes in matrix.items() for code in codes}
    assert {code.value: status for code, status in LEADER_ACTION_ERROR_STATUS.items()} == frozen


@pytest.mark.parametrize(
    ("fixture_name", "status", "code"),
    [
        ("error.401.invalid-token.json", 401, "invalid_token"),
        ("error.403.forbidden-not-assignee.json", 403, "forbidden_not_assignee"),
        ("error.404.assignment-not-found.json", 404, "assignment_not_found"),
    ],
)
def test_the_sample_error_fixtures_are_answerable_by_this_producer(
    fixture_name: str, status: int, code: str
) -> None:
    """The fixtures a Bridge writes its client against are shapes this server emits."""

    sample = load(FIXTURES / fixture_name)
    assert sample["detail"]["code"] == code
    assert LEADER_ACTION_ERROR_STATUS[
        next(
            member
            for member in LEADER_ACTION_ERROR_STATUS
            if member.value == sample["detail"]["code"]
        )
    ] == status
