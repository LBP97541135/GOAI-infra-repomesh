"""Worker-scoped runner credentials over HTTP (PR 5).

Until now every ``/runtime`` route took one global control token with no
subject, and the worker each request concerned was whatever the caller said it
was — a query parameter, a path segment, a body field. A Bridge running
out-of-cluster holds that token, so any Bridge could lease any worker's task,
report on any run and read any binding.

What is pinned here is the narrowing, one route at a time: with a worker's own
token the *credential* names the worker and the self-report is either ignored
(the lease) or checked against it (binding, start), and the managed Runner's
global token keeps behaving exactly as it did. The doubles are mounted the way
``test_external_worker_binding.py`` mounts its own, so nothing here reaches a
network or a database — these are assertions about the guard, not about the
store, and the store's half of the events guard is pinned against a real
database in ``tests/integrations/runner/test_gateway.py``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from integrations.agentteams.fakes import StubDirectory

from repomesh.api.router import api_router
from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalStatus,
    AgentPrincipalView,
    AgentRole,
)
from repomesh.modules.agent_runtime.ports.agent_team import TeamRuntimeRef, WorkerRuntimeRef
from repomesh.modules.agent_runtime.runner_store import RunnerGatewayForbidden
from repomesh.settings import get_settings

WORKER_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_WORKER_ID = UUID("22222222-2222-4222-8222-222222222222")
ORGANIZATION_ID = uuid4()
WORKER_NAME = "repomesh-worker-bridge"
CONTROL_TOKEN = "runner-secret"
WORKER_TOKEN = "worker-secret"
ACTION_TOKEN = "internal-secret"
WORKER_TOKENS = json.dumps({str(WORKER_ID): WORKER_TOKEN})
GLOBAL_HEADERS = {"Authorization": f"Bearer {CONTROL_TOKEN}"}
SCOPED_HEADERS = {"Authorization": f"Bearer {WORKER_TOKEN}"}
LEASE_URL = "/api/v1/runtime/runner-tasks/next"
EVENTS_URL = "/api/v1/runtime/runner-events"
START_URL = "/api/v1/agent-actions/start-worker-task"
RUN_ID = uuid4()
TASK_PAYLOAD: dict[str, object] = {"runId": str(RUN_ID), "adapterId": "codex"}


@pytest.fixture(autouse=True)
def _isolated_settings():
    """Settings are an ``lru_cache``, and this module rewrites their inputs.

    Cleared on the way out as well as in: leaving a cache built from this
    module's environment behind would hand the next test file a deployment it
    never configured.
    """

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class StubRunnerGateway:
    """Records what the router asked it for, and owns exactly one worker's runs.

    ``receive_event`` stands in for the real store's join from ``runId`` to the
    dispatch row: everything it holds belongs to ``owner``, so an authenticated
    worker that is anybody else gets the refusal the store raises.
    """

    def __init__(self, *, task: dict[str, object] | None = None, owner: UUID = WORKER_ID) -> None:
        self._task = task
        self._owner = owner
        self.leased_for: list[UUID | None] = []
        self.leased_with: list[tuple[frozenset[str] | None, frozenset[UUID]]] = []
        self.events: list[tuple[dict[str, object], UUID | None]] = []

    async def next_task(
        self,
        worker_agent_id: UUID | None,
        *,
        adapters: frozenset[str] | None = None,
        exclude_worker_ids: frozenset[UUID] = frozenset(),
    ) -> dict[str, object] | None:
        self.leased_for.append(worker_agent_id)
        self.leased_with.append((adapters, frozenset(exclude_worker_ids)))
        return self._task

    async def receive_event(
        self, event: dict[str, object], *, worker_agent_id: UUID | None = None
    ) -> bool:
        if worker_agent_id is not None and worker_agent_id != self._owner:
            raise RunnerGatewayForbidden("runner event belongs to another worker")
        self.events.append((event, worker_agent_id))
        return True


class StubControlPlane:
    """Answers the one external worker this module's binding tests read."""

    async def get_worker(self, name: str) -> WorkerRuntimeRef | None:
        return WorkerRuntimeRef(
            name=WORKER_NAME,
            phase="Ready",
            matrix_user_id="@repomesh-worker-bridge:matrix.local",
            room_id="!worker-bridge:matrix.local",
            team="repomesh-team-pricing",
            container_managed=False,
        )

    async def get_team(self, name: str) -> TeamRuntimeRef | None:
        return TeamRuntimeRef(
            name="repomesh-team-pricing",
            phase="Ready",
            team_room_id="!team-pricing:matrix.local",
            leader_room_id="!leader-pricing:matrix.local",
            leader_name="repomesh-leader-pricing",
            ready_workers=1,
            total_workers=1,
        )


class UnreachableControlPlane:
    """Reaching this is the failure: the guard runs before the use case."""

    async def get_worker(self, name: str) -> WorkerRuntimeRef | None:
        raise AssertionError("the binding read ran for a credential that does not own it")

    async def get_team(self, name: str) -> TeamRuntimeRef | None:
        raise AssertionError("the binding read ran for a credential that does not own it")


class StubWorkerExecution:
    def __init__(self) -> None:
        self.commands: list[object] = []

    async def execute(self, command: object) -> object:
        self.commands.append(command)
        return SimpleNamespace(
            task=SimpleNamespace(
                task_id=command.task_id,  # type: ignore[attr-defined]
                run_id=RUN_ID,
                workspace=SimpleNamespace(
                    workspace_id="ws-1", path="/srv/ws-1", base_sha="0" * 40
                ),
            ),
            status=SimpleNamespace(value="in_progress"),
        )


class StubContainer:
    def __init__(
        self,
        *,
        gateway: object | None = None,
        directory: StubDirectory | None = None,
        control_plane: object | None = None,
        worker_execution: object | None = None,
    ) -> None:
        self._gateway = gateway
        self.agent_directory = directory
        self._control_plane = control_plane
        self._worker_execution = worker_execution

    def runner_gateway(self) -> object | None:
        return self._gateway

    def external_worker_binding_control_plane(self) -> object | None:
        return self._control_plane

    def worker_execution_service(self) -> object | None:
        return self._worker_execution


def _worker_principal() -> AgentPrincipalView:
    return AgentPrincipalView(
        id=WORKER_ID,
        organization_id=ORGANIZATION_ID,
        role=AgentRole.WORKER,
        leader_agent_id=None,
        repository_id=None,
        responsibility_paths=(),
        agentteams_resource_name=WORKER_NAME,
        status=AgentPrincipalStatus.ACTIVE,
    )


def _client(
    monkeypatch,
    *,
    container: StubContainer,
    control_token: str = CONTROL_TOKEN,
    worker_tokens: str = WORKER_TOKENS,
    action_token: str = ACTION_TOKEN,
) -> TestClient:
    # Set rather than deleted even when empty: ``Settings`` also reads ``.env``,
    # and only a present-but-empty variable reliably means "unconfigured here".
    monkeypatch.setenv("REPOMESH_RUNNER_CONTROL_TOKEN", control_token)
    monkeypatch.setenv("REPOMESH_RUNNER_WORKER_TOKENS", worker_tokens)
    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", action_token)
    get_settings.cache_clear()
    application = FastAPI()
    application.include_router(api_router)
    application.state.container = container
    return TestClient(application)


def _binding_url(worker_agent_id: UUID) -> str:
    return f"/api/v1/runtime/external-workers/{worker_agent_id}/binding"


def _event(run_id: UUID = RUN_ID) -> dict[str, object]:
    """A Runner event exactly as the frozen wire schema has it: no worker id."""

    return {
        "schemaVersion": "runtime.v1",
        "eventId": str(uuid4()),
        "eventType": "runner.accepted",
        "runId": str(run_id),
        "sequence": 1,
        "payload": {},
    }


def _start_body(worker_agent_id: UUID) -> dict[str, str]:
    return {
        "task_id": str(uuid4()),
        "worker_agent_id": str(worker_agent_id),
        "adapter_id": "codex",
    }


# ---------------------------------------------------------------------------
# Lease: the credential decides whose queue, not the query string
# ---------------------------------------------------------------------------


def test_leasing_without_a_token_is_401(monkeypatch) -> None:
    client = _client(monkeypatch, container=StubContainer(gateway=StubRunnerGateway()))
    assert client.get(LEASE_URL).status_code == 401


def test_leasing_with_an_unknown_token_is_401(monkeypatch) -> None:
    client = _client(monkeypatch, container=StubContainer(gateway=StubRunnerGateway()))
    response = client.get(LEASE_URL, headers={"Authorization": "Bearer not-a-token"})
    assert response.status_code == 401


def test_the_global_token_still_leases_for_the_worker_it_names(monkeypatch) -> None:
    """The managed Runner's path: it says whose queue and is believed.

    The token has no subject, so there is nothing to check the parameter
    against — and the Runner leases for every managed worker. What it has to
    say, since the one-shot stack runs a Runner on it, is which adapters it
    serves.
    """

    gateway = StubRunnerGateway(task=TASK_PAYLOAD)
    client = _client(monkeypatch, container=StubContainer(gateway=gateway))

    named = client.get(
        LEASE_URL,
        params={"workerAgentId": str(OTHER_WORKER_ID), "adapter": "codex"},
        headers=GLOBAL_HEADERS,
    )
    unnamed = client.get(LEASE_URL, params={"adapter": "codex"}, headers=GLOBAL_HEADERS)

    assert named.status_code == 200
    assert named.json() == TASK_PAYLOAD
    assert unnamed.status_code == 200
    assert gateway.leased_for == [OTHER_WORKER_ID, None]


def test_a_subjectless_lease_must_name_its_adapters(monkeypatch) -> None:
    """No ``adapter`` on the control token is a 400, not a queue-wide lease.

    The compose Runner, every Bridge and the verifier drain one table. A
    subjectless poll that does not say what it can run would take whatever is
    oldest and fail it with ``binary_not_found`` in somebody else's lane, so
    the store is never asked.
    """

    gateway = StubRunnerGateway(task=TASK_PAYLOAD)
    client = _client(monkeypatch, container=StubContainer(gateway=gateway))

    bare = client.get(LEASE_URL, headers=GLOBAL_HEADERS)
    blank = client.get(LEASE_URL, params={"adapter": " , "}, headers=GLOBAL_HEADERS)

    assert bare.status_code == 400
    assert blank.status_code == 400
    assert gateway.leased_for == []


def test_a_subjectless_lease_is_narrowed_and_kept_off_external_members(monkeypatch) -> None:
    """What the store is asked for: the adapters named, minus the Bridges' queues.

    ``WORKER_ID`` holds a credential of its own, so it is a Bridge's worker and
    the managed Runner never drains it. Repeated and comma-separated ``adapter``
    values are one set.
    """

    gateway = StubRunnerGateway(task=TASK_PAYLOAD)
    client = _client(monkeypatch, container=StubContainer(gateway=gateway))

    response = client.get(
        LEASE_URL, params=[("adapter", "mock,codex"), ("adapter", "mock")], headers=GLOBAL_HEADERS
    )

    assert response.status_code == 200
    assert gateway.leased_with == [(frozenset({"mock", "codex"}), frozenset({WORKER_ID}))]


def test_a_subjectless_credential_may_not_name_an_external_members_queue(monkeypatch) -> None:
    gateway = StubRunnerGateway(task=TASK_PAYLOAD)
    client = _client(monkeypatch, container=StubContainer(gateway=gateway))

    response = client.get(
        LEASE_URL,
        params={"workerAgentId": str(WORKER_ID), "adapter": "codex"},
        headers=GLOBAL_HEADERS,
    )

    assert response.status_code == 403
    assert gateway.leased_for == []


def test_a_worker_token_may_narrow_its_own_queue_by_adapter(monkeypatch) -> None:
    """Optional for a Bridge: its credential already scopes the lease to one queue."""

    gateway = StubRunnerGateway()
    client = _client(monkeypatch, container=StubContainer(gateway=gateway))

    response = client.get(LEASE_URL, params={"adapter": "codex"}, headers=SCOPED_HEADERS)

    assert response.status_code == 204
    assert gateway.leased_with == [(frozenset({"codex"}), frozenset())]


def test_a_worker_token_leases_its_own_queue_without_saying_so(monkeypatch) -> None:
    """No ``workerAgentId``, and still a scoped lease.

    The pre-PR-5 shape of this call — no parameter — used to mean "any worker's
    next task". With a credential that names one worker it means that worker's,
    which is why the parameter is not required for the scoping to hold.
    """

    gateway = StubRunnerGateway()
    client = _client(monkeypatch, container=StubContainer(gateway=gateway))

    response = client.get(LEASE_URL, headers=SCOPED_HEADERS)

    assert response.status_code == 204
    assert gateway.leased_for == [WORKER_ID]
    # No adapter narrowing and no exclusions: the credential already pins the queue.
    assert gateway.leased_with == [(None, frozenset())]


def test_a_worker_token_may_not_lease_another_workers_queue(monkeypatch) -> None:
    gateway = StubRunnerGateway(task=TASK_PAYLOAD)
    client = _client(monkeypatch, container=StubContainer(gateway=gateway))

    response = client.get(
        LEASE_URL, params={"workerAgentId": str(OTHER_WORKER_ID)}, headers=SCOPED_HEADERS
    )

    assert response.status_code == 403
    assert gateway.leased_for == []


def test_a_worker_token_may_repeat_its_own_id(monkeypatch) -> None:
    """A Bridge that keeps sending the parameter is not punished for it."""

    gateway = StubRunnerGateway(task=TASK_PAYLOAD)
    client = _client(monkeypatch, container=StubContainer(gateway=gateway))

    response = client.get(
        LEASE_URL, params={"workerAgentId": str(WORKER_ID)}, headers=SCOPED_HEADERS
    )

    assert response.status_code == 200
    assert response.json() == TASK_PAYLOAD
    assert gateway.leased_for == [WORKER_ID]


# ---------------------------------------------------------------------------
# Events: ownership comes from the dispatch row, never from the body
# ---------------------------------------------------------------------------


def test_a_worker_token_may_not_report_on_another_workers_run(monkeypatch) -> None:
    """The store's refusal, and the status code it wears on the wire.

    409 would be the wrong answer here — nothing about the event conflicts with
    the run it names, the caller simply does not own it — and it is the code the
    neighbouring ``ValueError`` branch would have produced.
    """

    gateway = StubRunnerGateway(owner=OTHER_WORKER_ID)
    client = _client(monkeypatch, container=StubContainer(gateway=gateway))

    response = client.post(EVENTS_URL, json=_event(), headers=SCOPED_HEADERS)

    assert response.status_code == 403
    assert gateway.events == []


def test_a_worker_token_reports_on_its_own_run(monkeypatch) -> None:
    gateway = StubRunnerGateway()
    client = _client(monkeypatch, container=StubContainer(gateway=gateway))

    response = client.post(EVENTS_URL, json=_event(), headers=SCOPED_HEADERS)

    assert response.status_code == 202
    assert response.json() == {"accepted": True, "duplicate": False}
    assert [worker for _, worker in gateway.events] == [WORKER_ID]


def test_the_global_token_skips_the_ownership_guard(monkeypatch) -> None:
    """One Runner reports for every worker it manages, so there is nothing to own."""

    gateway = StubRunnerGateway(owner=OTHER_WORKER_ID)
    client = _client(monkeypatch, container=StubContainer(gateway=gateway))

    response = client.post(EVENTS_URL, json=_event(), headers=GLOBAL_HEADERS)

    assert response.status_code == 202
    assert [worker for _, worker in gateway.events] == [None]


# ---------------------------------------------------------------------------
# Binding: the path id is checked against the credential
# ---------------------------------------------------------------------------


def test_a_worker_token_may_not_read_another_workers_binding(monkeypatch) -> None:
    client = _client(
        monkeypatch,
        container=StubContainer(
            directory=StubDirectory(_worker_principal()),
            control_plane=UnreachableControlPlane(),
        ),
    )

    response = client.get(_binding_url(OTHER_WORKER_ID), headers=SCOPED_HEADERS)

    assert response.status_code == 403


def test_a_worker_token_reads_its_own_binding(monkeypatch) -> None:
    client = _client(
        monkeypatch,
        container=StubContainer(
            directory=StubDirectory(_worker_principal()), control_plane=StubControlPlane()
        ),
    )

    response = client.get(_binding_url(WORKER_ID), headers=SCOPED_HEADERS)

    assert response.status_code == 200
    assert response.json()["workerAgentId"] == str(WORKER_ID)


# ---------------------------------------------------------------------------
# Start: the body's worker id is a claim, and is checked against the credential
# ---------------------------------------------------------------------------


def test_the_agent_action_token_starts_any_workers_task(monkeypatch) -> None:
    service = StubWorkerExecution()
    client = _client(monkeypatch, container=StubContainer(worker_execution=service))

    response = client.post(
        START_URL,
        json=_start_body(OTHER_WORKER_ID),
        headers={"Authorization": f"Bearer {ACTION_TOKEN}"},
    )

    assert response.status_code == 202
    assert len(service.commands) == 1


def test_a_worker_token_starts_its_own_task(monkeypatch) -> None:
    service = StubWorkerExecution()
    client = _client(monkeypatch, container=StubContainer(worker_execution=service))

    response = client.post(START_URL, json=_start_body(WORKER_ID), headers=SCOPED_HEADERS)

    assert response.status_code == 202
    assert len(service.commands) == 1


def test_a_worker_token_may_not_start_another_workers_task(monkeypatch) -> None:
    service = StubWorkerExecution()
    client = _client(monkeypatch, container=StubContainer(worker_execution=service))

    response = client.post(START_URL, json=_start_body(OTHER_WORKER_ID), headers=SCOPED_HEADERS)

    assert response.status_code == 403
    assert service.commands == []


def test_starting_without_a_token_is_401(monkeypatch) -> None:
    service = StubWorkerExecution()
    client = _client(monkeypatch, container=StubContainer(worker_execution=service))

    response = client.post(START_URL, json=_start_body(WORKER_ID))

    assert response.status_code == 401
    assert service.commands == []


# ---------------------------------------------------------------------------
# Configuration: a broken credential document is a fault, not a verdict
# ---------------------------------------------------------------------------


def test_a_malformed_worker_token_document_is_503(monkeypatch) -> None:
    """Not 401.

    A deployment whose credential map does not parse cannot decide anything
    about this request, and answering 401 would send an operator to look at the
    Bridge's token instead of at their own env.
    """

    client = _client(
        monkeypatch,
        container=StubContainer(gateway=StubRunnerGateway()),
        worker_tokens="{not json",
    )

    response = client.get(LEASE_URL, headers=GLOBAL_HEADERS)

    assert response.status_code == 503


def test_a_worker_token_document_that_is_not_an_id_map_is_503(monkeypatch) -> None:
    client = _client(
        monkeypatch,
        container=StubContainer(gateway=StubRunnerGateway()),
        worker_tokens=json.dumps({"not-a-uuid": WORKER_TOKEN}),
    )

    response = client.get(LEASE_URL, headers=GLOBAL_HEADERS)

    assert response.status_code == 503


def test_no_configured_credential_of_either_kind_is_503(monkeypatch) -> None:
    client = _client(
        monkeypatch,
        container=StubContainer(gateway=StubRunnerGateway()),
        control_token="",
        worker_tokens="",
    )

    response = client.get(LEASE_URL, headers=SCOPED_HEADERS)

    assert response.status_code == 503
    assert response.json()["detail"] == "runner control token is not configured"


def test_worker_tokens_alone_are_a_configured_deployment(monkeypatch) -> None:
    """A deployment whose only Runners are Bridges never sets the global token."""

    gateway = StubRunnerGateway()
    client = _client(monkeypatch, container=StubContainer(gateway=gateway), control_token="")

    assert client.get(LEASE_URL, headers=GLOBAL_HEADERS).status_code == 401
    assert client.get(LEASE_URL, headers=SCOPED_HEADERS).status_code == 204
    assert gateway.leased_for == [WORKER_ID]


def test_worker_tokens_alone_also_authorize_starting_that_workers_task(monkeypatch) -> None:
    service = StubWorkerExecution()
    container = StubContainer(worker_execution=service)
    client = _client(monkeypatch, container=container, action_token="")

    response = client.post(START_URL, json=_start_body(WORKER_ID), headers=SCOPED_HEADERS)

    assert response.status_code == 202
    assert len(service.commands) == 1
