"""``PUT /api/v1/runtime/v2/external-members/{id}`` over HTTP (PR 5.5A).

The provisioning half of the D-11 generalization, and the sibling of the v1
admin route rather than a change to it: ``/runtime/external-workers/{id}`` keeps
refusing everything that is not a worker, and this is where a Repository Leader
becomes a ``containerManaged: false`` AgentTeams member. The two are pinned
against each other below — same principal, 409 there and 200 here.

Three things are asserted that the v1 file does not have to think about:

*The role comes from the directory.* The request is still nothing but the path
id, so the receipt's ``role`` is RepoMesh's own answer and not a caller's claim.
A body stating one would be a 422 like every other borrowed fact.

*The role reaches the adapter.* ``ensure_worker`` compares an existing worker
against the one being asked for, and a repository leader carries different
skills wherever the ordinary project path registered it — so passing a worker's
projection for a leader would answer 409 about skills and read as an operator's
mistake. The double records what it was called with.

*Nothing new is unlocked by it.* AC-02's boundary is the last section: a
Repository Leader that can now be provisioned and bound still cannot start a
coding task, and the check that stops it is the production one, not a stub.

The container is a double, mounted the way ``test_external_worker_provisioning``
mounts its own, so nothing here reaches a network or a database.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from integrations.agentteams.fakes import StubDirectory

from repomesh.api.router import api_router
from repomesh.bootstrap.container import ApplicationContainer
from repomesh.integrations.agentteams import AgentTeamsConflict, AgentTeamsUnavailable
from repomesh.integrations.runner import StartAssignedWorkerTask
from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalStatus,
    AgentPrincipalView,
    AgentRole,
)
from repomesh.modules.agent_runtime.contracts import (
    ExternalMemberRole,
    ExternalWorkerRefused,
    StartAssignedWorkerTaskCommand,
)
from repomesh.modules.agent_runtime.ports.agent_team import (
    WorkerControlPlaneUnavailable,
    WorkerProjection,
    WorkerRuntimeRef,
)
from repomesh.modules.identity_access.local_accounts import (
    LocalAuthenticationError,
    LocalHumanAccountView,
)
from repomesh.settings import get_settings

FIXTURES = Path(__file__).parents[2] / "contracts" / "agent-bridge" / "v2" / "fixtures"

SESSION = "admin-session-token"
HEADERS = {"Authorization": f"Bearer {SESSION}"}
ACTION_TOKEN = "internal-secret"
FAULT_DETAIL = "psycopg.OperationalError: password authentication failed for user 'repomesh'"
#: "leave the default in place", so ``None`` can mean what it means in
#: production: this deployment has no AgentTeams control plane wired.
_DEFAULT: object = object()


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


WORKER_ENROLLMENT = fixture("enrollment.worker.json")
LEADER_ENROLLMENT = fixture("enrollment.repository-leader.json")
ROGUE_ENROLLMENT = fixture("enrollment.invalid-role.organization-leader.json")

#: Every role the agent directory can hold, keyed by the string a contract
#: document spells it with. Built from the enum rather than written out, so a
#: fixture naming a role RepoMesh does not have would fail here rather than
#: quietly become something else.
_DIRECTORY_ROLES = {role.value: role for role in AgentRole}


class ControllerFault(RuntimeError):
    """The failure nobody classified, carrying a message that must not escape."""


class _RecordingControlPlane:
    """An ``AgentTeamControlPlane`` that remembers the projection it was asked for.

    Only ``ensure_worker`` is implemented: external provisioning writes nothing
    else, and a method that raised here would be the failure rather than a
    fixture gap.
    """

    def __init__(self) -> None:
        self.projections: list[WorkerProjection] = []
        self.raises: Exception | None = None

    async def ensure_worker(
        self, projection: WorkerProjection, *, idempotency_key: str
    ) -> WorkerRuntimeRef:
        if self.raises is not None:
            raise self.raises
        self.projections.append(projection)
        return WorkerRuntimeRef(name=projection.name, phase="Ready", container_managed=False)


class StubProvisioner:
    """Stands in for the translated adapter, and records the role it was given."""

    def __init__(
        self,
        *,
        worker: WorkerRuntimeRef | None = None,
        raises: Exception | None = None,
    ) -> None:
        self._worker = worker
        self._raises = raises
        self.calls: list[tuple[str, str, ExternalMemberRole]] = []

    async def provision(
        self,
        name: str,
        *,
        idempotency_key: str,
        role: ExternalMemberRole = ExternalMemberRole.WORKER,
    ) -> WorkerRuntimeRef:
        self.calls.append((name, idempotency_key, role))
        if self._raises is not None:
            raise self._raises
        assert self._worker is not None
        return self._worker


class StubAccounts:
    def __init__(self, account: LocalHumanAccountView | None) -> None:
        self._account = account

    async def authenticate(self, token: str) -> LocalHumanAccountView:
        if self._account is None or token != SESSION:
            raise LocalAuthenticationError("local session is invalid or expired")
        return self._account


class StubContainer:
    def __init__(
        self,
        *,
        directory: StubDirectory,
        provisioner: object | None,
        account: LocalHumanAccountView | None,
        worker_execution: object | None = None,
    ) -> None:
        self.agent_directory = directory
        self._provisioner = provisioner
        self._accounts = StubAccounts(account)
        self._worker_execution = worker_execution

    def external_member_provisioner(self) -> object | None:
        return self._provisioner

    def external_worker_provisioner(self) -> object | None:
        return self._provisioner

    def local_account_service(self) -> StubAccounts:
        return self._accounts

    def worker_execution_service(self) -> object | None:
        return self._worker_execution


def _account(*, is_admin: bool = True) -> LocalHumanAccountView:
    return LocalHumanAccountView(
        id=uuid4(),
        username="operator",
        display_name="Operator",
        is_admin=is_admin,
        active=True,
    )


def _principal(
    enrollment: dict[str, Any],
    *,
    role: AgentRole | None = None,
    status: AgentPrincipalStatus = AgentPrincipalStatus.ACTIVE,
) -> AgentPrincipalView:
    """The principal a contract fixture describes, role and all.

    The role is read out of the fixture unless a test overrides it, which is
    what makes ``enrollment.invalid-role.organization-leader.json`` load-bearing
    here rather than decorative: the document says ``organization_leader``, and
    that is the directory row the refusal below is about.
    """

    return AgentPrincipalView(
        id=UUID(enrollment["workerAgentId"]),
        organization_id=UUID(enrollment["organizationId"]),
        role=_DIRECTORY_ROLES[enrollment["role"]] if role is None else role,
        leader_agent_id=None,
        repository_id=None,
        responsibility_paths=(),
        agentteams_resource_name=enrollment["workerName"],
        status=status,
    )


def _provisioned(
    enrollment: dict[str, Any], *, container_managed: bool | None = False
) -> WorkerRuntimeRef:
    return WorkerRuntimeRef(
        name=enrollment["workerName"],
        phase="Ready",
        matrix_user_id=enrollment["matrixUserId"],
        team=enrollment["teamName"],
        container_managed=container_managed,
    )


def _client(
    *,
    enrollment: dict[str, Any] = LEADER_ENROLLMENT,
    directory: StubDirectory | None = None,
    provisioner: object = _DEFAULT,
    account: LocalHumanAccountView | None = None,
    worker_execution: object | None = None,
    raise_server_exceptions: bool = True,
) -> TestClient:
    application = FastAPI()
    application.include_router(api_router)
    application.state.container = StubContainer(
        directory=StubDirectory(_principal(enrollment)) if directory is None else directory,
        provisioner=StubProvisioner(worker=_provisioned(enrollment))
        if provisioner is _DEFAULT
        else provisioner,
        account=_account() if account is None else account,
        worker_execution=worker_execution,
    )
    return TestClient(application, raise_server_exceptions=raise_server_exceptions)


def _put(
    client: TestClient,
    *,
    member_id: UUID,
    headers: dict[str, str] | None = HEADERS,
    json: object | None = None,
):
    return client.put(
        f"/api/v1/runtime/v2/external-members/{member_id}",
        headers=headers,
        json=json,
    )


LEADER_ID = UUID(LEADER_ENROLLMENT["workerAgentId"])
WORKER_ID = UUID(WORKER_ENROLLMENT["workerAgentId"])
ROGUE_ID = UUID(ROGUE_ENROLLMENT["workerAgentId"])


# ---------------------------------------------------------------------------
# The answer, and the role in it
# ---------------------------------------------------------------------------


def test_a_repository_leader_is_provisioned_with_its_role(monkeypatch) -> None:
    """Acceptance 1, first half: provision now accepts a Repository Leader."""

    provisioner = StubProvisioner(worker=_provisioned(LEADER_ENROLLMENT))
    response = _put(_client(provisioner=provisioner), member_id=LEADER_ID)

    assert response.status_code == 200
    assert response.json() == {
        "workerAgentId": str(LEADER_ID),
        "workerName": LEADER_ENROLLMENT["workerName"],
        "role": "repository_leader",
        "phase": "Ready",
        "containerManaged": False,
    }


def test_the_adapter_is_asked_for_the_leaders_own_projection() -> None:
    """The role is not decoration: it picks which AgentTeams worker is requested.

    ``ensure_worker`` compares an existing resource against the one being asked
    for, and the ordinary project path registers a repository leader with a
    leader's skills. Asking for a worker's would answer 409 about a mismatch
    this call created — the R0 failure mode, one field over.
    """

    provisioner = StubProvisioner(worker=_provisioned(LEADER_ENROLLMENT))
    _put(_client(provisioner=provisioner), member_id=LEADER_ID)

    assert provisioner.calls == [
        (
            LEADER_ENROLLMENT["workerName"],
            f"external-worker:{LEADER_ID}:agentteams",
            ExternalMemberRole.REPOSITORY_LEADER,
        )
    ]


def test_a_worker_is_provisioned_through_v2_under_v1s_idempotency_key() -> None:
    """One AgentTeams resource per principal, whichever route asked for it.

    The key names the agent and nothing else — no version, no role — so
    provisioning the same worker through v1 and then v2 is one controller side
    effect rather than two spellings of one decision.
    """

    provisioner = StubProvisioner(worker=_provisioned(WORKER_ENROLLMENT))
    response = _put(
        _client(enrollment=WORKER_ENROLLMENT, provisioner=provisioner), member_id=WORKER_ID
    )

    assert response.status_code == 200
    assert response.json()["role"] == "worker"
    assert provisioner.calls == [
        (
            WORKER_ENROLLMENT["workerName"],
            f"external-worker:{WORKER_ID}:agentteams",
            ExternalMemberRole.WORKER,
        )
    ]


def test_a_replay_is_the_same_answer() -> None:
    provisioner = StubProvisioner(worker=_provisioned(LEADER_ENROLLMENT))
    client = _client(provisioner=provisioner)

    first = _put(client, member_id=LEADER_ID)
    second = _put(client, member_id=LEADER_ID)

    assert (first.status_code, second.status_code) == (200, 200)
    assert first.json() == second.json()
    assert {key for _, key, _ in provisioner.calls} == {
        f"external-worker:{LEADER_ID}:agentteams"
    }


def test_the_receipt_carries_no_secret_and_no_controller_address() -> None:
    body = _put(_client(), member_id=LEADER_ID).text
    assert "http" not in body
    assert "token" not in body.lower()


def test_the_receipt_carries_no_schema_version() -> None:
    """An operator's receipt, deliberately not a document to bind to.

    The versioned one is what preflight returns; stamping a version here would
    invite a consumer to treat this shape as a contract it may depend on.
    """

    assert "schemaVersion" not in _put(_client(), member_id=LEADER_ID).json()


# ---------------------------------------------------------------------------
# Which roles may be external members
# ---------------------------------------------------------------------------


def test_an_organization_leader_is_409() -> None:
    """Acceptance 3, first half, driven by the invalid enrollment fixture.

    The fixture's ``role`` is the directory row this refusal is about, so the
    document the v2 contract publishes as "must be rejected" is rejected by the
    server for the reason the filename claims.
    """

    assert ROGUE_ENROLLMENT["role"] == "organization_leader"
    assert ROGUE_ENROLLMENT["role"] not in {role.value for role in ExternalMemberRole}

    provisioner = StubProvisioner(worker=_provisioned(ROGUE_ENROLLMENT))
    response = _put(
        _client(enrollment=ROGUE_ENROLLMENT, provisioner=provisioner), member_id=ROGUE_ID
    )

    assert response.status_code == 409
    assert "organization_leader" in response.json()["detail"]
    assert provisioner.calls == []


def test_the_v1_route_still_refuses_the_leader_the_v2_route_provisions() -> None:
    """The compatibility statement, as an assertion rather than a promise."""

    client = _client()

    v1 = client.put(
        f"/api/v1/runtime/external-workers/{LEADER_ID}",
        headers=HEADERS,
    )
    v2 = _put(client, member_id=LEADER_ID)

    assert v1.status_code == 409
    assert v2.status_code == 200


def test_an_unknown_principal_is_404() -> None:
    response = _put(_client(directory=StubDirectory()), member_id=LEADER_ID)
    assert response.status_code == 404


def test_a_disabled_principal_is_409() -> None:
    directory = StubDirectory(
        _principal(LEADER_ENROLLMENT, status=AgentPrincipalStatus.DISABLED)
    )
    response = _put(_client(directory=directory), member_id=LEADER_ID)
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# The translation table, mirroring the v1 route's
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("container_managed", [True, None])
def test_an_unconfirmed_container_managed_is_409(container_managed: bool | None) -> None:
    provisioner = StubProvisioner(
        worker=_provisioned(LEADER_ENROLLMENT, container_managed=container_managed)
    )
    response = _put(_client(provisioner=provisioner), member_id=LEADER_ID)
    assert response.status_code == 409


def test_an_adapter_conflict_is_409() -> None:
    provisioner = StubProvisioner(
        raises=ExternalWorkerRefused("AgentTeams HTTP 409: worker differs in: containerManaged")
    )
    response = _put(_client(provisioner=provisioner), member_id=LEADER_ID)
    assert response.status_code == 409


def test_an_unconfigured_control_plane_is_503() -> None:
    response = _put(_client(provisioner=None), member_id=LEADER_ID)
    assert response.status_code == 503
    assert response.json()["detail"] == "AgentTeams control plane is not configured"


def test_an_unreachable_control_plane_is_503() -> None:
    provisioner = StubProvisioner(
        raises=WorkerControlPlaneUnavailable("AgentTeams request failed: POST /api/v1/workers")
    )
    response = _put(_client(provisioner=provisioner), member_id=LEADER_ID)
    assert response.status_code == 503
    assert "AgentTeams request failed" in response.json()["detail"]


def test_an_unclassified_fault_is_an_untranslated_500() -> None:
    provisioner = StubProvisioner(raises=ControllerFault(FAULT_DETAIL))
    response = _put(
        _client(provisioner=provisioner, raise_server_exceptions=False), member_id=LEADER_ID
    )

    assert response.status_code == 500
    assert FAULT_DETAIL not in response.text
    assert "ControllerFault" not in response.text
    assert "Traceback" not in response.text


# ---------------------------------------------------------------------------
# The guard, and the shape of the request
# ---------------------------------------------------------------------------


def test_no_session_is_401() -> None:
    assert _put(_client(), member_id=LEADER_ID, headers=None).status_code == 401


def test_a_non_admin_session_is_403() -> None:
    response = _put(_client(account=_account(is_admin=False)), member_id=LEADER_ID)
    assert response.status_code == 403


def test_the_guard_runs_before_the_provisioner() -> None:
    provisioner = StubProvisioner(worker=_provisioned(LEADER_ENROLLMENT))
    _put(_client(provisioner=provisioner), member_id=LEADER_ID, headers=None)
    assert provisioner.calls == []


@pytest.mark.parametrize(
    "body",
    [
        {"role": "repository_leader"},
        {"role": "organization_leader"},
        {"containerManaged": False},
        {"workerName": "repomesh-worker-somebody-else"},
    ],
)
def test_a_self_reported_fact_is_refused(body: dict[str, object]) -> None:
    """``role`` included, and that is the whole design of this endpoint.

    The role is read from the agent directory, and preflight's job is to confirm
    that RepoMesh and the enrollment agree about it. A caller-stated role here
    would make that circular — the server would be confirming the claim against
    itself.
    """

    assert _put(_client(), member_id=LEADER_ID, json=body).status_code == 422


def test_an_empty_body_is_accepted() -> None:
    assert _put(_client(), member_id=LEADER_ID, json={}).status_code == 200


# ---------------------------------------------------------------------------
# AC-02: what being an external member still does not unlock
# ---------------------------------------------------------------------------


def test_a_repository_leader_still_cannot_start_a_coding_task(monkeypatch) -> None:
    """The reverse lock, pinned against the production check rather than a stub.

    ``StartAssignedWorkerTask`` refuses a non-worker identity before it reads a
    task, so every other collaborator can be absent — reaching one would itself
    be the failure. Nothing in PR 5.5A touches this path; the point of the test
    is that nothing in it may.
    """

    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", ACTION_TOKEN)
    monkeypatch.setenv("REPOMESH_RUNNER_WORKER_TOKENS", "")
    get_settings.cache_clear()
    directory = StubDirectory(_principal(LEADER_ENROLLMENT))
    execution = StartAssignedWorkerTask(
        directory,
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
    application.state.container = StubContainer(
        directory=directory,
        provisioner=StubProvisioner(worker=_provisioned(LEADER_ENROLLMENT)),
        account=_account(),
        worker_execution=execution,
    )
    client = TestClient(application)

    response = client.post(
        "/api/v1/agent-actions/start-worker-task",
        headers={"Authorization": f"Bearer {ACTION_TOKEN}"},
        json={
            "task_id": str(uuid4()),
            "worker_agent_id": str(LEADER_ID),
            "adapter_id": "codex",
        },
    )

    assert response.status_code == 409
    assert "restricted to Worker identities" in response.json()["detail"]
    get_settings.cache_clear()


async def test_the_worker_only_check_is_the_first_thing_the_use_case_does() -> None:
    """The other half of the lock, one layer down and with no HTTP in the way."""

    execution = StartAssignedWorkerTask(
        StubDirectory(_principal(LEADER_ENROLLMENT)),
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="restricted to Worker identities"):
        await execution.execute(
            StartAssignedWorkerTaskCommand(
                task_id=uuid4(), worker_agent_id=LEADER_ID, adapter_id="codex"
            )
        )


# ---------------------------------------------------------------------------
# The composition root's half: one adapter under two names
# ---------------------------------------------------------------------------


def test_neither_provisioner_exists_without_a_control_plane(
    application_container: ApplicationContainer,
) -> None:
    assert application_container.external_member_provisioner() is None
    assert application_container.external_worker_provisioner() is None


async def test_the_composition_root_forwards_the_role_and_translates_conflicts(
    application_container: ApplicationContainer,
) -> None:
    """One AgentTeams resource per principal, so one adapter behind both names.

    The v1 accessor must keep answering an adapter that provisions a worker's
    projection when nobody names a role, and the v2 one must carry the role
    through to the controller. Both are the same object here on purpose: a
    second adapter is how the same principal ends up provisioned twice under
    two spellings of one decision.

    The conflict translation is the port's contract, and it is held where it can
    be — outside the module, since module code may not import
    ``repomesh.integrations.*`` to catch ``AgentTeamsConflict`` itself.
    """

    control_plane = _RecordingControlPlane()
    container = replace(application_container, agent_team_control_plane=control_plane)

    member = container.external_member_provisioner()
    worker = container.external_worker_provisioner()
    assert member is not None and worker is not None

    await member.provision(
        "repomesh-worker-leader",
        idempotency_key="external-worker:leader:agentteams",
        role=ExternalMemberRole.REPOSITORY_LEADER,
    )
    await worker.provision(
        "repomesh-worker-plain", idempotency_key="external-worker:plain:agentteams"
    )

    assert [(p.name, p.skills, p.container_managed) for p in control_plane.projections] == [
        ("repomesh-worker-leader", ("code-review", "planning"), False),
        ("repomesh-worker-plain", ("coding",), False),
    ]

    control_plane.raises = AgentTeamsConflict("worker differs in: containerManaged")
    with pytest.raises(ExternalWorkerRefused):
        await member.provision(
            "repomesh-worker-leader",
            idempotency_key="external-worker:leader:agentteams",
            role=ExternalMemberRole.REPOSITORY_LEADER,
        )

    control_plane.raises = AgentTeamsUnavailable("AgentTeams request failed: POST /api/v1/workers")
    with pytest.raises(WorkerControlPlaneUnavailable):
        await member.provision(
            "repomesh-worker-leader",
            idempotency_key="external-worker:leader:agentteams",
            role=ExternalMemberRole.REPOSITORY_LEADER,
        )
