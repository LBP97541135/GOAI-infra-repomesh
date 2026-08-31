"""The two readiness endpoints over HTTP: a member's self-report, the console's read.

The write endpoint is the first ``/runtime`` route that refuses the *global*
control token. Everywhere else on this router that credential means "every
worker", which is exactly why it cannot be used here: readiness is a self-report
about one process, and a credential with no subject has no process to report
about. So the auth matrix below is one row longer than its neighbours', and the
403 for the control token is asserted rather than assumed.

The read endpoint is a console surface and is guarded like the read models, not
like the Bridge writes — a browser session holds the action token and no member
credential at all.

Doubles are mounted the way ``test_external_member_binding.py`` mounts its own,
so nothing here reaches a network or a database. The store, by contrast, is the
real one: it is in-memory by design, so a double would only be a second
implementation of the thing under test.
"""

from __future__ import annotations

import json
from pathlib import Path
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
from repomesh.modules.agent_runtime.api.models import EXTERNAL_MEMBER_READINESS_SCHEMA
from repomesh.modules.agent_runtime.application.readiness import ExternalMemberReadinessStore
from repomesh.settings import get_settings

CONTROL_TOKEN = "runner-secret"
MEMBER_TOKEN = "member-secret"
OTHER_MEMBER_TOKEN = "other-member-secret"
ACTION_TOKEN = "console-secret"
WORKSPACE_ROOT = ".repomesh-workspaces"
TTL_SECONDS = 45

MEMBER_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_MEMBER_ID = UUID("22222222-2222-4222-8222-222222222222")

MEMBER_HEADERS = {"Authorization": f"Bearer {MEMBER_TOKEN}"}
CONSOLE_HEADERS = {"Authorization": f"Bearer {ACTION_TOKEN}"}

WRITE_PATH = "/api/v1/runtime/v1/external-members/{member_id}/readiness"
READ_PATH = "/api/v1/runtime/v1/external-members/readiness"


@pytest.fixture(autouse=True)
def _isolated_settings():
    """Settings are an ``lru_cache`` and this module rewrites their inputs."""

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class StubContainer:
    def __init__(self, *, directory: StubDirectory) -> None:
        self.agent_directory = directory
        self._store = ExternalMemberReadinessStore(ttl_seconds=TTL_SECONDS)

    def external_member_readiness_store(self) -> ExternalMemberReadinessStore:
        return self._store


def principal(
    member_agent_id: UUID, *, role: AgentRole = AgentRole.WORKER
) -> AgentPrincipalView:
    return AgentPrincipalView(
        id=member_agent_id,
        organization_id=uuid4(),
        role=role,
        leader_agent_id=None,
        repository_id=None,
        responsibility_paths=(),
        agentteams_resource_name=f"readiness-{role.value}",
        status=AgentPrincipalStatus.ACTIVE,
    )


def client(*principals: AgentPrincipalView, monkeypatch) -> TestClient:
    # Set rather than deleted: ``Settings`` also reads ``.env``, and only a
    # present-but-empty variable reliably means "unconfigured here".
    monkeypatch.setenv("REPOMESH_RUNNER_CONTROL_TOKEN", CONTROL_TOKEN)
    monkeypatch.setenv(
        "REPOMESH_RUNNER_WORKER_TOKENS",
        json.dumps({str(MEMBER_ID): MEMBER_TOKEN, str(OTHER_MEMBER_ID): OTHER_MEMBER_TOKEN}),
    )
    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", ACTION_TOKEN)
    monkeypatch.setenv("REPOMESH_RUNNER_WORKSPACE_ROOT", WORKSPACE_ROOT)
    get_settings.cache_clear()
    application = FastAPI()
    application.include_router(api_router)
    application.state.container = StubContainer(directory=StubDirectory(*principals))
    return TestClient(application)


def body(
    *,
    instance_id: UUID | None = None,
    kind: str = "startup",
    role: str = "worker",
    leader_lane: bool = False,
    governed_lane: bool = True,
    workspace_root: str | None = WORKSPACE_ROOT,
) -> dict[str, object]:
    """A well-formed worker report; every test names only what it is about."""

    return {
        "schema": EXTERNAL_MEMBER_READINESS_SCHEMA,
        "instanceId": str(uuid4() if instance_id is None else instance_id),
        "kind": kind,
        "role": role,
        "leaderLane": leader_lane,
        "governedLane": governed_lane,
        "workspaceRoot": workspace_root,
    }


def report(
    test_client: TestClient,
    *,
    member_id: UUID = MEMBER_ID,
    headers: dict[str, str] | None = MEMBER_HEADERS,
    **overrides,
):
    return test_client.post(
        WRITE_PATH.format(member_id=member_id), json=body(**overrides), headers=headers
    )


# ---------------------------------------------------------------------------
# The write: a member reporting about itself
# ---------------------------------------------------------------------------


def test_a_startup_report_answers_the_lease_and_when_to_renew(monkeypatch) -> None:
    response = report(client(principal(MEMBER_ID), monkeypatch=monkeypatch))

    assert response.status_code == 200
    payload = response.json()
    assert payload["agentId"] == str(MEMBER_ID)
    assert payload["status"] == "ready"
    # The reporter is told when to come back rather than deciding for itself, so
    # a deployment that retunes the TTL retunes every Bridge with it.
    assert payload["renewAfterSeconds"] == TTL_SECONDS // 3
    assert set(payload) == {"agentId", "status", "expiresAt", "renewAfterSeconds"}


def test_a_renew_from_the_same_instance_is_accepted(monkeypatch) -> None:
    test_client = client(principal(MEMBER_ID), monkeypatch=monkeypatch)
    instance = uuid4()

    assert report(test_client, instance_id=instance).status_code == 200
    renewed = report(test_client, instance_id=instance, kind="renew")

    assert renewed.status_code == 200
    assert renewed.json()["status"] == "ready"


def test_a_shutdown_report_takes_the_member_offline(monkeypatch) -> None:
    test_client = client(principal(MEMBER_ID), monkeypatch=monkeypatch)
    instance = uuid4()
    report(test_client, instance_id=instance)

    response = report(test_client, instance_id=instance, kind="shutdown")

    assert response.status_code == 200
    assert response.json()["status"] == "offline"


def test_a_renew_that_lands_after_shutdown_does_not_revive_the_member(monkeypatch) -> None:
    """A renew timer firing during teardown, and the two halves agreeing about it.

    The report is not refused — the lease is still this instance's, so nothing
    is wrong — but it does not bring the member back either: only a startup says
    that. What matters here is that the *receipt* and the read model say the same
    thing about the same member at the same moment, which they only do because
    the receipt's status is derived from the row after the report is applied
    rather than assumed from the kind of report it was.
    """

    test_client = client(principal(MEMBER_ID), monkeypatch=monkeypatch)
    instance = uuid4()
    report(test_client, instance_id=instance)
    report(test_client, instance_id=instance, kind="shutdown")

    late = report(test_client, instance_id=instance, kind="renew")

    assert late.status_code == 200
    assert late.json()["status"] == "offline"
    (member,) = test_client.get(READ_PATH, headers=CONSOLE_HEADERS).json()["members"]
    assert member["status"] == "offline"
    assert member["stoppedAt"] is not None


def test_a_report_from_a_replaced_instance_is_a_named_409(monkeypatch) -> None:
    """The one refusal the Bridge acts on: it has been taken over, so it exits.

    Structured rather than a sentence, because "stop renewing and shut down" is
    a different reaction from every other 409 on this route, and matching on
    prose is how a client ends up doing it by accident.
    """

    test_client = client(principal(MEMBER_ID), monkeypatch=monkeypatch)
    report(test_client, instance_id=uuid4())

    response = report(test_client, instance_id=uuid4(), kind="renew")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "stale_instance"


def test_a_leader_that_reports_a_worker_capability_is_409(monkeypatch) -> None:
    test_client = client(
        principal(MEMBER_ID, role=AgentRole.REPOSITORY_LEADER), monkeypatch=monkeypatch
    )

    response = report(
        test_client,
        role="repository_leader",
        leader_lane=True,
        governed_lane=True,
        workspace_root=None,
    )

    assert response.status_code == 409
    assert isinstance(response.json()["detail"], str)


def test_a_worker_under_another_workspace_root_is_409(monkeypatch) -> None:
    """The refusal names no path: neither this deployment's nor the reporter's."""

    response = report(
        client(principal(MEMBER_ID), monkeypatch=monkeypatch), workspace_root="C:/somewhere/else"
    )

    assert response.status_code == 409
    assert "somewhere" not in response.text
    assert WORKSPACE_ROOT not in response.text


def test_a_member_the_directory_does_not_hold_is_404(monkeypatch) -> None:
    response = report(client(monkeypatch=monkeypatch))

    assert response.status_code == 404


def test_a_body_that_is_not_the_v1_document_is_refused(monkeypatch) -> None:
    """The schema is pinned, so a later readiness family cannot arrive here by accident."""

    test_client = client(principal(MEMBER_ID), monkeypatch=monkeypatch)
    payload = body() | {"schema": "repomesh.agent-bridge.readiness.v2"}

    response = test_client.post(
        WRITE_PATH.format(member_id=MEMBER_ID), json=payload, headers=MEMBER_HEADERS
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Who may write it
# ---------------------------------------------------------------------------


def test_a_report_without_a_credential_is_401(monkeypatch) -> None:
    response = report(client(principal(MEMBER_ID), monkeypatch=monkeypatch), headers=None)

    assert response.status_code == 401


def test_the_control_token_may_not_report_on_a_members_behalf(monkeypatch) -> None:
    """A credential with no subject cannot make a self-report about anyone."""

    response = report(
        client(principal(MEMBER_ID), monkeypatch=monkeypatch),
        headers={"Authorization": f"Bearer {CONTROL_TOKEN}"},
    )

    assert response.status_code == 403


def test_a_member_may_not_report_for_another_member(monkeypatch) -> None:
    response = report(
        client(principal(MEMBER_ID), principal(OTHER_MEMBER_ID), monkeypatch=monkeypatch),
        headers={"Authorization": f"Bearer {OTHER_MEMBER_TOKEN}"},
    )

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# The read: what the console polls
# ---------------------------------------------------------------------------


def test_the_console_read_lists_the_member_with_its_derived_status(monkeypatch) -> None:
    test_client = client(principal(MEMBER_ID), monkeypatch=monkeypatch)
    report(test_client)

    response = test_client.get(READ_PATH, headers=CONSOLE_HEADERS)

    assert response.status_code == 200
    (member,) = response.json()["members"]
    assert member["agentId"] == str(MEMBER_ID)
    assert member["status"] == "ready"
    assert member["role"] == "worker"
    assert member["governedLane"] is True
    assert member["stoppedAt"] is None
    # The reported workspace root is a path on the operator's own machine and
    # has no reader here, so it never leaves the process.
    assert set(member) == {
        "agentId",
        "status",
        "role",
        "leaderLane",
        "governedLane",
        "reportedAt",
        "expiresAt",
        "stoppedAt",
    }
    assert str(Path(WORKSPACE_ROOT)) not in response.text


def test_the_console_read_is_empty_before_anybody_reports(monkeypatch) -> None:
    response = client(principal(MEMBER_ID), monkeypatch=monkeypatch).get(
        READ_PATH, headers=CONSOLE_HEADERS
    )

    assert response.status_code == 200
    assert response.json() == {"members": []}


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param(None, id="no credential"),
        pytest.param(MEMBER_HEADERS, id="a member credential"),
    ],
)
def test_the_console_read_refuses_anything_but_the_action_token(
    headers: dict[str, str] | None, monkeypatch
) -> None:
    """A member's own token opens the write and nothing else."""

    response = client(principal(MEMBER_ID), monkeypatch=monkeypatch).get(
        READ_PATH, headers=headers
    )

    assert response.status_code == 401
