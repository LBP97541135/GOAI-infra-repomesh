"""``GET /api/v1/runtime/v2/external-members/{id}/binding`` over HTTP (PR 5.5A).

The v2 preflight, and the reason it is a sibling route rather than a widened
one: v1's document cannot say which role it described, so "this endpoint now
also answers about leaders" is not a compatible change to it. Both live, and
the two are pinned against each other here — the same leader principal is a 409
on v1 and a 200 on v2, by design.

The success cases are driven straight from the frozen fixtures under
``contracts/agent-bridge/v2/fixtures``: the enrollment fixture supplies the
identity the doubles are built from, and the response is asserted equal to the
binding fixture, key for key. Nothing in this file hand-writes a binding
document, so a server that started answering something else fails against the
contract rather than against a copy of it.

The room assertions are the point of the whole PR. A worker is given the Team
room and its own DM; a repository leader is given the Team room and the leader
DM — and each double deliberately carries the *other* role's DM room somewhere
in reach, so "the worker DM is absent from a leader's allowlist" is a fact the
test could have caught being wrong.

Doubles are mounted the way ``test_external_worker_binding.py`` mounts its own,
so nothing here reaches a network or a database.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

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
from repomesh.modules.agent_runtime.ports.agent_team import (
    TeamRuntimeRef,
    WorkerControlPlaneUnavailable,
    WorkerRuntimeRef,
)
from repomesh.settings import get_settings

FIXTURES = Path(__file__).parents[2] / "contracts" / "agent-bridge" / "v2" / "fixtures"

CONTROL_TOKEN = "runner-secret"
HEADERS = {"Authorization": f"Bearer {CONTROL_TOKEN}"}
FAULT_DETAIL = "psycopg.OperationalError: password authentication failed for user 'repomesh'"

#: The malformed room the invalid fixture carries, kept as one value so the
#: server-side refusal is pinned to the same string the contract publishes.
MALFORMED_ROOM = "not-a-matrix-room-id"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _isolated_settings():
    """Settings are an ``lru_cache`` and this module rewrites their inputs."""

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class ControllerFault(RuntimeError):
    """The failure nobody classified, carrying a message that must not escape."""


class StubControlPlane:
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


class FaultyControlPlane:
    async def get_worker(self, name: str) -> WorkerRuntimeRef | None:
        raise ControllerFault(FAULT_DETAIL)

    async def get_team(self, name: str) -> TeamRuntimeRef | None:
        raise ControllerFault(FAULT_DETAIL)


class StubContainer:
    def __init__(self, *, directory: StubDirectory, control_plane: object | None) -> None:
        self.agent_directory = directory
        self._control_plane = control_plane

    def external_worker_binding_control_plane(self) -> object | None:
        return self._control_plane


# ---------------------------------------------------------------------------
# The two members the fixtures describe, built from the fixtures themselves
# ---------------------------------------------------------------------------


def _principal(
    enrollment: dict[str, Any],
    *,
    role: AgentRole,
    status: AgentPrincipalStatus = AgentPrincipalStatus.ACTIVE,
) -> AgentPrincipalView:
    return AgentPrincipalView(
        id=UUID(enrollment["workerAgentId"]),
        organization_id=UUID(enrollment["organizationId"]),
        role=role,
        leader_agent_id=None,
        repository_id=None,
        responsibility_paths=(),
        agentteams_resource_name=enrollment["workerName"],
        status=status,
    )


def _worker_ref(
    enrollment: dict[str, Any],
    *,
    room_id: str | None,
    name: str | None = None,
    container_managed: bool | None = False,
    team: str | None = None,
) -> WorkerRuntimeRef:
    return WorkerRuntimeRef(
        name=enrollment["workerName"] if name is None else name,
        phase="Ready",
        room_id=room_id,
        matrix_user_id=enrollment["matrixUserId"],
        team=enrollment["teamName"] if team is None else team,
        container_managed=container_managed,
    )


def _team_ref(
    enrollment: dict[str, Any],
    *,
    leader_name: str,
    team_room_id: str | None,
    leader_room_id: str | None,
) -> TeamRuntimeRef:
    return TeamRuntimeRef(
        name=enrollment["teamName"],
        phase="Ready",
        team_room_id=team_room_id,
        leader_room_id=leader_room_id,
        leader_name=leader_name,
        ready_workers=1,
        total_workers=1,
    )


WORKER_ENROLLMENT = fixture("enrollment.worker.json")
LEADER_ENROLLMENT = fixture("enrollment.repository-leader.json")
WORKER_ID = UUID(WORKER_ENROLLMENT["workerAgentId"])
LEADER_ID = UUID(LEADER_ENROLLMENT["workerAgentId"])
TEAM_ROOM = "!team-pricing:matrix.example.org"
WORKER_DM = "!dm-pricing-worker:matrix.example.org"
LEADER_DM = "!dm-pricing-leader:matrix.example.org"
LEADER_NAME = LEADER_ENROLLMENT["workerName"]


def _worker_scene() -> tuple[StubDirectory, StubControlPlane]:
    """A worker whose Team also has a leader DM room — which it must not be given."""

    return (
        StubDirectory(_principal(WORKER_ENROLLMENT, role=AgentRole.WORKER)),
        StubControlPlane(
            worker=_worker_ref(WORKER_ENROLLMENT, room_id=WORKER_DM),
            team=_team_ref(
                WORKER_ENROLLMENT,
                leader_name=LEADER_NAME,
                team_room_id=TEAM_ROOM,
                leader_room_id=LEADER_DM,
            ),
        ),
    )


def _leader_scene(
    *,
    leader_room_id: str | None = LEADER_DM,
    team_room_id: str | None = TEAM_ROOM,
    leader_name: str = LEADER_NAME,
) -> tuple[StubDirectory, StubControlPlane]:
    """A leader whose *own worker document* carries the worker DM room.

    Deliberately: the controller publishes a ``roomID`` for every worker
    resource, and a leader is projected as one. If the allowlist were still
    built from that field the leader would silently be handed the worker's DM,
    and the assertion below would fail — which is what makes it worth making.
    """

    return (
        StubDirectory(_principal(LEADER_ENROLLMENT, role=AgentRole.REPOSITORY_LEADER)),
        StubControlPlane(
            worker=_worker_ref(LEADER_ENROLLMENT, room_id=WORKER_DM),
            team=_team_ref(
                LEADER_ENROLLMENT,
                leader_name=leader_name,
                team_room_id=team_room_id,
                leader_room_id=leader_room_id,
            ),
        ),
    )


def _client(
    *,
    directory: StubDirectory,
    control_plane: object | None,
    monkeypatch,
    raise_server_exceptions: bool = True,
) -> TestClient:
    # Set rather than deleted: ``Settings`` also reads ``.env``, and only a
    # present-but-empty variable reliably means "unconfigured here".
    monkeypatch.setenv("REPOMESH_RUNNER_CONTROL_TOKEN", CONTROL_TOKEN)
    monkeypatch.setenv("REPOMESH_RUNNER_WORKER_TOKENS", "")
    get_settings.cache_clear()
    application = FastAPI()
    application.include_router(api_router)
    application.state.container = StubContainer(directory=directory, control_plane=control_plane)
    return TestClient(application, raise_server_exceptions=raise_server_exceptions)


def _get(
    client: TestClient,
    *,
    member_id: UUID,
    role: str | None,
    headers: dict[str, str] | None = HEADERS,
):
    return client.get(
        f"/api/v1/runtime/v2/external-members/{member_id}/binding",
        params=None if role is None else {"role": role},
        headers=headers,
    )


# ---------------------------------------------------------------------------
# The two bindings, asserted equal to the frozen fixtures
# ---------------------------------------------------------------------------


def test_a_repository_leader_binds_to_the_canonical_v2_document(monkeypatch) -> None:
    """Acceptance 1: a Repository Leader gets a v2 binding, Team room + leader DM."""

    directory, control_plane = _leader_scene()
    response = _get(
        _client(directory=directory, control_plane=control_plane, monkeypatch=monkeypatch),
        member_id=LEADER_ID,
        role="repository_leader",
    )

    assert response.status_code == 200
    assert response.json() == fixture("binding.repository-leader.json")


def test_a_repository_leader_is_never_handed_the_worker_dm(monkeypatch) -> None:
    """The same answer, stated as the property rather than as a document."""

    directory, control_plane = _leader_scene()
    body = _get(
        _client(directory=directory, control_plane=control_plane, monkeypatch=monkeypatch),
        member_id=LEADER_ID,
        role="repository_leader",
    ).json()

    assert body["role"] == "repository_leader"
    assert body["allowedRoomIds"] == [TEAM_ROOM, LEADER_DM]
    assert WORKER_DM not in body["allowedRoomIds"]


def test_a_worker_binds_to_the_canonical_v2_document(monkeypatch) -> None:
    """Acceptance 2: v2 answers a worker too, and answers it v1's rooms."""

    directory, control_plane = _worker_scene()
    response = _get(
        _client(directory=directory, control_plane=control_plane, monkeypatch=monkeypatch),
        member_id=WORKER_ID,
        role="worker",
    )

    assert response.status_code == 200
    assert response.json() == fixture("binding.worker.json")


def test_a_worker_is_never_handed_the_leader_dm(monkeypatch) -> None:
    directory, control_plane = _worker_scene()
    body = _get(
        _client(directory=directory, control_plane=control_plane, monkeypatch=monkeypatch),
        member_id=WORKER_ID,
        role="worker",
    ).json()

    assert body["role"] == "worker"
    assert body["allowedRoomIds"] == [TEAM_ROOM, WORKER_DM]
    assert LEADER_DM not in body["allowedRoomIds"]


def test_the_v2_body_is_the_v1_body_plus_role(monkeypatch) -> None:
    """The drift guard the contract test makes about the schemas, on the wire."""

    worker_directory, worker_control_plane = _worker_scene()
    v2 = _get(
        _client(
            directory=worker_directory, control_plane=worker_control_plane, monkeypatch=monkeypatch
        ),
        member_id=WORKER_ID,
        role="worker",
    ).json()

    worker_directory, worker_control_plane = _worker_scene()
    v1 = (
        _client(
            directory=worker_directory, control_plane=worker_control_plane, monkeypatch=monkeypatch
        )
        .get(f"/api/v1/runtime/external-workers/{WORKER_ID}/binding", headers=HEADERS)
        .json()
    )

    assert v1["schemaVersion"] == "repomesh.agent-bridge.binding.v1"
    assert "role" not in v1
    assert set(v2) - set(v1) == {"role"}
    assert {key: value for key, value in v2.items() if key not in {"role", "schemaVersion"}} == {
        key: value for key, value in v1.items() if key != "schemaVersion"
    }


# ---------------------------------------------------------------------------
# Roles: who may be an external member, and who says so
# ---------------------------------------------------------------------------


def test_an_organization_leader_principal_is_409(monkeypatch) -> None:
    """Acceptance 3: the Organization Leader stays on the AgentTeams Manager."""

    rogue = fixture("enrollment.invalid-role.organization-leader.json")
    directory = StubDirectory(_principal(rogue, role=AgentRole.ORGANIZATION_LEADER))
    response = _get(
        _client(directory=directory, control_plane=StubControlPlane(), monkeypatch=monkeypatch),
        member_id=UUID(rogue["workerAgentId"]),
        role="worker",
    )

    assert response.status_code == 409
    assert "organization_leader" in response.json()["detail"]


def test_the_organization_leader_fixtures_own_role_is_409(monkeypatch) -> None:
    """The invalid fixture consumed as what it is: a role no binding may carry.

    ``enrollment.invalid-role.organization-leader.json`` is load-bearing per the
    v2 README, and this is the server-side half of it — the role string the
    fixture actually contains, presented by a Bridge, is refused rather than
    rejected as an unparseable enum. 409 and not 422: RepoMesh understood the
    question and said no.
    """

    rogue = fixture("enrollment.invalid-role.organization-leader.json")
    directory, control_plane = _leader_scene()
    response = _get(
        _client(directory=directory, control_plane=control_plane, monkeypatch=monkeypatch),
        member_id=LEADER_ID,
        role=rogue["role"],
    )

    assert rogue["role"] == "organization_leader"
    assert response.status_code == 409
    assert "repository_leader" in response.json()["detail"]


@pytest.mark.parametrize(
    ("scene", "member_id", "claimed"),
    [
        ("leader", "leader", "worker"),
        ("worker", "worker", "repository_leader"),
    ],
)
def test_an_enrollment_that_claims_the_other_role_is_409(
    scene: str, member_id: str, claimed: str, monkeypatch
) -> None:
    """Acceptance 4: the directory is the truth, the enrollment is a claim.

    Both directions, because the two failures are different in consequence: a
    leader that enrolled as a worker would try to enter the Runner execution
    path, and a worker that enrolled as a leader would wait for decisions
    nobody will ask it for.
    """

    directory, control_plane = _leader_scene() if scene == "leader" else _worker_scene()
    response = _get(
        _client(directory=directory, control_plane=control_plane, monkeypatch=monkeypatch),
        member_id=LEADER_ID if member_id == "leader" else WORKER_ID,
        role=claimed,
    )

    assert response.status_code == 409
    assert "the enrollment claims" in response.json()["detail"]


def test_an_unparseable_role_is_409_not_422(monkeypatch) -> None:
    directory, control_plane = _worker_scene()
    response = _get(
        _client(directory=directory, control_plane=control_plane, monkeypatch=monkeypatch),
        member_id=WORKER_ID,
        role="team_leader",
    )

    assert response.status_code == 409


def test_a_missing_role_is_refused_by_the_framework(monkeypatch) -> None:
    """Required, so that the mismatch check is not one a caller may decline."""

    directory, control_plane = _worker_scene()
    response = _get(
        _client(directory=directory, control_plane=control_plane, monkeypatch=monkeypatch),
        member_id=WORKER_ID,
        role=None,
    )

    assert response.status_code == 422


def test_the_v1_route_still_refuses_the_leader_the_v2_route_binds(monkeypatch) -> None:
    """The compatibility statement, as an assertion rather than a promise.

    Same principal, same doubles, two routes: v1 answers what it has always
    answered (409 — its document cannot describe a leader), and v2 answers 200.
    A future change that "generalised" the v1 path would fail here.
    """

    directory, control_plane = _leader_scene()
    client = _client(directory=directory, control_plane=control_plane, monkeypatch=monkeypatch)

    v1 = client.get(f"/api/v1/runtime/external-workers/{LEADER_ID}/binding", headers=HEADERS)
    v2 = _get(client, member_id=LEADER_ID, role="repository_leader")

    assert v1.status_code == 409
    assert v2.status_code == 200


# ---------------------------------------------------------------------------
# The refusals that are about the controller's answer, not the role
# ---------------------------------------------------------------------------


def test_an_unknown_principal_is_404(monkeypatch) -> None:
    response = _get(
        _client(
            directory=StubDirectory(), control_plane=StubControlPlane(), monkeypatch=monkeypatch
        ),
        member_id=LEADER_ID,
        role="repository_leader",
    )
    assert response.status_code == 404


def test_a_disabled_principal_is_409(monkeypatch) -> None:
    directory = StubDirectory(
        _principal(
            LEADER_ENROLLMENT,
            role=AgentRole.REPOSITORY_LEADER,
            status=AgentPrincipalStatus.DISABLED,
        )
    )
    response = _get(
        _client(directory=directory, control_plane=StubControlPlane(), monkeypatch=monkeypatch),
        member_id=LEADER_ID,
        role="repository_leader",
    )
    assert response.status_code == 409


def test_a_managed_member_is_409(monkeypatch) -> None:
    directory = StubDirectory(_principal(LEADER_ENROLLMENT, role=AgentRole.REPOSITORY_LEADER))
    control_plane = StubControlPlane(
        worker=_worker_ref(LEADER_ENROLLMENT, room_id=None, container_managed=True),
        team=_team_ref(
            LEADER_ENROLLMENT,
            leader_name=LEADER_NAME,
            team_room_id=TEAM_ROOM,
            leader_room_id=LEADER_DM,
        ),
    )
    response = _get(
        _client(directory=directory, control_plane=control_plane, monkeypatch=monkeypatch),
        member_id=LEADER_ID,
        role="repository_leader",
    )
    assert response.status_code == 409


def test_a_controller_answering_about_another_resource_is_409(monkeypatch) -> None:
    """Identity mismatch, v1's check, still made on the v2 path."""

    directory = StubDirectory(_principal(LEADER_ENROLLMENT, role=AgentRole.REPOSITORY_LEADER))
    control_plane = StubControlPlane(
        worker=_worker_ref(LEADER_ENROLLMENT, room_id=None, name="somebody-else"),
        team=_team_ref(
            LEADER_ENROLLMENT,
            leader_name=LEADER_NAME,
            team_room_id=TEAM_ROOM,
            leader_room_id=LEADER_DM,
        ),
    )
    response = _get(
        _client(directory=directory, control_plane=control_plane, monkeypatch=monkeypatch),
        member_id=LEADER_ID,
        role="repository_leader",
    )
    assert response.status_code == 409
    assert "different worker name" in response.json()["detail"]


def test_a_team_led_by_somebody_else_is_409(monkeypatch) -> None:
    """The leader DM belongs to whoever leads the Team, so who that is matters.

    Without this check a repository leader whose Team is led by another agent
    would be handed that agent's DM room in its allowlist — a room it may then
    read from and post in.
    """

    directory, control_plane = _leader_scene(leader_name="pricing-codex-someone-else")
    response = _get(
        _client(directory=directory, control_plane=control_plane, monkeypatch=monkeypatch),
        member_id=LEADER_ID,
        role="repository_leader",
    )

    assert response.status_code == 409
    assert "is led by" in response.json()["detail"]


def test_a_leader_without_a_dm_room_is_409(monkeypatch) -> None:
    """A leader with no DM room cannot be given work, so it is not bound."""

    directory, control_plane = _leader_scene(leader_room_id=None)
    response = _get(
        _client(directory=directory, control_plane=control_plane, monkeypatch=monkeypatch),
        member_id=LEADER_ID,
        role="repository_leader",
    )
    assert response.status_code == 409


def test_a_malformed_room_from_the_controller_is_refused(monkeypatch) -> None:
    """The invalid binding fixture's room, refused at the endpoint that would emit it.

    ``binding.invalid-room.malformed-room-id.json`` is the other load-bearing
    fixture: a controller answering with something that is not a Matrix room id
    must not become a binding, because the allowlist is what a Bridge will
    accept work from.
    """

    invalid = fixture("binding.invalid-room.malformed-room-id.json")
    assert invalid["allowedRoomIds"] == [MALFORMED_ROOM]

    directory, control_plane = _leader_scene(team_room_id=MALFORMED_ROOM)
    response = _get(
        _client(directory=directory, control_plane=control_plane, monkeypatch=monkeypatch),
        member_id=LEADER_ID,
        role="repository_leader",
    )

    assert response.status_code == 409
    assert MALFORMED_ROOM in response.json()["detail"]


# ---------------------------------------------------------------------------
# The guard and the three non-verdicts, mirroring the v1 endpoint's table
# ---------------------------------------------------------------------------


def test_missing_token_is_401(monkeypatch) -> None:
    directory, control_plane = _leader_scene()
    response = _get(
        _client(directory=directory, control_plane=control_plane, monkeypatch=monkeypatch),
        member_id=LEADER_ID,
        role="repository_leader",
        headers=None,
    )
    assert response.status_code == 401


def test_a_member_credential_may_not_read_another_members_binding(monkeypatch) -> None:
    """The credential names one member; the path is the caller's claim about it."""

    directory, control_plane = _leader_scene()
    monkeypatch.setenv("REPOMESH_RUNNER_CONTROL_TOKEN", "")
    monkeypatch.setenv(
        "REPOMESH_RUNNER_WORKER_TOKENS", json.dumps({str(WORKER_ID): "member-secret"})
    )
    get_settings.cache_clear()
    application = FastAPI()
    application.include_router(api_router)
    application.state.container = StubContainer(directory=directory, control_plane=control_plane)
    client = TestClient(application)

    response = client.get(
        f"/api/v1/runtime/v2/external-members/{LEADER_ID}/binding",
        params={"role": "repository_leader"},
        headers={"Authorization": "Bearer member-secret"},
    )

    assert response.status_code == 403


def test_an_unconfigured_control_plane_is_503(monkeypatch) -> None:
    directory, _ = _leader_scene()
    response = _get(
        _client(directory=directory, control_plane=None, monkeypatch=monkeypatch),
        member_id=LEADER_ID,
        role="repository_leader",
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "AgentTeams control plane is not configured"


def test_an_unreachable_control_plane_is_503(monkeypatch) -> None:
    directory, _ = _leader_scene()
    response = _get(
        _client(
            directory=directory,
            control_plane=StubControlPlane(unavailable=True),
            monkeypatch=monkeypatch,
        ),
        member_id=LEADER_ID,
        role="repository_leader",
    )
    assert response.status_code == 503
    assert "AgentTeams request failed" in response.json()["detail"]


def test_an_unclassified_fault_is_an_untranslated_500(monkeypatch) -> None:
    """A fault is not a verdict, and it says nothing else on the way out."""

    directory, _ = _leader_scene()
    response = _get(
        _client(
            directory=directory,
            control_plane=FaultyControlPlane(),
            monkeypatch=monkeypatch,
            raise_server_exceptions=False,
        ),
        member_id=LEADER_ID,
        role="repository_leader",
    )

    assert response.status_code == 500
    assert FAULT_DETAIL not in response.text
    assert "ControllerFault" not in response.text
    assert "Traceback" not in response.text
