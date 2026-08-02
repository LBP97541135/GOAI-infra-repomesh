from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from repomesh.api.router import api_router
from repomesh.bootstrap.container import ApplicationContainer
from repomesh.integrations.coding_agents.mock import MockCodingAgent, MockScenario
from repomesh.modules.repository_intelligence.infrastructure import PostgresRepositoryCatalog
from repomesh.persistence import Database
from repomesh.persistence.outbox import OutboxStore
from repomesh.settings import get_settings


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    try:
        yield
    finally:
        await application.state.container.database.dispose()


def build_default_container() -> ApplicationContainer:
    settings = get_settings()
    database = Database(settings.database_url)
    return ApplicationContainer(
        database=database,
        repository_catalog=PostgresRepositoryCatalog(database),
        outbox_store=OutboxStore(database),
        mock_coding_agent_factory=lambda scenario: MockCodingAgent(MockScenario(scenario)),
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