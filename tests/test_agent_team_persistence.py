import asyncio
from dataclasses import replace
from uuid import uuid4

from fastapi.testclient import TestClient

from repomesh.bootstrap import create_app
from repomesh.bootstrap.container import ApplicationContainer
from repomesh.modules.agent_directory.application import (
    CreateAgent,
    CreateAgentRequest,
    CreateRepositoryAgentTeam,
    CreateRepositoryAgentTeamRequest,
)
from repomesh.modules.agent_directory.contracts import AgentRole
from repomesh.modules.agent_runtime.ports.agent_team import TeamRuntimeRef


class _ControlPlane:
    async def ensure_team(self, projection, *, idempotency_key: str):
        return TeamRuntimeRef(
            name=projection.name,
            phase="Ready",
            team_room_id="!team:matrix.local",
            leader_room_id="!leader:matrix.local",
            leader_name=projection.members[0].name,
            ready_workers=len(projection.members) - 1,
            total_workers=len(projection.members) - 1,
        )


def _seed_team(container: ApplicationContainer, organization_id, repository_id):
    async def _run():
        organization_leader = await CreateAgent(container.agent_directory).execute(
            CreateAgentRequest(
                organization_id=organization_id,
                role=AgentRole.ORGANIZATION_LEADER,
                agentteams_resource_name="persist-org-leader",
            ),
            idempotency_key="persist-org-leader",
        )
        return await CreateRepositoryAgentTeam(container.agent_directory).execute(
            CreateRepositoryAgentTeamRequest(
                organization_id=organization_id,
                organization_leader_id=organization_leader.principal.id,
                repository_id=repository_id,
                leader_agentteams_resource_name="persist-repo-leader",
                worker_agentteams_resource_names=("persist-worker",),
            ),
            idempotency_key="persist-team-agents",
        )

    return asyncio.run(_run())


def _admin(client: TestClient) -> None:
    client.post(
        "/api/v1/auth/bootstrap",
        json={"username": "admin", "password": "strong-password-123", "display_name": "Admin"},
    )
    client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "strong-password-123"},
    )


def _create_team_payload(organization_id, team_agents) -> dict:
    return {
        "organization_id": str(organization_id),
        "name": "saleor_backend",
        "description": "Saleor backend team",
        "leader_agent_id": str(team_agents.leader.id),
        "member_agent_ids": [str(team_agents.workers[0].id)],
        "idempotency_key": "persist-team:saleor-backend",
    }


def test_composed_team_is_persisted_and_listed(
    application_container: ApplicationContainer,
) -> None:
    organization_id = uuid4()
    repository_id = uuid4()
    team_agents = _seed_team(application_container, organization_id, repository_id)
    container = replace(application_container, agent_team_control_plane=_ControlPlane())

    with TestClient(create_app(container)) as client:
        _admin(client)
        created = client.post(
            "/api/v1/agent-teams",
            json=_create_team_payload(organization_id, team_agents),
        )
        assert created.status_code == 201

        listing = client.get("/api/v1/agent-teams")
        assert listing.status_code == 200
        teams = listing.json()
        assert len(teams) == 1
        team = teams[0]
        assert team["name"] == "saleor_backend"
        assert team["description"] == "Saleor backend team"
        assert team["leader_agent_id"] == str(team_agents.leader.id)
        assert team["member_agent_ids"] == [str(team_agents.workers[0].id)]
        assert team["repository_id"] == str(repository_id)


def test_recomposing_same_team_does_not_duplicate(
    application_container: ApplicationContainer,
) -> None:
    organization_id = uuid4()
    repository_id = uuid4()
    team_agents = _seed_team(application_container, organization_id, repository_id)
    container = replace(application_container, agent_team_control_plane=_ControlPlane())

    with TestClient(create_app(container)) as client:
        _admin(client)
        payload = _create_team_payload(organization_id, team_agents)
        assert client.post("/api/v1/agent-teams", json=payload).status_code == 201
        assert client.post("/api/v1/agent-teams", json=payload).status_code == 201

        teams = client.get("/api/v1/agent-teams").json()
        assert len(teams) == 1


def test_listing_agent_teams_requires_authentication(
    application_container: ApplicationContainer,
) -> None:
    with TestClient(create_app(application_container)) as client:
        assert client.get("/api/v1/agent-teams").status_code == 401
