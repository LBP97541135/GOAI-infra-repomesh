from fastapi import FastAPI

from repomesh.api.router import api_router
from repomesh.bootstrap.container import ApplicationContainer
from repomesh.integrations.coding_agents.mock import MockCodingAgent, MockScenario
from repomesh.modules.repository_intelligence.infrastructure import InMemoryRepositoryCatalog
from repomesh.settings import get_settings


def build_default_container() -> ApplicationContainer:
    return ApplicationContainer(
        repository_catalog=InMemoryRepositoryCatalog(),
        mock_coding_agent_factory=lambda scenario: MockCodingAgent(MockScenario(scenario)),
    )


def create_app(container: ApplicationContainer | None = None) -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Multi-repository coding-agent orchestration infrastructure",
    )
    application.state.container = container or build_default_container()
    application.include_router(api_router)
    return application
