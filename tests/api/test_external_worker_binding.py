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

from fastapi import FastAPI
from fastapi.testclient import TestClient

from repomesh.api.router import api_router
from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalStatus,
    AgentPrincipalView,
    AgentRole,
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


class StubDirectory:
    def __init__(self, views: dict[UUID, AgentPrincipalView]) -> None:
        self._views = views

    async def get_view(self, agent_id: UUID) -> AgentPrincipalView | None:
        return self._views.get(agent_id)

    async def list_views(self) -> tuple[AgentPrincipalView, ...]:
        return tuple(self._views.values())


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


def _client(*, directory: StubDirectory, control_plane: object | None, monkeypatch) -> TestClient:
    monkeypatch.setenv("REPOMESH_RUNNER_CONTROL_TOKEN", "runner-secret")
    get_settings.cache_clear()
    application = FastAPI()
    application.include_router(api_router)
    application.state.container = StubContainer(directory=directory, control_plane=control_plane)
    return TestClient(application)


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
        directory=StubDirectory({WORKER_ID: _worker_principal()}),
        control_plane=StubControlPlane(),
        monkeypatch=monkeypatch,
    )
    response = _get(client, headers=None)
    assert response.status_code == 401


def test_wrong_token_is_401(monkeypatch) -> None:
    client = _client(
        directory=StubDirectory({WORKER_ID: _worker_principal()}),
        control_plane=StubControlPlane(),
        monkeypatch=monkeypatch,
    )
    response = _get(client, headers={"Authorization": "Bearer not-the-token"})
    assert response.status_code == 401


def test_unknown_worker_agent_id_is_404(monkeypatch) -> None:
    client = _client(
        directory=StubDirectory({}),
        control_plane=StubControlPlane(),
        monkeypatch=monkeypatch,
    )
    response = _get(client)
    assert response.status_code == 404


def test_a_managed_worker_is_refused_as_409(monkeypatch) -> None:
    """The controller still owns this worker's container -- no binding."""

    client = _client(
        directory=StubDirectory({WORKER_ID: _worker_principal()}),
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
        directory=StubDirectory({WORKER_ID: _worker_principal()}),
        control_plane=StubControlPlane(unavailable=True),
        monkeypatch=monkeypatch,
    )
    response = _get(client)
    assert response.status_code == 503
    assert "AgentTeams request failed" in response.json()["detail"]


def test_an_unconfigured_control_plane_is_503(monkeypatch) -> None:
    client = _client(
        directory=StubDirectory({WORKER_ID: _worker_principal()}),
        control_plane=None,
        monkeypatch=monkeypatch,
    )
    response = _get(client)
    assert response.status_code == 503
    assert response.json()["detail"] == "AgentTeams control plane is not configured"


def test_a_confirmed_binding_is_200_with_the_exact_wire_body(monkeypatch) -> None:
    client = _client(
        directory=StubDirectory({WORKER_ID: _worker_principal()}),
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
