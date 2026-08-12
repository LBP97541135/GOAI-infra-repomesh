import asyncio
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

import repomesh.bootstrap.app as bootstrap_app
from repomesh.bootstrap import create_app
from repomesh.bootstrap.container import ApplicationContainer
from repomesh.integrations.agentteams import (
    AgentTeamsControlPlaneClient,
    AgentTeamsMatrixClient,
)
from repomesh.settings import Settings


class StaticProbe:
    def __init__(self, ready: bool) -> None:
        self._ready = ready

    async def health(self) -> bool:
        return self._ready


def test_required_agentteams_failure_marks_api_not_ready(
    application_container: ApplicationContainer,
) -> None:
    container = replace(
        application_container,
        agentteams_required=True,
        agentteams_probe=StaticProbe(False),
    )

    with TestClient(create_app(container)) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {"status": "not_ready", "dependency": "agentteams"}
    }


def test_required_agentteams_success_marks_api_ready(
    application_container: ApplicationContainer,
) -> None:
    container = replace(
        application_container,
        agentteams_required=True,
        agentteams_probe=StaticProbe(True),
    )

    with TestClient(create_app(container)) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_default_container_wires_agentteams_adapters(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'bootstrap.db'}",
        agentteams_required=True,
        agentteams_controller_url="http://agentteams-controller:8090",
        agentteams_controller_token="controller-token",
        agentteams_matrix_url="http://agentteams-controller:6167",
        agentteams_matrix_access_token="matrix-token",
    )
    monkeypatch.setattr(bootstrap_app, "get_settings", lambda: settings)

    container = bootstrap_app.build_default_container()

    assert isinstance(container.agent_team_control_plane, AgentTeamsControlPlaneClient)
    # The messenger the container hands out is the delivery wrapper that retells
    # AgentTeamsUnavailable as the collaboration port's retryable refusal
    # (defect A-6). The Matrix client itself is still what gets closed.
    assert container.agent_team_messenger is not None
    assert not isinstance(container.agent_team_messenger, AgentTeamsMatrixClient)
    assert any(
        isinstance(resource, AgentTeamsMatrixClient) for resource in container.external_resources
    )
    assert container.agentteams_probe is container.agent_team_control_plane
    assert container.agentteams_required is True
    assert len(container.external_resources) == 2
    asyncio.run(container.close())


def test_default_container_allows_controller_without_matrix_token(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'bootstrap-no-matrix.db'}",
        agentteams_matrix_access_token=None,
    )
    monkeypatch.setattr(bootstrap_app, "get_settings", lambda: settings)

    container = bootstrap_app.build_default_container()

    assert isinstance(container.agent_team_control_plane, AgentTeamsControlPlaneClient)
    assert container.agent_team_messenger is None
    assert len(container.external_resources) == 1
    asyncio.run(container.close())
