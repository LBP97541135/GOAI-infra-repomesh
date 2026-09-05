"""``POST /repositories/{id}/agent-team`` chooses a construction mode, not a runtime.

Hosted-native spec D-17 in one endpoint: the onboarding request used to carry
``leader_runtime`` / ``worker_runtime`` with defaults that disagreed with the
settings' own (``repomesh-runner`` here, ``copaw`` there). It now carries one
``construction_mode``, and everything the controller is asked for follows from
it through ``derive_runtime``: a ``local_cli`` team's leader and workers are
projected with ``containerManaged: false`` — identity and seat, no container,
a Bridge for a body — and a ``hosted_native`` team's with copaw containers.
The deployment default fills in when the request says nothing, so there is
exactly one place a default lives.

The control plane is a recording double; the directory and the catalog are
the container's own (SQLite), so the principals and the team come out of the
production code path.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from repomesh.bootstrap import create_app
from repomesh.bootstrap.container import ApplicationContainer
from repomesh.modules.agent_directory.application import CreateAgent, CreateAgentRequest
from repomesh.modules.agent_directory.contracts import AgentRole
from repomesh.modules.agent_runtime.ports.agent_team import (
    TeamProjection,
    TeamRuntimeRef,
    WorkerProjection,
    WorkerRuntimeRef,
)
from repomesh.modules.project.contracts import ConstructionMode, derive_runtime
from repomesh.modules.repository_intelligence.application import RegisterRepository
from repomesh.modules.repository_intelligence.domain import RepositoryProfile
from repomesh.settings import get_settings

PATH = "/api/v1/repositories/{repository_id}/agent-team"


class RecordingControlPlane:
    """Answers every ensure and keeps the projections it was asked for."""

    def __init__(self) -> None:
        self.workers: list[WorkerProjection] = []
        self.teams: list[TeamProjection] = []

    async def ensure_worker(
        self, projection: WorkerProjection, *, idempotency_key: str
    ) -> WorkerRuntimeRef:
        self.workers.append(projection)
        return WorkerRuntimeRef(projection.name, "Ready")

    async def ensure_team(
        self, projection: TeamProjection, *, idempotency_key: str
    ) -> TeamRuntimeRef:
        self.teams.append(projection)
        return TeamRuntimeRef(
            name=projection.name,
            phase="Ready",
            team_room_id=f"!{projection.name}:matrix.local",
            leader_room_id=f"!leader-{projection.name}:matrix.local",
            leader_name=projection.members[0].name,
            ready_workers=len(projection.members) - 1,
            total_workers=len(projection.members) - 1,
        )


def _staffable(container: ApplicationContainer) -> tuple[str, str]:
    """An organization with one active Organization Leader and one catalogued repository."""

    organization_id = uuid4()

    async def _prepare() -> str:
        await CreateAgent(container.agent_directory).execute(
            CreateAgentRequest(
                organization_id=organization_id,
                role=AgentRole.ORGANIZATION_LEADER,
                agentteams_resource_name=f"onboard-org-leader-{organization_id.hex[:8]}",
            ),
            idempotency_key=f"onboard-org-leader-{organization_id}",
        )
        profile = RepositoryProfile(
            name=f"pricing-{organization_id.hex[:6]}",
            url=f"https://github.com/example/pricing-{organization_id.hex[:6]}",
        )
        await RegisterRepository(container.repository_catalog).execute(profile)
        return str(profile.id)

    repository_id = asyncio.run(_prepare())
    return str(organization_id), repository_id


def _client(container: ApplicationContainer, control_plane: RecordingControlPlane) -> TestClient:
    client = TestClient(create_app(replace(container, agent_team_control_plane=control_plane)))
    client.__enter__()
    client.post(
        "/api/v1/auth/bootstrap",
        json={"username": "admin", "password": "strong-password-123", "display_name": "Admin"},
    )
    client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "strong-password-123"},
    )
    return client


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (ConstructionMode.LOCAL_CLI, "local_cli"),
        (ConstructionMode.HOSTED_NATIVE, "hosted_native"),
    ],
)
def test_the_mode_decides_how_every_member_is_projected(
    application_container: ApplicationContainer, mode: ConstructionMode, expected: str
) -> None:
    organization_id, repository_id = _staffable(application_container)
    control_plane = RecordingControlPlane()
    derived = derive_runtime(mode)

    with _client(application_container, control_plane) as client:
        response = client.post(
            PATH.format(repository_id=repository_id),
            json={
                "organization_id": organization_id,
                "worker_count": 2,
                "construction_mode": mode.value,
                "idempotency_key": f"onboard:{repository_id}",
            },
        )

    assert response.status_code == 201, response.text
    assert response.json()["construction_mode"] == expected
    assert len(response.json()["workers"]) == 2
    # The leader and both workers: one mode, three identical answers.
    assert [worker.container_managed for worker in control_plane.workers] == [
        derived.container_managed
    ] * 3
    assert {worker.runtime for worker in control_plane.workers} == {derived.worker_runtime}
    assert len(control_plane.teams) == 1


def test_a_request_that_says_nothing_takes_the_deployment_default(
    application_container: ApplicationContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One default, and it is the settings' (D-17).

    Set to ``local_cli`` rather than left at the product default so the test
    can tell "the endpoint read the setting" from "the endpoint hard-codes
    hosted_native".
    """

    monkeypatch.setenv("REPOMESH_CONSTRUCTION_MODE_DEFAULT", "local_cli")
    get_settings.cache_clear()
    try:
        organization_id, repository_id = _staffable(application_container)
        control_plane = RecordingControlPlane()

        with _client(application_container, control_plane) as client:
            response = client.post(
                PATH.format(repository_id=repository_id),
                json={
                    "organization_id": organization_id,
                    "idempotency_key": f"onboard:{repository_id}",
                },
            )
    finally:
        get_settings.cache_clear()

    assert response.status_code == 201, response.text
    assert response.json()["construction_mode"] == "local_cli"
    assert all(worker.container_managed is False for worker in control_plane.workers)


def test_the_retired_runtime_fields_are_ignored_not_honoured(
    application_container: ApplicationContainer,
) -> None:
    """A client still sending ``worker_runtime`` gets the mode's runtime, not its own.

    The pair was retired because it was a second default; honouring it here
    would bring the disagreement back through the door it left by.
    """

    organization_id, repository_id = _staffable(application_container)
    control_plane = RecordingControlPlane()

    with _client(application_container, control_plane) as client:
        response = client.post(
            PATH.format(repository_id=repository_id),
            json={
                "organization_id": organization_id,
                "leader_runtime": "openclaw",
                "worker_runtime": "repomesh-runner",
                "construction_mode": "hosted_native",
                "idempotency_key": f"onboard:{repository_id}",
            },
        )

    assert response.status_code == 201, response.text
    hosted = derive_runtime(ConstructionMode.HOSTED_NATIVE)
    assert {worker.runtime for worker in control_plane.workers} == {hosted.worker_runtime}
    assert all(worker.container_managed is True for worker in control_plane.workers)


def test_an_unknown_mode_is_a_validation_error(
    application_container: ApplicationContainer,
) -> None:
    organization_id, repository_id = _staffable(application_container)

    with _client(application_container, RecordingControlPlane()) as client:
        response = client.post(
            PATH.format(repository_id=repository_id),
            json={
                "organization_id": organization_id,
                "construction_mode": "remote_runner",
                "idempotency_key": f"onboard:{repository_id}",
            },
        )

    assert response.status_code == 422
