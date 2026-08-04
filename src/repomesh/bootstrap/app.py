from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from repomesh.api.router import api_router
from repomesh.bootstrap.container import ApplicationContainer, AsyncCloseable
from repomesh.integrations.agentteams import (
    AgentTeamsControlPlaneClient,
    AgentTeamsMatrixClient,
)
from repomesh.integrations.coding_agents.mock import MockCodingAgent, MockScenario
from repomesh.modules.agent_directory.infrastructure import PostgresAgentDirectory
from repomesh.modules.project.infrastructure import PostgresProjectTopologyStore
from repomesh.modules.repository_intelligence.infrastructure import PostgresRepositoryCatalog
from repomesh.persistence import Database
from repomesh.persistence.outbox import OutboxStore
from repomesh.settings import get_settings


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    try:
        yield
    finally:
        await application.state.container.close()


def build_default_container() -> ApplicationContainer:
    settings = get_settings()
    database = Database(settings.database_url)
    control_plane = AgentTeamsControlPlaneClient(
        settings.agentteams_controller_url,
        token=settings.agentteams_controller_token,
    )
    messenger = (
        AgentTeamsMatrixClient(
            settings.agentteams_matrix_url,
            settings.agentteams_matrix_access_token,
        )
        if settings.agentteams_matrix_access_token
        else None
    )
    resources: tuple[AsyncCloseable, ...] = (
        (control_plane, messenger) if messenger is not None else (control_plane,)
    )
    return ApplicationContainer(
        database=database,
        agent_directory=PostgresAgentDirectory(database),
        project_topology_store=PostgresProjectTopologyStore(database),
        repository_catalog=PostgresRepositoryCatalog(database),
        outbox_store=OutboxStore(database),
        mock_coding_agent_factory=lambda scenario: MockCodingAgent(MockScenario(scenario)),
        agent_team_control_plane=control_plane,
        agent_team_messenger=messenger,
        agentteams_probe=control_plane,
        agentteams_required=settings.agentteams_required,
        external_resources=resources,
    )


def create_app(container: ApplicationContainer | None = None) -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Multi-repository coding-agent orchestration infrastructure",
        lifespan=lifespan,
    )
    application.state.container = container or build_default_container()
    application.include_router(api_router)
    return application
