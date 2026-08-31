"""``PUT /api/v1/runtime/external-workers/{id}`` over HTTP (ADR 0004).

The production entry point for ``ProvisionExternalWorker``: until this route
existed the use case had no caller in ``src/`` at all, so "this worker's body
runs outside the cluster" was a decision only a test could take.

Two things are pinned here, and they are different in kind.

*The translation table.* Which refusal wears which status code, mirrored on the
preflight endpoint's table so that an operator reading a 409 from one and a 409
from the other learns the same thing: 404 is "RepoMesh has never heard of this
principal", 409 is "the facts do not add up to an external worker", 503 is "the
control plane could not be asked", and 500 is none of those — something broke,
and translating it would send an operator to fix a worker that is fine.

*The shape of the request.* The whole request is the path id. A caller may not
say ``containerManaged``, a controller resource name, or a runtime, because
each of those is a fact the controller and RepoMesh's own directory own; a body
that states one is refused rather than quietly ignored, so that nobody ends up
believing they set a field that was dropped on the floor.

The container is a double, mounted the way ``test_external_worker_binding.py``
mounts its own, so nothing here reaches a network or a database — except
``test_the_composition_root_translates_an_adapter_conflict``, which builds a
real container on the sqlite fixture precisely because the translation it pins
is the composition root's job and lives nowhere else.
"""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from integrations.agentteams.fakes import StubDirectory

from repomesh.api.router import api_router
from repomesh.bootstrap.container import ApplicationContainer
from repomesh.integrations.agentteams import AgentTeamsConflict, AgentTeamsUnavailable
from repomesh.modules.agent_directory.contracts import (
    AgentPrincipalStatus,
    AgentPrincipalView,
    AgentRole,
)
from repomesh.modules.agent_runtime.contracts import (
    ExternalWorkerRefused,
)
from repomesh.modules.agent_runtime.ports.agent_team import (
    ManagerProjection,
    ManagerRuntimeRef,
    TeamProjection,
    TeamRuntimeRef,
    WorkerControlPlaneUnavailable,
    WorkerProjection,
    WorkerRuntimeRef,
)
from repomesh.modules.identity_access.local_accounts import (
    LocalAuthenticationError,
    LocalHumanAccountView,
)

WORKER_ID = uuid4()
ORGANIZATION_ID = uuid4()
WORKER_NAME = "repomesh-worker-bridge"
SESSION = "admin-session-token"
HEADERS = {"Authorization": f"Bearer {SESSION}"}
FAULT_DETAIL = "psycopg.OperationalError: password authentication failed for user 'repomesh'"
#: "leave the default in place", so that ``None`` can mean what it means in
#: production: this deployment has no AgentTeams control plane wired.
_DEFAULT: object = object()


class ControllerFault(RuntimeError):
    """Something the endpoint has no answer for.

    Not an ``ExternalWorkerError`` and not a ``WorkerControlPlaneUnavailable``:
    those are verdicts on the request and each has its status code. This is the
    failure nobody classified, carrying a message of exactly the sort that must
    not reach a caller.
    """


class StubProvisioner:
    """Stands in for the translated adapter the composition root supplies.

    It raises what that adapter hands the router — module-owned exceptions —
    rather than the integration's own taxonomy; the translation itself is
    pinned separately against a real container.
    """

    def __init__(
        self,
        *,
        worker: WorkerRuntimeRef | None = None,
        raises: Exception | None = None,
    ) -> None:
        self._worker = worker
        self._raises = raises
        self.calls: list[tuple[str, str]] = []

    async def provision(self, name: str, *, idempotency_key: str) -> WorkerRuntimeRef:
        self.calls.append((name, idempotency_key))
        if self._raises is not None:
            raise self._raises
        assert self._worker is not None
        return self._worker


class StubAccounts:
    """A ``LocalAccountService`` for one session token."""

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
    ) -> None:
        self.agent_directory = directory
        self._provisioner = provisioner
        self._accounts = StubAccounts(account)

    def external_worker_provisioner(self) -> object | None:
        return self._provisioner

    def local_account_service(self) -> StubAccounts:
        return self._accounts


def _account(*, is_admin: bool = True) -> LocalHumanAccountView:
    return LocalHumanAccountView(
        id=uuid4(),
        username="operator",
        display_name="Operator",
        is_admin=is_admin,
        active=True,
    )


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


def _external_worker(*, container_managed: bool | None = False) -> WorkerRuntimeRef:
    return WorkerRuntimeRef(
        name=WORKER_NAME,
        phase="Ready",
        matrix_user_id="@repomesh-worker-bridge:matrix.local",
        team="repomesh-team-pricing",
        container_managed=container_managed,
    )


def _client(
    *,
    directory: StubDirectory | None = None,
    provisioner: object = _DEFAULT,
    account: LocalHumanAccountView | None = None,
    raise_server_exceptions: bool = True,
) -> TestClient:
    application = FastAPI()
    application.include_router(api_router)
    application.state.container = StubContainer(
        directory=StubDirectory(_worker_principal()) if directory is None else directory,
        provisioner=StubProvisioner(worker=_external_worker())
        if provisioner is _DEFAULT
        else provisioner,
        account=_account() if account is None else account,
    )
    # ``raise_server_exceptions=False`` is how a test sees the response a real
    # deployment sends for an unhandled exception; the default re-raises it in
    # the test process instead.
    return TestClient(application, raise_server_exceptions=raise_server_exceptions)


def _put(
    client: TestClient,
    *,
    headers: dict[str, str] | None = HEADERS,
    worker_id: UUID = WORKER_ID,
    json: object | None = None,
):
    return client.put(
        f"/api/v1/runtime/external-workers/{worker_id}",
        headers=headers,
        json=json,
    )


# ---------------------------------------------------------------------------
# The guard: a local human session, and an administrator's
# ---------------------------------------------------------------------------


def test_no_session_is_401() -> None:
    response = _put(_client(), headers=None)
    assert response.status_code == 401


def test_an_invalid_session_is_401() -> None:
    response = _put(_client(), headers={"Authorization": "Bearer not-a-session"})
    assert response.status_code == 401


def test_a_session_cookie_is_accepted_like_the_delivery_guard() -> None:
    """The console holds a cookie, not an Authorization header."""

    client = _client()
    client.cookies.set("repomesh_session", SESSION)
    response = _put(client, headers=None)
    assert response.status_code == 200


def test_a_non_admin_session_is_403() -> None:
    """Provisioning is an administrator's decision, not any logged-in human's."""

    response = _put(_client(account=_account(is_admin=False)))
    assert response.status_code == 403


def test_the_guard_runs_before_the_provisioner() -> None:
    """An unauthenticated request must not reach the control plane at all."""

    provisioner = StubProvisioner(worker=_external_worker())
    _put(_client(provisioner=provisioner), headers=None)
    assert provisioner.calls == []


# ---------------------------------------------------------------------------
# The translation table, mirrored on the preflight endpoint's
# ---------------------------------------------------------------------------


def test_an_unknown_principal_is_404() -> None:
    response = _put(_client(directory=StubDirectory()))
    assert response.status_code == 404


def test_a_principal_that_is_not_a_worker_is_409() -> None:
    directory = StubDirectory(_worker_principal(role=AgentRole.REPOSITORY_LEADER))
    response = _put(_client(directory=directory))
    assert response.status_code == 409


def test_a_disabled_principal_is_409() -> None:
    directory = StubDirectory(_worker_principal(status=AgentPrincipalStatus.DISABLED))
    response = _put(_client(directory=directory))
    assert response.status_code == 409


@pytest.mark.parametrize("container_managed", [True, None])
def test_an_unconfirmed_container_managed_is_409(container_managed: bool | None) -> None:
    """One refusal type, several messages -- the caller may not branch on text.

    ``True`` is the controller saying it owns the container; ``None`` is an
    answer that did not carry the field at all, which is "unknown" and must
    never be read as "external".
    """

    provisioner = StubProvisioner(worker=_external_worker(container_managed=container_managed))
    response = _put(_client(provisioner=provisioner))
    assert response.status_code == 409


def test_an_adapter_conflict_is_409() -> None:
    """What the composition root hands the router for an AgentTeams conflict.

    A worker that already exists as a *managed* one is the case that matters:
    converting it is not something an operator gets to do silently, and a 500
    would read as "try again" when no retry clears it.
    """

    provisioner = StubProvisioner(
        raises=ExternalWorkerRefused("AgentTeams HTTP 409: worker differs in: containerManaged")
    )
    response = _put(_client(provisioner=provisioner))
    assert response.status_code == 409


def test_an_unconfigured_control_plane_is_503() -> None:
    response = _put(_client(provisioner=None))
    assert response.status_code == 503
    assert response.json()["detail"] == "AgentTeams control plane is not configured"


def test_an_unreachable_control_plane_is_503() -> None:
    provisioner = StubProvisioner(
        raises=WorkerControlPlaneUnavailable(
            "AgentTeams request failed: POST /api/v1/workers"
        )
    )
    response = _put(_client(provisioner=provisioner))
    assert response.status_code == 503
    assert "AgentTeams request failed" in response.json()["detail"]


def test_an_unclassified_fault_is_an_untranslated_500() -> None:
    """The fourth outcome, pinned so it cannot drift into one of the other three.

    The refusals are enumerated in the router; the absence of a bare
    ``except Exception`` beside them is the contract, and this is what holds it.
    """

    provisioner = StubProvisioner(raises=ControllerFault(FAULT_DETAIL))
    response = _put(
        _client(provisioner=provisioner, raise_server_exceptions=False),
    )

    assert response.status_code == 500
    assert FAULT_DETAIL not in response.text
    assert "ControllerFault" not in response.text
    assert "Traceback" not in response.text


# ---------------------------------------------------------------------------
# The answer, and what a caller may put in the request
# ---------------------------------------------------------------------------


def test_a_provisioned_worker_is_200_with_the_exact_wire_body() -> None:
    response = _put(_client())

    assert response.status_code == 200
    assert response.json() == {
        "workerAgentId": str(WORKER_ID),
        "workerName": WORKER_NAME,
        "phase": "Ready",
        "containerManaged": False,
    }


def test_the_response_carries_no_secret_and_no_controller_address() -> None:
    body = _put(_client()).text
    assert "http" not in body
    assert "token" not in body.lower()


def test_a_replay_is_the_same_answer_under_the_same_idempotency_key() -> None:
    """PUT, and it means it.

    Both calls answer 200 rather than 201-then-200: the control plane does not
    report whether it created the resource or found it (``ensure_worker``
    answers the same document either way), so a 201 here would be RepoMesh
    guessing at a fact it does not hold. The idempotency key is the one the use
    case derives from the agent alone, so the second call is the same
    controller side effect rather than a second one.
    """

    provisioner = StubProvisioner(worker=_external_worker())
    client = _client(provisioner=provisioner)

    first = _put(client)
    second = _put(client)

    assert (first.status_code, second.status_code) == (200, 200)
    assert first.json() == second.json()
    assert provisioner.calls == [
        (WORKER_NAME, f"external-worker:{WORKER_ID}:agentteams"),
        (WORKER_NAME, f"external-worker:{WORKER_ID}:agentteams"),
    ]


def test_an_empty_body_is_accepted() -> None:
    assert _put(_client(), json={}).status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {"containerManaged": False},
        {"containerManaged": True},
        {"workerName": "repomesh-worker-somebody-else"},
        {"runtime": "openclaw"},
    ],
)
def test_a_self_reported_projection_fact_is_refused(body: dict[str, object]) -> None:
    """Refused rather than ignored, on purpose.

    Every field above is owned by somebody else -- the resource name by the
    agent directory, the runtime and ``containerManaged`` by the controller's
    answer -- so accepting one would let a caller believe it set a fact that
    was silently dropped. The whole request is the path id.
    """

    assert _put(_client(), json=body).status_code == 422


def test_a_refused_body_never_reaches_the_control_plane() -> None:
    provisioner = StubProvisioner(worker=_external_worker())
    _put(_client(provisioner=provisioner), json={"containerManaged": True})
    assert provisioner.calls == []


# ---------------------------------------------------------------------------
# The composition root's half: the integration's taxonomy, translated
# ---------------------------------------------------------------------------


class _ConflictingControlPlane:
    """An ``AgentTeamControlPlane`` whose writes answer the way a conflict does."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def ensure_worker(
        self, projection: WorkerProjection, *, idempotency_key: str
    ) -> WorkerRuntimeRef:
        raise self._error

    async def ensure_manager(
        self, projection: ManagerProjection, *, idempotency_key: str
    ) -> ManagerRuntimeRef:  # pragma: no cover - not reached
        raise AssertionError("provisioning does not touch managers")

    async def ensure_team(
        self, projection: TeamProjection, *, idempotency_key: str
    ) -> TeamRuntimeRef:  # pragma: no cover - not reached
        raise AssertionError("provisioning does not touch teams")

    async def ensure_worker_ready(
        self, name: str, *, idempotency_key: str
    ) -> WorkerRuntimeRef:  # pragma: no cover - not reached
        raise AssertionError("provisioning does not ensure readiness")

    async def get_worker(self, name: str) -> WorkerRuntimeRef | None:  # pragma: no cover
        raise AssertionError("provisioning does not read")

    async def get_team(self, name: str) -> TeamRuntimeRef | None:  # pragma: no cover
        raise AssertionError("provisioning does not read")

    async def get_manager(self, name: str) -> ManagerRuntimeRef | None:  # pragma: no cover
        raise AssertionError("provisioning does not read")


def test_the_provisioner_is_absent_without_a_control_plane(
    application_container: ApplicationContainer,
) -> None:
    assert application_container.external_worker_provisioner() is None


async def test_the_composition_root_translates_an_adapter_conflict(
    application_container: ApplicationContainer,
) -> None:
    """The port's contract, held where it can be: outside the module.

    ``ExternalWorkerProvisioner`` says a conflict from the adapter is a refusal
    rather than an internal error, and the router cannot enforce that itself --
    module code may not import ``repomesh.integrations.*`` to catch
    ``AgentTeamsConflict``. So the composition root translates, exactly as it
    already does for the project runtime projection.
    """

    container = replace(
        application_container,
        agent_team_control_plane=_ConflictingControlPlane(
            AgentTeamsConflict("worker repomesh-worker-bridge differs in: containerManaged")
        ),
    )
    provisioner = container.external_worker_provisioner()
    assert provisioner is not None

    with pytest.raises(ExternalWorkerRefused):
        await provisioner.provision(WORKER_NAME, idempotency_key="external-worker:x:agentteams")


async def test_the_composition_root_translates_an_unreachable_controller(
    application_container: ApplicationContainer,
) -> None:
    container = replace(
        application_container,
        agent_team_control_plane=_ConflictingControlPlane(
            AgentTeamsUnavailable("AgentTeams request failed: POST /api/v1/workers")
        ),
    )
    provisioner = container.external_worker_provisioner()
    assert provisioner is not None

    with pytest.raises(WorkerControlPlaneUnavailable):
        await provisioner.provision(WORKER_NAME, idempotency_key="external-worker:x:agentteams")
