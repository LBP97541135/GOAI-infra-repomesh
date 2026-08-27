"""``GET /api/v1/runtime/external-workers/{id}/binding`` over HTTP (ADR 0004).

The endpoint is a translation table over a read: which refusal wears which
status code, and what the exact wire body looks like on success. The use
case behind it (``ResolveExternalWorkerBinding``) is exercised against a real
projection in ``tests/integrations/agentteams/test_runtime_projection.py``
and ``tests/contracts/test_agentteams_integration.py``; here the directory
and control plane are doubles, mounted the way
``tests/api/test_round_redispatch_endpoint.py`` mounts its service double, so
nothing here reaches a network or a database.

The 503 case is the regression pin for the fix: an unreachable AgentTeams
controller used to escape ``ResolveExternalWorkerBinding`` as the
integration's own ``AgentTeamsUnavailable`` and surface as an unhandled 500.
The adapter (``ExternalWorkerProjection.get_worker``/``get_team``) now
translates that into the module-owned ``WorkerControlPlaneUnavailable``, and
the router maps it to 503 -- the double raises the port exception directly,
which is exactly what the translated adapter hands the router in production.
"""

from __future__ import annotations

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
from repomesh.modules.agent_runtime.application.external_worker import (
    ResolveExternalWorkerBinding,
)
from repomesh.modules.agent_runtime.contracts import (
    ExternalWorkerBindingQuery,
    ExternalWorkerError,
)
from repomesh.modules.agent_runtime.ports.agent_team import (
    TeamRuntimeRef,
    WorkerControlPlaneUnavailable,
    WorkerRuntimeRef,
)
from repomesh.settings import get_settings

WORKER_ID = uuid4()
ORGANIZATION_ID = uuid4()
WORKER_NAME = "repomesh-worker-bridge"
HEADERS = {"Authorization": "Bearer runner-secret"}
FAULT_DETAIL = "psycopg.OperationalError: password authentication failed for user 'repomesh'"


class ControllerFault(RuntimeError):
    """Something the endpoint has no answer for.

    Not an ``ExternalWorkerError``: those are verdicts on the request and each
    has its status code. This is the third kind of failure — the read broke in
    a way nobody classified — and it carries a message of exactly the sort that
    must not reach a Bridge.
    """


class FaultyControlPlane:
    """A control plane whose reads raise something unclassified."""

    async def get_worker(self, name: str) -> WorkerRuntimeRef | None:
        raise ControllerFault(FAULT_DETAIL)

    async def get_team(self, name: str) -> TeamRuntimeRef | None:
        raise ControllerFault(FAULT_DETAIL)


class StubControlPlane:
    """Stands in for the translated adapter.

    Raises the port exception it already produced (see module docstring), or
    answers a worker/team read.
    """

    def __init__(
        self,
        *,
        worker: WorkerRuntimeRef | None = None,
        team: TeamRuntimeRef | None = None,
        unavailable: bool = False,
    ) -> None:
        self._worker = worker
        self._team = team
        self._unavailable = unavailable

    async def get_worker(self, name: str) -> WorkerRuntimeRef | None:
        if self._unavailable:
            raise WorkerControlPlaneUnavailable(
                f"AgentTeams request failed: GET /api/v1/workers/{name}"
            )
        return self._worker

    async def get_team(self, name: str) -> TeamRuntimeRef | None:
        if self._unavailable:
            raise WorkerControlPlaneUnavailable(
                f"AgentTeams request failed: GET /api/v1/teams/{name}"
            )
        return self._team


class StubContainer:
    def __init__(self, *, directory: StubDirectory, control_plane: object | None) -> None:
        self.agent_directory = directory
        self._control_plane = control_plane

    def external_worker_binding_control_plane(self) -> object | None:
        return self._control_plane


def _worker_principal(
    *,
    role: AgentRole = AgentRole.WORKER,
    status: AgentPrincipalStatus = AgentPrincipalStatus.ACTIVE,
) -> AgentPrincipalView:
    return AgentPrincipalView(
        id=WORKER_ID,
        organization_id=ORGANIZATION_ID,
        role=role,
        leader_agent_id=None,
        repository_id=None,
        responsibility_paths=(),
        agentteams_resource_name=WORKER_NAME,
        status=status,
    )


def _client(
    *,
    directory: StubDirectory,
    control_plane: object | None,
    monkeypatch,
    raise_server_exceptions: bool = True,
) -> TestClient:
    monkeypatch.setenv("REPOMESH_RUNNER_CONTROL_TOKEN", "runner-secret")
    get_settings.cache_clear()
    application = FastAPI()
    application.include_router(api_router)
    application.state.container = StubContainer(directory=directory, control_plane=control_plane)
    # ``raise_server_exceptions=False`` is how a test sees the response a real
    # deployment sends for an unhandled exception; the default re-raises it in
    # the test process instead, which cannot answer "what did the Bridge get".
    return TestClient(application, raise_server_exceptions=raise_server_exceptions)


def _get(
    client: TestClient,
    *,
    headers: dict[str, str] | None = HEADERS,
    worker_id: UUID = WORKER_ID,
):
    return client.get(
        f"/api/v1/runtime/external-workers/{worker_id}/binding",
        headers=headers,
    )


def test_missing_token_is_401(monkeypatch) -> None:
    client = _client(
        directory=StubDirectory(_worker_principal()),
        control_plane=StubControlPlane(),
        monkeypatch=monkeypatch,
    )
    response = _get(client, headers=None)
    assert response.status_code == 401


def test_wrong_token_is_401(monkeypatch) -> None:
    client = _client(
        directory=StubDirectory(_worker_principal()),
        control_plane=StubControlPlane(),
        monkeypatch=monkeypatch,
    )
    response = _get(client, headers={"Authorization": "Bearer not-the-token"})
    assert response.status_code == 401


def test_unknown_worker_agent_id_is_404(monkeypatch) -> None:
    client = _client(
        directory=StubDirectory(),
        control_plane=StubControlPlane(),
        monkeypatch=monkeypatch,
    )
    response = _get(client)
    assert response.status_code == 404


def test_a_managed_worker_is_refused_as_409(monkeypatch) -> None:
    """The controller still owns this worker's container -- no binding."""

    client = _client(
        directory=StubDirectory(_worker_principal()),
        control_plane=StubControlPlane(
            worker=WorkerRuntimeRef(
                name=WORKER_NAME,
                phase="Ready",
                matrix_user_id="@repomesh-worker-bridge:matrix.local",
                team="repomesh-team-pricing",
                container_managed=True,
            )
        ),
        monkeypatch=monkeypatch,
    )
    response = _get(client)
    assert response.status_code == 409


def test_an_unreachable_control_plane_is_503(monkeypatch) -> None:
    """Regression pin: this used to escape as an unhandled 500."""

    client = _client(
        directory=StubDirectory(_worker_principal()),
        control_plane=StubControlPlane(unavailable=True),
        monkeypatch=monkeypatch,
    )
    response = _get(client)
    assert response.status_code == 503
    assert "AgentTeams request failed" in response.json()["detail"]


def test_an_unconfigured_control_plane_is_503(monkeypatch) -> None:
    client = _client(
        directory=StubDirectory(_worker_principal()),
        control_plane=None,
        monkeypatch=monkeypatch,
    )
    response = _get(client)
    assert response.status_code == 503
    assert response.json()["detail"] == "AgentTeams control plane is not configured"


def test_a_confirmed_binding_is_200_with_the_exact_wire_body(monkeypatch) -> None:
    client = _client(
        directory=StubDirectory(_worker_principal()),
        control_plane=StubControlPlane(
            worker=WorkerRuntimeRef(
                name=WORKER_NAME,
                phase="Ready",
                matrix_user_id="@repomesh-worker-bridge:matrix.local",
                room_id="!worker-bridge:matrix.local",
                team="repomesh-team-pricing",
                container_managed=False,
            ),
            team=TeamRuntimeRef(
                name="repomesh-team-pricing",
                phase="Ready",
                team_room_id="!team-pricing:matrix.local",
                leader_room_id="!leader-pricing:matrix.local",
                leader_name="repomesh-leader-pricing",
                ready_workers=1,
                total_workers=1,
            ),
        ),
        monkeypatch=monkeypatch,
    )
    response = _get(client)

    assert response.status_code == 200
    assert response.json() == {
        "schemaVersion": "repomesh.agent-bridge.binding.v1",
        "organizationId": str(ORGANIZATION_ID),
        "teamName": "repomesh-team-pricing",
        "workerAgentId": str(WORKER_ID),
        "workerName": WORKER_NAME,
        "matrixUserId": "@repomesh-worker-bridge:matrix.local",
        "allowedRoomIds": [
            "!team-pricing:matrix.local",
            "!worker-bridge:matrix.local",
        ],
        "containerManaged": False,
    }


# ---------------------------------------------------------------------------
# A fault is not a verdict: 500, and it says nothing else (PR 1 Minor)
# ---------------------------------------------------------------------------


def test_an_unclassified_control_plane_fault_is_an_untranslated_500(monkeypatch) -> None:
    """The fourth outcome, pinned so it cannot drift into one of the other three.

    404, 409 and 503 are answers about the request: this agent is unknown, this
    binding does not add up, the controller did not respond. A fault is none of
    them — something inside RepoMesh broke — and translating it would tell an
    operator to go and fix a worker that is fine, or a Bridge to retry
    something no retry will fix. The refusals are enumerated in the router; the
    absence of a bare ``except Exception`` beside them is the contract, and
    this is what holds it.

    The body matters as much as the code. This endpoint answers a process that
    holds no AgentTeams credential, so an internal message reaching it is both
    a confusing answer and a leak.
    """

    client = _client(
        directory=StubDirectory(_worker_principal()),
        control_plane=FaultyControlPlane(),
        monkeypatch=monkeypatch,
        raise_server_exceptions=False,
    )
    response = _get(client)

    assert response.status_code == 500
    assert FAULT_DETAIL not in response.text
    assert "ControllerFault" not in response.text
    assert "Traceback" not in response.text


async def test_the_use_case_lets_an_unclassified_fault_through_unchanged() -> None:
    """The other half of the pin, one layer down.

    The router can only decline to translate what reaches it unchanged, so the
    use case has to be fail-*loud* here rather than fail-closed: an unexpected
    exception is not an ``ExternalWorkerRefused``, and turning it into one
    would make a 409 out of a broken read — a refusal nobody can act on.
    """

    with pytest.raises(ControllerFault) as raised:
        await ResolveExternalWorkerBinding(
            StubDirectory(_worker_principal()), FaultyControlPlane()
        ).execute(ExternalWorkerBindingQuery(worker_agent_id=WORKER_ID))

    assert not isinstance(raised.value, ExternalWorkerError)
    assert str(raised.value) == FAULT_DETAIL
